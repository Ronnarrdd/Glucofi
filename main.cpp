/*

     download samples from a Roche accuchek device using libusb

     usage: see kUsage below, or run accuchek --help

     --set-time  set the meter clock to the PC clock when they differ by more
                 than kClockToleranceS and the PC clock is NTP synchronized
     --now       PC clock to assume while replaying, taken as synchronized

     stdout: a JSON object (format 2, see schema/output.schema.json),
             written only once the download succeeded
     stderr: "accuchek: <reason>" on failure, logs when ACCUCHEK_DBG is set
     exit codes: see ExitCode in session.h

     compile with: make

 */

#include <log.h>
#include <usb.h>
#include <merge.h>
#include <trace.h>
#include <output.h>
#include <session.h>
#include <protocol.h>
#include <string>
#include <vector>
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <unistd.h>
#include <stdarg.h>
#include <sys/stat.h>
#include <memory>
#include <algorithm>

using namespace accuchek;

// reason the program stops, reported on stderr with its exit code
struct Fatal {
    ExitCode code;
    std::string msg;
};

[[noreturn]] static void die(
    ExitCode code,
    const char *format,
    ...
) __attribute__((format(printf, 2, 3)));

[[noreturn]] static void die(
    ExitCode code,
    const char *format,
    ...
) {
    char msg[1024];
    va_list arg;
    va_start(arg, format);
    vsnprintf(msg, sizeof(msg), format, arg);
    va_end(arg);
    LOG_WRN("%s -- giving up", msg);
    throw Fatal{code, msg};
}

// what the command line asks for
struct Args {
    int deviceIndex = -1;
    const char *capturePath = 0;
    const char *replayPath = 0;
    const char *configPath = 0;
    const char *nowText = 0;
    const char *mergePath = 0;
    const char *waitText = 0;
    long waitSeconds = 0;       // 0: the meter must already be on the bus
    bool listDevices = false;
    bool csv = false;
    SessionOptions options;
};

// one download: the samples are kept in memory until it succeeds
struct Download {
    SessionReport report;
    std::vector<Sample> samples;
};

// run the protocol, keep samples in memory until it succeeds
static void runSession(
    Transport &transport,
    const SessionOptions &options,
    Download &download
) {
    try {
        downloadSamples(transport, options, download.report, [&](const Sample &s) { download.samples.push_back(s); });
    } catch(const SessionError &e) {
        auto received = download.report.glucose.received;
        download.samples.clear();
        if(0<received) {
            die(e.code, "%s (%d samples received before the error, none written)", e.what(), (int)received);
        }
        die(e.code, "%s", e.what());
    }
}

// talk to the meter over transport, recording the exchange when asked to
static void runRecorded(
    Transport &transport,
    const Args &args,
    Download &download
) {
    if(0==args.capturePath) {
        runSession(transport, args.options, download);
        return;
    }
    auto fp = fopen(args.capturePath, "w");
    if(0==fp) {
        die(kExitUsage, "cannot write trace %s", args.capturePath);
    }
    // keep the trace of a failed download too, that is when it is most useful
    auto closeTrace = [&]() {
        auto failed = (0!=ferror(fp));
        failed = (0!=fclose(fp)) || failed;
        if(failed) {
            fprintf(stderr, "accuchek: warning: cannot write trace %s, it is incomplete\n", args.capturePath);
        }
    };
    RecordingTransport recording(transport, fp);
    try {
        runSession(recording, args.options, download);
    } catch(const Fatal &) {
        closeTrace();
        throw;
    }
    closeTrace();
}

// find the selected meter on the bus and download from it
static void downloadFromMeter(
    const Config &config,
    const Args &args,
    Download &download
) {
    UsbContext usb;
    MeterScan scan;
    auto index = std::max(0, args.deviceIndex);
    auto found = [&]() {
        scan = scanMeters(usb.context, config);
        return index<int(scan.meters.size());
    };
    if(0<args.waitSeconds) {
        if(isatty(STDERR_FILENO)) {
            fprintf(stderr, "accuchek: waiting up to %ld s for the meter, plug it in\n", args.waitSeconds);
        }
        // a meter just plugged in is refused until udev gives access: keep looking
        waitFor(found, args.waitSeconds * 1000, 500, monotonicMs, sleepMs);
    } else {
        found();
    }
    auto waited = (0<args.waitSeconds ? " after waiting " + std::to_string(args.waitSeconds) + " s" : std::string());
    if(scan.meters.empty()) {
        if(!scan.accessDenied.empty()) {
            die(kExitAccessDenied, "permission denied on USB meter %s", scan.accessDenied.c_str());
        }
        die(kExitNoDevice, "no Accu-Chek meter found on the USB bus%s", waited.c_str());
    }
    if(int(scan.meters.size())<=index) {
        die(kExitNoDevice, "meter #%d selected but only %d found%s", index, (int)scan.meters.size(), waited.c_str());
    }
    withMeter(scan.meters[index], [&](Transport &transport) { runRecorded(transport, args, download); });
}

// replay a recorded trace instead of talking to a device
static void replayTrace(
    const Args &args,
    Download &download
) {
    std::string text;
    if(false==readFile(args.replayPath, text)) {
        die(kExitUsage, "cannot read trace %s", args.replayPath);
    }
    std::unique_ptr<ReplayTransport> replay;
    try {
        replay.reset(new ReplayTransport(text));
    } catch(const std::runtime_error &e) {
        die(kExitUsage, "bad trace %s: %s", args.replayPath, e.what());
    }
    runSession(*replay, args.options, download);
}

// the archive of --merge, checked before talking to the meter
static Archive loadArchive(
    const char *path
) {
    // "accuchek --merge a.json > a.json": the shell has already emptied it
    struct stat out;
    struct stat in;
    if(0==fstat(STDOUT_FILENO, &out) && 0==stat(path, &in) && out.st_dev==in.st_dev && out.st_ino==in.st_ino) {
        die(kExitUsage, "stdout is the archive %s itself, emptied by the shell before accuchek started: "
            "write to another file, then rename it", path);
    }
    std::string text;
    if(false==readFile(path, text)) {
        die(kExitUsage, "cannot read archive %s", path);
    }
    Archive archive;
    std::string error;
    if(!parseArchive(text, archive, error)) {
        die(kExitUsage, "bad archive %s: %s", path, error.c_str());
    }
    return archive;
}

// the archive readings the meter no longer holds, then the download
static void mergeArchive(
    const char *path,
    const Archive &archive,
    Download &download
) {
    const auto &serial = download.report.meter.serial;
    if(!archive.serial.empty() && download.report.hasMeter && !serial.empty() && archive.serial!=serial) {
        die(kExitUsage, "archive %s holds the readings of meter %s, this is meter %s: keep one archive per meter",
            path, archive.serial.c_str(), serial.c_str());
    }
    std::vector<Sample> merged;
    auto kept = mergeSamples(archive.samples, download.samples, merged);
    LOG_NFO("merge: %d archive readings kept, %d downloaded", (int)kept, (int)download.samples.size());
    download.samples.swap(merged);
}

// set by the Makefile from git describe or the VERSION file
#ifndef ACCUCHEK_VERSION
#define ACCUCHEK_VERSION "unknown"
#endif

static const char kUsage[] =
    "usage: accuchek [DEVICE_INDEX] [--config FILE] [--wait SECONDS] [--set-time] [--capture TRACE]\n"
    "                [--merge ARCHIVE] [--csv]\n"
    "       accuchek --replay TRACE [--set-time --now \"YYYY/MM/DD HH:MM:SS\"] [--merge ARCHIVE] [--csv]\n"
    "       accuchek [--config FILE] --known-devices\n"
    "       accuchek --help | --version\n"
    "\n"
    "Download every reading from a Roche Accu-Chek meter over USB and print\n"
    "them as one JSON object on stdout.\n"
    "\n"
    "  DEVICE_INDEX      read the Nth known meter on the bus (default: the first)\n"
    "  --wait SECONDS    wait up to SECONDS (1 to 3600) for the meter to be plugged in\n"
    "  --csv             one CSV line per reading instead of the JSON object\n"
    "  --set-time        set the meter clock to the PC clock if they differ by\n"
    "                    more than 60 s and the PC clock is NTP synchronized\n"
    "  --capture TRACE   also record the USB exchange to TRACE (health data!)\n"
    "  --replay TRACE    replay a recorded exchange instead of talking to a meter\n"
    "  --now TIME        PC clock to assume while replaying\n"
    "  --merge ARCHIVE   also output the readings of ARCHIVE (an earlier output)\n"
    "                    that the meter no longer holds, without duplicates;\n"
    "                    write to another file, then rename it over ARCHIVE\n"
    "  --config FILE     add or disable meter models (see config.example.txt)\n"
    "  --known-devices   list accepted meters as vendor:product\n"
    "\n"
    "Exit codes: 0 ok, 1 usage, 2 no meter, 3 access denied (udev rule missing),\n"
    "4 USB transfer failed, 5 protocol error, 6 stdout not writable.\n"
    "Set ACCUCHEK_DBG=1 for logs on stderr.\n";

// a plain decimal number in [0, max], refused otherwise (atoi read "foo" as 0)
static bool parseCount(
    const char *text,
    long max,
    long &n
) {
    char *end = 0;
    errno = 0;
    n = strtol(text, &end, 10);
    return isdigit((unsigned char)text[0]) && 0==*end && 0==errno && n<=max;
}

// PC clock given by --now, a real local time
static time_t parseNow(
    const char *nowText
) {
    struct tm t;
    memset(&t, 0, sizeof(t));
    int consumed = 0;
    if(6!=sscanf(nowText, "%d/%d/%d %d:%d:%d%n", &t.tm_year, &t.tm_mon, &t.tm_mday, &t.tm_hour, &t.tm_min, &t.tm_sec, &consumed) ||
        0!=nowText[consumed]) {
        die(kExitUsage, "bad --now %s, expected \"YYYY/MM/DD HH:MM:SS\"", nowText);
    }
    t.tm_year -= 1900;
    t.tm_mon -= 1;
    t.tm_isdst = -1;
    auto asked = t;
    auto now = mktime(&t);
    // mktime normalizes 2026/13/45 into 2027/02/14 and shifts times in the
    // spring forward gap: a date that does not come back unchanged is wrong
    if(asked.tm_year!=t.tm_year || asked.tm_mon!=t.tm_mon || asked.tm_mday!=t.tm_mday ||
        asked.tm_hour!=t.tm_hour || asked.tm_min!=t.tm_min || asked.tm_sec!=t.tm_sec) {
        die(kExitUsage, "bad --now %s, no such local time", nowText);
    }
    return now;
}

// returns the text to print instead of downloading (--help...), "" otherwise
static std::string parseArgs(
    int argc,
    char *argv[],
    Args &args
) {
    // an option given twice is a typo or a script bug, never "last one wins"
    auto value = [&](int &i, const char *&slot) {
        if(0!=slot) {
            die(kExitUsage, "%s given twice", argv[i]);
        }
        slot = argv[++i];
    };
    for(int i=1; i<argc; ++i) {
        if(0==strcmp(argv[i], "--help") || 0==strcmp(argv[i], "-h")) {
            return kUsage;
        } else if(0==strcmp(argv[i], "--version")) {
            return std::string("accuchek ") + ACCUCHEK_VERSION + "\n";
        } else if(0==strcmp(argv[i], "--config") && i+1<argc) {
            value(i, args.configPath);
        } else if(0==strcmp(argv[i], "--set-time")) {
            args.options.setTime = true;
        } else if(0==strcmp(argv[i], "--now") && i+1<argc) {
            value(i, args.nowText);
        } else if(0==strcmp(argv[i], "--known-devices")) {
            args.listDevices = true;
        } else if(0==strcmp(argv[i], "--capture") && i+1<argc) {
            value(i, args.capturePath);
        } else if(0==strcmp(argv[i], "--replay") && i+1<argc) {
            value(i, args.replayPath);
        } else if(0==strcmp(argv[i], "--merge") && i+1<argc) {
            value(i, args.mergePath);
        } else if(0==strcmp(argv[i], "--wait") && i+1<argc) {
            value(i, args.waitText);
        } else if(0==strcmp(argv[i], "--csv")) {
            args.csv = true;
        } else if('-'!=argv[i][0]) {
            long n = 0;
            if(!parseCount(argv[i], 9999, n)) {
                die(kExitUsage, "bad DEVICE_INDEX %s, expected 0, 1, 2... (see accuchek --help)", argv[i]);
            }
            if(0<=args.deviceIndex) {
                die(kExitUsage, "DEVICE_INDEX given twice");
            }
            args.deviceIndex = int(n);
        } else {
            die(kExitUsage, "unknown or incomplete option %s (see accuchek --help)", argv[i]);
        }
    }
    if(0!=args.replayPath && 0!=args.capturePath) {
        die(kExitUsage, "--capture records a meter, it does not go with --replay");
    }
    if(0!=args.replayPath && 0<=args.deviceIndex) {
        die(kExitUsage, "DEVICE_INDEX selects a meter, it does not go with --replay");
    }
    // a fake clock must never reach a real meter
    if(0!=args.nowText && 0==args.replayPath) {
        die(kExitUsage, "--now only goes with --replay");
    }
    if(0!=args.waitText) {
        if(!parseCount(args.waitText, 3600, args.waitSeconds) || args.waitSeconds<1) {
            die(kExitUsage, "bad --wait %s, expected seconds from 1 to 3600", args.waitText);
        }
        if(0!=args.replayPath) {
            die(kExitUsage, "--wait waits for a meter, it does not go with --replay");
        }
    }
    return "";
}

// everything but writing on stdout: returns what to write, throws Fatal on failure
static std::string run(
    int argc,
    char *argv[]
) {
    Args args;
    auto text = parseArgs(argc, argv, args);
    if(!text.empty()) {
        return text;
    }

    auto config = defaultConfig();
    if(0!=args.configPath) {
        std::string file;
        if(false==readFile(args.configPath, file)) {
            die(kExitUsage, "cannot read config file %s", args.configPath);
        }
        config = configWithFile(file);
    }
    // replaying a trace: the PC clock of the capture is unknown unless given
    if(0==args.replayPath) {
        args.options.pcClock = systemClock;
    }
    if(0!=args.nowText) {
        auto now = parseNow(args.nowText);
        args.options.pcClock = [now]() { return PcClock{now, true, true}; };
    }
    if(args.listDevices) {
        std::string list;
        for(const auto &device : allowedDevices(config)) {
            list += device + "\n";
        }
        return list;
    }

    // a bad archive is refused before the meter is read
    Archive archive;
    if(0!=args.mergePath) {
        archive = loadArchive(args.mergePath);
    }

    LOG_NFO("starting");
    Download download;
    if(0!=args.replayPath) {
        replayTrace(args, download);
    } else {
        try {
            downloadFromMeter(config, args, download);
        } catch(const UsbError &e) {
            die(e.code, "%s", e.what());
        }
    }
    for(const auto &warning : countWarnings(download.report)) {
        fprintf(stderr, "accuchek: warning: %s\n", warning.c_str());
    }
    if(0!=args.mergePath) {
        mergeArchive(args.mergePath, archive, download);
    }
    if(args.csv) {
        return outputCsv(download.samples);
    }
    return outputJson(download.report, download.samples);
}

// a full disk or a closed pipe must not end in exit code 0
static void writeStdout(
    const std::string &text
) {
    auto written = fwrite(text.data(), 1, text.size(), stdout);
    auto flushed = (0==fflush(stdout));
    auto error = errno;
    if(written!=text.size() || !flushed || ferror(stdout)) {
        die(kExitOutput, "cannot write on stdout: %s", strerror(error));
    }
    if(0!=fclose(stdout)) {
        die(kExitOutput, "cannot write on stdout: %s", strerror(errno));
    }
}

// entry point
int main(
    int argc,
    char *argv[]
) {
    // be silent unless asked to talk (on stderr)
    gQuiet = (0==getenv("ACCUCHEK_DBG"));
    try {
        // a closed fd 1 would be reused by the next open, a --capture trace
        // would then receive the JSON: refuse before talking to the meter
        if(fcntl(STDOUT_FILENO, F_GETFD)<0) {
            die(kExitOutput, "stdout is closed, nowhere to write the readings");
        }
        writeStdout(run(argc, argv));
    } catch(const Fatal &f) {
        fprintf(stderr, "accuchek: %s\n", f.msg.c_str());
        return f.code;
    }
    LOG_NFO("done");
    return kExitOk;
}
