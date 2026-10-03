# accuchek

Download every blood glucose reading from a Roche Accu-Chek meter over USB on Linux, and print them as one JSON object.

```sh
$ accuchek > readings.json
$ jq '.readings[-1]' readings.json
{"id": 637, "epoch": 1790880420, "timestamp": "2026/10/01 20:47", "mg/dL": 104, "mmol/L": 5.8, "status": 0, "meal": "fasting", "key": "2026100120471300"}
```

No cloud, no vendor app, no browser: a small C++17 program on top of libusb, without root.

This is a fork of [emogenet/accuchek](https://github.com/emogenet/accuchek). It adds:

- meal markers (fasting, before / after meal, bedtime...), meter identity (serial, firmware) and meter clock
- `--set-time` to set the meter clock from an NTP synchronized PC
- HI / LO off-scale readings kept instead of dropped, summer time epochs fixed
- bounds checks on every packet from the meter, fuzzed against guard pages
- distinct exit codes, all-or-nothing JSON, no root (udev `uaccess` rule)
- tests that replay recorded USB exchanges, and a JSON schema for the output

## Supported meters

| USB id | Meter |
| --- | --- |
| `173a:21d5` | Accu-Chek Guide (model 925), tested |
| `173a:21d7` | similar Roche model, untested |
| `173a:21d8` | Relion Platinum (model 982) |

The protocol is ISO/IEEE 11073-20601 (Personal Health Devices), as in the [Tidepool uploader driver](https://github.com/tidepool-org/uploader/tree/master/lib/drivers/roche). Another meter speaking it may work: try it with `--config` (see below) and open an issue or a PR with the result.

## Install

Dependencies:

| Distribution | Packages |
| --- | --- |
| Debian, Ubuntu | `g++ make libusb-1.0-0-dev` |
| Fedora | `gcc-c++ make libusb1-devel` |
| Arch | `gcc make libusb` |
| Mageia | `gcc-c++ make lib64usb1.0-devel` |

```sh
make
sudo make install          # /usr/local/bin/accuchek and /etc/udev/rules.d/70-accuchek.rules
sudo udevadm control --reload
sudo udevadm trigger --subsystem-match=usb
```

The udev rule gives the user of the active local session access to the meter (`TAG+="uaccess"`), so `accuchek` never needs root. `make install` honors `PREFIX`, `DESTDIR` and `UDEVDIR` (packagers: `UDEVDIR=/usr/lib/udev/rules.d`). The default build is portable; `make OPTFLAGS="-O3 -march=native"` tunes it to the build machine.

## Usage

Plug the meter in. It shows "data transfer", then:

```sh
accuchek > readings.json                        # first known meter on the bus
accuchek 1 > readings.json                      # second one, if several are plugged in
accuchek --wait 60 > readings.json              # start first, plug the meter in within 60 s
accuchek --csv > readings.csv                   # one line per reading, for a spreadsheet
accuchek --set-time > readings.json             # also set the meter clock if it is off by more than 60 s
accuchek --capture session.trace > readings.json   # also record the USB exchange
accuchek --replay session.trace > readings.json    # replay a recording, no meter needed
accuchek --merge archive.json > archive.new && mv archive.new archive.json   # keep the readings the meter drops
accuchek --known-devices                        # accepted meters, as vendor:product
accuchek --config my-meters.txt                 # add or disable models (format: config.example.txt)
accuchek --help
```

Ambiguous command lines are refused with exit code 1 rather than guessed: a `DEVICE_INDEX` that is not a plain number, an option given twice, `--capture`, `--wait` or `DEVICE_INDEX` with `--replay`, a `--wait` outside 1 to 3600 seconds, a `--now` that is not a real local time (2026/02/30, or 02:30 on the night clocks spring forward).

`--wait SECONDS` looks for the meter every half second until it is on the bus and readable: a meter just plugged in is refused for a moment, until udev grants access. Past the delay, the usual exit code 2 (no meter) or 3 (access denied).

`--set-time` only writes the clock when the meter declares it settable and the PC clock is NTP synchronized (`adjtimex` without `TIME_ERROR`). A meter that refuses does not stop the download (`"action": "rejected"`).

### Keeping more than the meter holds

A Guide keeps its last 720 readings. `--merge ARCHIVE` reads an earlier output of accuchek and writes the download preceded by the archive readings the meter no longer holds, without duplicates. The output is a normal format 2 object: `meter`, `clock`, `glucose` and `meal` describe this download, `readings` the whole history, `id` numbered again from 0.

- Same reading: same `key`, `mg/dL` and `status`. Archives written before 2.2 have no `key`: a reading is then the download reading of the same minute, `mg/dL` and `status`, matched one to one, and is written back with the meter's key.
- The archive is checked before the meter is read. Exit code 1, with nothing written, when it is not an accuchek output, when a reading is inconsistent (a `key` that is not the time of its `timestamp`, `"range": "high"` without 601 mg/dL...), or when it comes from another meter (serial numbers differ).
- Write to another file, then rename it: with `accuchek --merge a.json > a.json` the shell empties `a.json` before accuchek starts. accuchek detects it and refuses, but the archive is already gone.
- `epoch`, `id` and `mmol/L` are computed again; unknown fields of the archive are dropped.

A trace holds all your readings: it is health data. Keep it out of public places; the `.gitignore` ignores `*.trace` outside `tests/fixtures/`.

## Output

One JSON object, written only after the whole download succeeded. Formal definition: [`schema/output.schema.json`](schema/output.schema.json).

```json
{
  "format": 2,
  "meter": {"manufacturer": "Roche", "model": "925", "serial": "...", "firmware": "v1.9.6", "hardware": "G", "software": "", "system_id": "..."},
  "clock": {"meter": "2026/10/01 21:06:58", "pc": "2026/10/01 20:42:52", "offset_s": 1446, "settable": true, "pc_synchronized": true, "action": "set"},
  "glucose": {"announced": 638, "received": 638},
  "meal": {"announced": 576, "received": 576, "unmatched": 0},
  "readings": [
    {"id": 0, "epoch": 1617009120, "timestamp": "2021/03/29 11:12", "mg/dL": 133, "mmol/L": 7.4, "status": 0, "meal": "before_meal", "key": "2021032911120700"}
  ]
}
```

- `glucose.announced` is the count the meter gives for its glucose segment, `received` the readings actually downloaded (same for `meal`). When they differ, the download still succeeds (exit code 0) and stderr gets `accuchek: warning: the meter announced N readings, M received`.
- `timestamp` is the meter time. `epoch` is that time read in the PC time zone, summer time included.
- `id` is the position of the reading in the output. A Guide keeps its last 720 readings: once full, each new reading drops the oldest one and every `id` shifts by one. To recognize a reading from one download to the next, use `key` (the raw meter time, seconds included, `2026100120471300` for 2026/10/01 20:47:13) with `mg/dL` and `status`, or let `--merge` do it.
- `mmol/L` is `mg/dL / 18` with one decimal, as a meter set to mmol/L shows it. `mg/dL` is the value the meter stores.
- Every reading is written, whatever its `status` (raw value from the meter, 0 for a normal reading).
- Off-scale readings get `"range": "high"` with 601 mg/dL (HI) or `"range": "low"` with 9 mg/dL (LO), as in the Tidepool driver.
- A reading with an invalid date gets `"error": "invalid date"`, with `epoch` and `timestamp` set to null.
- `meal` (in a reading) is one of `fasting`, `before_meal`, `after_meal`, `casual`, `bedtime`, `other`, and is absent without a marker. Each marker is attached to the reading of the same second.
- `meter` and `clock` are null when the meter does not provide them, and the top-level `meal` is null when it has no marker segment.
- `clock` is read at the start of the session, before `--set-time`. Possible actions: `not_requested`, `set`, `within_tolerance`, `not_settable`, `pc_not_synchronized`, `pc_unknown`, `unknown`, `rejected`.
- In a replay without `--now`, `pc`, `offset_s` and `pc_synchronized` are null, so replaying a trace always gives the same output.

`--csv` writes the readings only, with the same fields, a null or absent field left empty:

```
id,key,epoch,timestamp,mg/dL,mmol/L,status,range,meal,error
0,2026090108000000,1788242400,2026/09/01 08:00,120,6.7,0,,fasting,
1,2026090112000000,1788256800,2026/09/01 12:00,601,33.4,0,high,,
```

## Exit codes

On failure stdout stays empty (never a partial download) and stderr holds one line, `accuchek: <reason>`. The one exception is code 6: stdout itself failed while the JSON was being written, so whatever reached it is incomplete.

| Code | Meaning |
| --- | --- |
| 0 | success, including an empty meter (`"readings": []`) |
| 1 | usage: unknown option, unreadable config file or trace, bad `--merge` archive |
| 2 | no known meter on the USB bus |
| 3 | meter found but access denied: the udev rule is missing or not loaded |
| 4 | USB transfer failed: timeout, meter unplugged |
| 5 | protocol: the meter aborted the association or answered something unexpected |
| 6 | output: stdout closed or not writable (disk full, broken pipe); what reached it is incomplete, throw it away |

## Troubleshooting

- Exit code 3: install the udev rule (see Install), then unplug and replug the meter.
- Exit code 4 or 5: unplug the meter, wait until its screen is off, plug it in again and wait for "data transfer".
- `ACCUCHEK_DBG=1 accuchek` prints logs and hex dumps of every packet on stderr; stdout does not change.
- When reporting a bug, `--capture` records the exchange. Remove your readings before sharing it, or describe the problem from the logs.

## Development

```sh
make test          # unit and session tests, with ASan / UBSan when available
make schema-check  # replay every fixture, validate the JSON (pip install jsonschema)
make fuzz          # 200 000 mutated packets against guard pages and mutated archives, 0 crash expected
make eval-merge    # --merge on real outputs, oldest first (ARCHIVES="a.json b.json"; default: Glucofi's raw copies)
make coverage      # line coverage of the tests, binary included, fails under 88% (pip install gcovr)
make cppcheck      # static analysis, fails on any finding (pip install cppcheck)
make CXX=clang++   # any target, built with clang
make hooks         # once per clone: make test (warnings as errors) before every commit
```

The code builds without a single warning under `-Wall -Wextra -Wshadow`, with GCC and clang. CI builds both with `WERROR=-Werror`, runs the command line tests against a binary built with ASan and UBSan, and runs coverage and cppcheck. The lines coverage leaves out are mostly the libusb calls of `usb.cpp`: only a real meter reaches them, their logic is tested through `DeviceOps` in `tests/test_usb.cpp`.

| File | Role |
| --- | --- |
| `protocol.h/.cpp` | ISO/IEEE 11073 constants, outgoing messages (clock setting included), decoding of incoming messages, JSON. No I/O. |
| `session.h/.cpp` | Full download sequence over an abstract `Transport`. Failures throw `SessionError`. |
| `trace.h/.cpp` | Recorded USB exchanges: `RecordingTransport` (`--capture`) and `ReplayTransport` (`--replay`). |
| `output.h/.cpp` | The JSON object of a download (`outputJson`), built in memory. No I/O. |
| `usb.h/.cpp` | libusb: finding known meters (`scanMeters`), the open sequence (`ClaimedMeter` over `DeviceOps`, faked in tests), `LibusbTransport`. The active configuration is never set again: on a configured device that is a lightweight reset. |
| `log.h/.cpp` | One line per log on stderr, only with `ACCUCHEK_DBG`. |
| `json.h/.cpp` | Strict JSON reader (RFC 8259) for `--merge` archives: no comments, trailing commas, duplicate keys, nesting past 64. Written here to keep libusb the only dependency. |
| `merge.h/.cpp` | `--merge`: reading an archive back into samples, checked field by field, and merging it with a download. |
| `main.cpp` | Command line, choosing the meter, writing the JSON on stdout. |
| `tests/sim.h` | Meter simulator building packets with the Tidepool driver layout. |
| `tests/fixtures/` | `*.trace`: simulated sessions; `guide925_*.hex`: answers from a real Guide 925, serial number, system id and dates replaced. |

Every decoder takes the number of bytes actually received and reads through `Reader`, which refuses to go past the end: a truncated or inconsistent packet stops the download with a precise message. A segment whose last message never comes stops after `kMaxDataMessages` (4096) messages, exit code 5, instead of looping forever. The fuzzer puts each mutated packet right before a protected memory page, so reading a single byte too far crashes even without AddressSanitizer. On a crash it prints the command that replays that iteration (`FUZZ_ARGS="--seed S --from N --count 1" make fuzz`).

`accuchek --version` comes from `git describe` in a clone (`2.1.0-3-gabcdef0` three commits after `v2.1.0`), from the `VERSION` file in a copy of the sources. To release: bump `VERSION`, commit, `git tag vX.Y.Z`, push the tag. `make test` fails when the tag is ahead of `VERSION`.

After changing the simulator, regenerate the fixtures with `ACCUCHEK_UPDATE_FIXTURES=1 make test` and review the diff.

## Credits and license

Original program by [emogenet](https://github.com/emogenet/accuchek), protocol reverse-engineered from the [Tidepool uploader](https://github.com/tidepool-org/uploader). Public domain ([Unlicense](LICENSE.txt)).

This program is not affiliated with Roche. It is not a medical device: check important values on the meter itself.
