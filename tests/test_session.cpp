#include "check.h"
#include "sim.h"
#include <session.h>
#include <trace.h>
#include <usb.h>
#include <algorithm>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/wait.h>

using namespace accuchek;

static const std::vector<std::vector<sim::Record>> kTwoSegments = {
    {
        {2021, 1, 15, 8, 0, 133, 0},
        {2021, 1, 15, 12, 30, 192, 0},
    },
    {
        {2021, 1, 16, 7, 45, 98, 0},
    },
};

static const std::vector<std::vector<sim::Record>> kNoSegments;

// trace line indexes in sim::sessionTrace (line 0 is the comment)
enum {
    kLinePairingConfirm = 3,
    kLineMdsAnswer = 7,
    kLineSegmentHeaders = 11,
    kLineFirstSegment = 12,
};

static std::string replaceLine(
    const std::string &trace,
    int index,
    const std::string &replacement
) {
    std::string out;
    size_t start = 0;
    for(int i=0; start<trace.size(); ++i) {
        auto nl = trace.find('\n', start);
        auto line = trace.substr(start, nl - start + 1);
        out += (i==index ? replacement + "\n" : line);
        start = nl + 1;
    }
    return out;
}

static std::vector<Sample> download(
    const std::string &trace,
    ReplayTransport **leftover = 0
) {
    static ReplayTransport *last = 0;
    delete last;
    last = new ReplayTransport(trace);
    if(leftover) {
        *leftover = last;
    }
    std::vector<Sample> samples;
    downloadSamples(*last, [&](const Sample &s) { samples.push_back(s); });
    return samples;
}

struct Failure {
    int code;
    std::string msg;
};

static Failure sessionFailure(
    const std::string &trace
) {
    try {
        download(trace);
    } catch(const SessionError &e) {
        return {e.code, e.what()};
    }
    return {kExitOk, ""};
}

static std::string sessionError(
    const std::string &trace
) {
    return sessionFailure(trace).msg;
}

static std::vector<Sample> downloadWith(
    const std::string &trace,
    const SessionOptions &options,
    SessionReport &report,
    ReplayTransport **leftover = 0
) {
    static ReplayTransport *last = 0;
    delete last;
    last = new ReplayTransport(trace);
    if(leftover) {
        *leftover = last;
    }
    std::vector<Sample> samples;
    downloadSamples(*last, options, report, [&](const Sample &s) { samples.push_back(s); });
    return samples;
}

static SessionOptions optionsAt(
    const sim::Session &s,
    bool synchronized = true
) {
    auto tm = sim::localTm(s.now);
    auto now = mktime(&tm);
    SessionOptions options;
    options.setTime = s.setTime;
    options.pcClock = [now, synchronized]() { return PcClock{now, true, synchronized}; };
    return options;
}

// one unmatched marker, markers split over two messages like the glucose
static sim::Session mealSession() {
    sim::Session s;
    s.glucose = {
        {{2026, 9, 30, 7, 31, 112, 0, 12}, {2026, 9, 30, 13, 2, 182, 0, 40}},
        {{2026, 9, 30, 21, 45, 151, 0, 3}, {2026, 10, 1, 7, 10, 98, 0, 59}},
    };
    s.meals = {
        {{2026, 9, 30, 7, 31, 12, kMDC_CTXT_GLU_MEAL_FASTING}, {2026, 9, 30, 13, 2, 40, kMDC_CTXT_GLU_MEAL_POSTPRANDIAL}},
        {{2026, 9, 30, 21, 45, 3, kMDC_CTXT_GLU_MEAL_BEDTIME}, {2026, 9, 29, 8, 0, 0, kMDC_CTXT_GLU_MEAL_PREPRANDIAL}},
    };
    return s;
}

TEST(session_reads_meal_markers_from_their_segment) {
    auto s = mealSession();
    SessionReport report;
    ReplayTransport *t = 0;
    auto samples = downloadWith(sim::sessionTrace(s), optionsAt(s), report, &t);
    CHECK(t->finished());
    CHECK_EQ(samples.size(), 4u);
    if(4!=samples.size()) {
        return;
    }
    CHECK_EQ(samples[0].meal, kMDC_CTXT_GLU_MEAL_FASTING);
    CHECK_EQ(samples[1].meal, kMDC_CTXT_GLU_MEAL_POSTPRANDIAL);
    CHECK_EQ(samples[2].meal, kMDC_CTXT_GLU_MEAL_BEDTIME);
    CHECK_EQ(samples[3].meal, 0);
    CHECK(report.hasMealSegment);
    CHECK(report.meal.announced);
    CHECK_EQ(report.meal.expected, 4u);
    CHECK_EQ(report.meal.received, 4u);
    CHECK_EQ(report.mealsUnmatched, 1u);
    CHECK(report.glucose.announced);
    CHECK_EQ(report.glucose.expected, 4u);
    CHECK_EQ(report.glucose.received, 4u);
}

TEST(session_reports_meter_identity_and_clock_offset) {
    sim::Session s;
    s.glucose = kTwoSegments;
    SessionReport report;
    downloadWith(sim::sessionTrace(s), optionsAt(s), report);
    CHECK(report.hasMeter);
    CHECK_EQ(report.meter.serial, std::string("92500000042"));
    CHECK_EQ(report.meter.firmware, std::string("v1.9.6"));
    CHECK(report.hasClockOffset);
    CHECK_EQ(report.clockOffsetS, 24L * 60 + 6);     // 21:06:58 on the meter at 20:42:52
    CHECK_EQ(report.clockAction, kClockNotRequested);
    // the Guide always lists its marker segment, empty when no marker was set
    CHECK(report.hasMealSegment);
    CHECK_EQ(report.meal.expected, 0u);
}

TEST(session_sets_a_clock_that_is_off) {
    sim::Session s;
    s.glucose = kTwoSegments;
    s.setTime = true;
    SessionReport report;
    ReplayTransport *t = 0;
    auto samples = downloadWith(sim::sessionTrace(s), optionsAt(s), report, &t);
    CHECK_EQ(report.clockAction, kClockSet);
    CHECK_EQ(samples.size(), 3u);
    CHECK(t->finished());
    CHECK(std::string::npos!=sim::sessionTrace(s).find("0C17000C2026100120425200"));
}

// a real capture with --set-time used to be impossible to replay: the PC time sent was nowhere
TEST(capture_records_the_pc_time_it_sent) {
    sim::Session s;
    s.glucose = kTwoSegments;
    s.setTime = true;
    ReplayTransport meter(sim::sessionTrace(s));
    char *text = 0;
    size_t size = 0;
    auto out = open_memstream(&text, &size);
    {
        RecordingTransport recording(meter, out);
        SessionReport report;
        downloadSamples(recording, optionsAt(s), report, [](const Sample &) {});
        CHECK_EQ(report.clockAction, kClockSet);
    }
    fclose(out);
    std::string captured(text, size);
    free(text);
    CHECK(std::string::npos!=captured.find("\n# args: " + sim::replayArgs(s) + "\n"));
    SessionReport replayed;
    ReplayTransport *t = 0;
    CHECK_EQ(downloadWith(captured, optionsAt(s), replayed, &t).size(), 3u);
    CHECK_EQ(replayed.clockAction, kClockSet);
    CHECK(t->finished());
}

TEST(capture_without_set_time_has_no_args) {
    sim::Session s;
    s.glucose = kTwoSegments;
    ReplayTransport meter(sim::sessionTrace(s));
    char *text = 0;
    size_t size = 0;
    auto out = open_memstream(&text, &size);
    {
        RecordingTransport recording(meter, out);
        SessionReport report;
        downloadSamples(recording, optionsAt(s), report, [](const Sample &) {});
    }
    fclose(out);
    CHECK(std::string::npos==std::string(text, size).find("# args:"));
    free(text);
}

static ClockAction clockActionFor(
    sim::Session s,
    bool synchronized = true
) {
    s.glucose = kTwoSegments;
    auto traced = s;
    traced.setTime = false;     // nothing is sent when the clock is left alone
    SessionReport report;
    ReplayTransport *t = 0;
    downloadWith(sim::sessionTrace(traced), optionsAt(s, synchronized), report, &t);
    CHECK(t->finished());
    return report.clockAction;
}

TEST(session_leaves_a_clock_within_tolerance) {
    sim::Session s;
    s.setTime = true;
    s.meter.clock = {2026, 10, 1, 20, 43, 52};      // 60 s ahead
    CHECK_EQ(clockActionFor(s), kClockWithinTolerance);
    s.meter.clock = {2026, 10, 1, 20, 41, 52};      // 60 s behind
    CHECK_EQ(clockActionFor(s), kClockWithinTolerance);
    s.meter.clock = {2026, 10, 1, 20, 43, 53};
    s.setTime = true;
    auto traced = s;
    traced.glucose = kTwoSegments;
    SessionReport report;
    ReplayTransport *t = 0;
    downloadWith(sim::sessionTrace(traced), optionsAt(traced), report, &t);
    CHECK_EQ(report.clockAction, kClockSet);
    CHECK(t->finished());
}

// a PC clock that drifts must never be copied to the meter
TEST(session_never_sets_the_clock_from_an_unsynchronized_pc) {
    sim::Session s;
    s.setTime = true;
    CHECK_EQ(clockActionFor(s, false), kClockPcNotSynchronized);
}

TEST(session_does_not_set_an_unsettable_clock) {
    sim::Session s;
    s.setTime = true;
    s.meter.settable = false;
    CHECK_EQ(clockActionFor(s), kClockNotSettable);
}

TEST(session_without_pc_clock_does_not_set_the_meter) {
    sim::Session s;
    s.glucose = kTwoSegments;
    SessionOptions options;
    options.setTime = true;
    SessionReport report;
    ReplayTransport *t = 0;
    downloadWith(sim::sessionTrace(s), options, report, &t);
    CHECK_EQ(report.clockAction, kClockPcUnknown);
    CHECK(!report.hasClockOffset);
    CHECK(t->finished());
}

TEST(session_keeps_samples_when_the_meter_refuses_the_time) {
    sim::Session s;
    s.glucose = kTwoSegments;
    s.setTime = true;
    s.setTimeAccepted = false;
    SessionReport report;
    ReplayTransport *t = 0;
    auto samples = downloadWith(sim::sessionTrace(s), optionsAt(s), report, &t);
    CHECK_EQ(report.clockAction, kClockRejected);
    CHECK_EQ(samples.size(), 3u);
    CHECK(t->finished());
}

// meters that describe neither themselves nor their segments: segment 0 only, as before
TEST(session_with_a_meter_that_describes_nothing) {
    sim::Session s;
    s.glucose = kTwoSegments;
    s.meals = mealSession().meals;
    s.describe = false;
    SessionReport report;
    ReplayTransport *t = 0;
    auto samples = downloadWith(sim::sessionTrace(s), optionsAt(s), report, &t);
    CHECK_EQ(samples.size(), 3u);
    CHECK(t->finished());
    CHECK(!report.hasMeter);
    CHECK(!report.hasMealSegment);
    CHECK(!report.glucose.announced);
    CHECK_EQ(report.glucose.received, 3u);
}

// all or nothing: markers missing for some samples would change the morning reading
TEST(session_meal_transfer_failure_fails_the_download) {
    auto s = mealSession();
    // two glucose segments and their ACKs, then the marker request and headers
    const int firstMarkerSegment = kLineFirstSegment + 4 + 2;
    auto f = sessionFailure(replaceLine(sim::sessionTrace(s), firstMarkerSegment, "< !-7"));
    CHECK_EQ(f.code, kExitTransfer);
    CHECK(std::string::npos!=f.msg.find("meal marker segment"));
}

TEST(session_downloads_all_segments) {
    ReplayTransport *t = 0;
    auto samples = download(sim::sessionTrace(kTwoSegments), &t);
    CHECK_EQ(samples.size(), 3u);
    CHECK_EQ(samples[0].value, 133);
    CHECK_EQ(samples[1].value, 192);
    CHECK_EQ(samples[2].value, 98);
    CHECK_EQ(samples[2].day, 16);
    CHECK(t->finished());
}

TEST(session_with_many_segments) {
    std::vector<std::vector<sim::Record>> segments;
    for(int k=0; k<10; ++k) {
        segments.push_back({{2024, 1, 1 + k, 8, 0, uint16_t(100 + k), 0}});
    }
    ReplayTransport *t = 0;
    auto samples = download(sim::sessionTrace(segments), &t);
    CHECK_EQ(samples.size(), 10u);
    CHECK_EQ(samples[9].value, 109);
    CHECK(t->finished());
}

// a meter that answers every ACK with one more segment, never flagged last;
// the download used to loop forever, eating memory
struct EndlessMeter : Transport {
    ReplayTransport prefix;
    sim::Bytes segment = sim::dataSegment(0x20, 0x0100, 0, {{2026, 1, 1, 8, 0, 100, 0}}, true, false);
    size_t segmentsSent = 0;

    explicit EndlessMeter(const std::string &trace) : prefix(trace) {}

    int controlStatus(uint8_t *buffer, size_t len) override { return prefix.controlStatus(buffer, len); }
    int bulkOut(const uint8_t *buffer, size_t len) override {
        return prefix.finished() ? int(len) : prefix.bulkOut(buffer, len);
    }
    int bulkIn(uint8_t *buffer, size_t maxLen) override {
        if(!prefix.finished()) {
            return prefix.bulkIn(buffer, maxLen);
        }
        memcpy(buffer, segment.data(), segment.size());
        ++segmentsSent;
        return int(segment.size());
    }
    const char *errorName(int code) override { return prefix.errorName(code); }
};

// the session up to the glucose segment headers, the meter takes over after
static std::string untilSegmentHeaders() {
    auto full = sim::sessionTrace(kTwoSegments);
    size_t end = 0;
    for(int i=0; i<=kLineSegmentHeaders; ++i) {
        end = full.find('\n', end) + 1;
    }
    return full.substr(0, end);
}

TEST(session_gives_up_on_a_segment_that_never_ends) {
    EndlessMeter meter(untilSegmentHeaders());
    SessionReport report;
    std::vector<Sample> samples;
    try {
        downloadSamples(meter, SessionOptions(), report, [&](const Sample &s) { samples.push_back(s); });
        CHECK(false);
    } catch(const SessionError &e) {
        CHECK_EQ(e.code, kExitProtocol);
        CHECK_EQ(std::string(e.what()), "no last data segment after " + std::to_string(kMaxDataMessages) + " messages");
    }
    CHECK_EQ(meter.segmentsSent, kMaxDataMessages);
    CHECK(samples.empty());
}

// the limit counts messages, a meter flagging the last one right at the limit passes
TEST(session_data_message_limit_boundary) {
    std::vector<std::vector<sim::Record>> segments;
    for(int k=0; k<10; ++k) {
        segments.push_back({{2024, 1, 1 + k, 8, 0, uint16_t(100 + k), 0}});
    }
    SessionOptions options;
    options.maxDataMessages = 10;
    SessionReport report;
    CHECK_EQ(downloadWith(sim::sessionTrace(segments), options, report).size(), 10u);
    options.maxDataMessages = 9;
    try {
        downloadWith(sim::sessionTrace(segments), options, report);
        CHECK(false);
    } catch(const SessionError &e) {
        CHECK_EQ(std::string(e.what()), std::string("no last data segment after 9 messages"));
    }
}

TEST(session_association_abort) {
    auto trace = replaceLine(sim::sessionTrace(kTwoSegments), kLineMdsAnswer, "< E60000020000");
    auto f = sessionFailure(trace);
    CHECK_EQ(f.code, kExitProtocol);
    CHECK(std::string::npos!=f.msg.find("abort"));
}

// an empty meter used to end in an error, tidepool treats it as no data
TEST(session_empty_meter_is_not_an_error) {
    ReplayTransport *t = 0;
    auto samples = download(sim::sessionTrace(kNoSegments), &t);
    CHECK_EQ(samples.size(), 0u);
    CHECK(t->finished());
}

TEST(session_data_response_error) {
    auto headers = sim::segmentHeaders(0x0013, 0x0100, 2);
    auto trace = replaceLine(sim::sessionTrace(kTwoSegments), kLineSegmentHeaders, sim::line('<', headers));
    auto f = sessionFailure(trace);
    CHECK_EQ(f.code, kExitProtocol);
    CHECK(std::string::npos!=f.msg.find("code = 2"));
}

TEST(session_timeout) {
    auto trace = replaceLine(sim::sessionTrace(kTwoSegments), kLineFirstSegment, "< !-7");
    auto f = sessionFailure(trace);
    CHECK_EQ(f.code, kExitTransfer);
    CHECK(std::string::npos!=f.msg.find("timed out"));
}

TEST(session_meter_unplugged) {
    auto trace = replaceLine(sim::sessionTrace(kTwoSegments), kLineMdsAnswer, "< !-4");
    auto f = sessionFailure(trace);
    CHECK_EQ(f.code, kExitTransfer);
    CHECK(std::string::npos!=f.msg.find("MDS attribute answer"));
}

TEST(session_garbage_answer) {
    auto trace = replaceLine(sim::sessionTrace(kTwoSegments), kLineSegmentHeaders, "< E700000200");
    CHECK_EQ(sessionFailure(trace).code, kExitProtocol);
}

TEST(session_release_failure_keeps_samples) {
    auto full = sim::sessionTrace(kTwoSegments);
    auto lastLine = full.rfind("\n<") + 1;
    auto trace = full.substr(0, lastLine) + "< !-4\n";
    CHECK_EQ(sessionError(trace), std::string(""));
    CHECK_EQ(download(trace).size(), 3u);
    CHECK_EQ(download(full.substr(0, lastLine)).size(), 3u);
}

TEST(session_detects_unexpected_outgoing_message) {
    auto trace = replaceLine(sim::sessionTrace(kTwoSegments), kLinePairingConfirm, "> 00");
    CHECK(std::string::npos!=sessionError(trace).find("pairing confirmation"));
}

TEST(session_truncated_trace) {
    auto full = sim::sessionTrace(kTwoSegments);
    auto cut = full.substr(0, full.find("\n<", full.find("\n<", full.find("\n<") + 1) + 1));
    CHECK(!sessionError(cut).empty());
}

// a zero-length packet used to reach memcpy / memcmp with a null pointer (UBSan)
TEST(replay_empty_packets) {
    ReplayTransport replay("c\n<\n>\n");
    uint8_t buffer[4] = {1, 2, 3, 4};
    CHECK_EQ(replay.controlStatus(buffer, sizeof(buffer)), 0);
    CHECK_EQ(replay.bulkIn(buffer, sizeof(buffer)), 0);
    CHECK_EQ(replay.bulkOut(buffer, 0), 0);
    CHECK(replay.finished());
    CHECK_EQ(buffer[0], 1);
}

// checked-in copy of the simulated session, also validated against
// schema/output.schema.json by tests/check_schema.py; regenerate with: ACCUCHEK_UPDATE_FIXTURES=1 make test
// off scale readings and a reading with a non zero status
static const std::vector<std::vector<sim::Record>> kFlags = {
    {
        {2026, 9, 1, 8, 0, 120, 0},
        {2026, 9, 1, 12, 0, kValueHigh, 0},
        {2026, 9, 2, 3, 15, kValueLow, 0},
        {2026, 9, 2, 8, 0, 140, 0x0001},
    },
};

TEST(session_reports_every_sample_whatever_its_status) {
    auto samples = download(sim::sessionTrace(kFlags));
    CHECK_EQ(samples.size(), 4u);
    CHECK_EQ(samples[1].value, kValueHigh);
    CHECK_EQ(samples[2].value, kValueLow);
    CHECK_EQ(samples[3].status, 1);
}

static const std::vector<std::vector<sim::Record>> kSummerAndDst = {
    {
        {2026, 3, 29, 1, 59, 101, 0},
        {2026, 3, 29, 3, 0, 102, 0},
        {2026, 7, 2, 7, 36, 120, 0},
    },
    {
        {2026, 10, 25, 1, 59, 103, 0},
        {2026, 10, 25, 3, 0, 104, 0},
        {2026, 12, 24, 19, 0, 105, 0},
    },
};

static void checkFixture(
    const char *path,
    const std::string &expected
) {
    if(getenv("ACCUCHEK_UPDATE_FIXTURES")) {
        auto fp = fopen(path, "w");
        CHECK(0!=fp);
        if(fp) {
            fputs(expected.c_str(), fp);
            fclose(fp);
        }
    }
    std::string text;
    CHECK(readFile(path, text));
    CHECK_EQ(text, expected);
}

TEST(fixture_traces_match_simulator) {
    checkFixture("tests/fixtures/two_segments.trace", sim::sessionTrace(kTwoSegments));
    checkFixture("tests/fixtures/summer_and_dst.trace", sim::sessionTrace(kSummerAndDst));
    checkFixture("tests/fixtures/flags.trace", sim::sessionTrace(kFlags));
    checkFixture("tests/fixtures/empty_meter.trace", sim::sessionTrace(kNoSegments));
    checkFixture("tests/fixtures/meals.trace", sim::sessionTrace(mealSession()));
    sim::Session setTime;
    setTime.glucose = kTwoSegments;
    setTime.setTime = true;
    checkFixture("tests/fixtures/set_time.trace", sim::sessionTrace(setTime));
    sim::Session old;
    old.glucose = kTwoSegments;
    old.describe = false;
    checkFixture("tests/fixtures/undescribed_meter.trace", sim::sessionTrace(old));
}

// command line: run the real binary on a trace

struct CliResult {
    int code;
    std::string out;
    std::string err;
};

// run the binary with args from directory cwd (default: a fresh empty one)
static CliResult runBinary(
    const std::string &args,
    const std::string &cwd = "",
    const std::string &env = "env -u ACCUCHEK_DBG"
) {
    std::string dir = cwd;
    char tmpdir[] = "/tmp/accuchek-cwd-XXXXXX";
    if(dir.empty()) {
        CHECK(0!=mkdtemp(tmpdir));
        dir = tmpdir;
    }
    char errPath[] = "/tmp/accuchek-err-XXXXXX";
    close(mkstemp(errPath));
    auto bin = getenv("ACCUCHEK_BIN");
    auto cmd = "cd '" + dir + "' && " + env + " " + (bin ? bin : "./accuchek") + " " + args + " 2>" + errPath;
    auto pipe = popen(cmd.c_str(), "r");
    CliResult result = {-1, "", ""};
    char chunk[4096];
    size_t n;
    while(0<(n = fread(chunk, 1, sizeof(chunk), pipe))) {
        result.out.append(chunk, n);
    }
    auto status = pclose(pipe);
    result.code = WIFEXITED(status) ? WEXITSTATUS(status) : 128 + WTERMSIG(status);
    readFile(errPath, result.err);
    unlink(errPath);
    if(cwd.empty()) {
        rmdir(tmpdir);
    }
    return result;
}

static std::string writeTemp(
    const std::string &text
) {
    char path[] = "/tmp/accuchek-test-XXXXXX";
    auto fd = mkstemp(path);
    CHECK(0<=fd);
    CHECK_EQ(write(fd, text.data(), text.size()), (ssize_t)text.size());
    close(fd);
    return path;
}

static CliResult runCli(
    const std::string &trace,
    const std::string &args = "",
    const std::string &env = "env -u ACCUCHEK_DBG"
) {
    auto path = writeTemp(trace);
    auto result = runBinary("--replay " + path + " " + args, "", env);
    unlink(path.c_str());
    return result;
}

TEST(cli_known_devices_without_config_file) {
    auto r = runBinary("--known-devices");
    CHECK_EQ(r.code, 0);
    CHECK_EQ(r.out, std::string("173a:21d5\n173a:21d7\n173a:21d8\n"));
}

TEST(cli_ignores_config_txt_in_current_directory) {
    char dir[] = "/tmp/accuchek-cwd-XXXXXX";
    CHECK(0!=mkdtemp(dir));
    auto cfg = std::string(dir) + "/config.txt";
    auto fp = fopen(cfg.c_str(), "w");
    CHECK(0!=fp);
    if(!fp) {
        return;
    }
    fputs("vendor_0x173a_device_0x21d5 0\n", fp);
    fclose(fp);
    auto r = runBinary("--known-devices", dir);
    CHECK_EQ(r.out, std::string("173a:21d5\n173a:21d7\n173a:21d8\n"));
    auto withFile = runBinary("--config config.txt --known-devices", dir);
    CHECK_EQ(withFile.out, std::string("173a:21d7\n173a:21d8\n"));
    unlink(cfg.c_str());
    rmdir(dir);
}

TEST(cli_missing_config_file_is_an_error) {
    auto r = runBinary("--config /nonexistent/config.txt --known-devices");
    CHECK_EQ(r.code, kExitUsage);
    CHECK_EQ(r.out, std::string(""));
    CHECK_EQ(r.err, std::string("accuchek: cannot read config file /nonexistent/config.txt\n"));
}

static const char *kMeterJson =
    "  \"meter\": {\"manufacturer\":\"Roche\", \"model\":\"925\", \"serial\":\"92500000042\", "
    "\"firmware\":\"v1.9.6\", \"hardware\":\"G\", \"software\":\"\", \"system_id\":\"0060190000000042\"},\n";

// replayed without --now: the PC clock of the capture is unknown, stdout stays the same on every run
TEST(cli_outputs_json_object) {
    auto r = runCli(sim::sessionTrace(kTwoSegments));
    CHECK_EQ(r.code, 0);
    CHECK_EQ(r.err, std::string(""));
    auto head = std::string("{\n  \"format\": 2,\n") + kMeterJson +
        "  \"clock\": {\"meter\":\"2026/10/01 21:06:58\", \"pc\":null, \"offset_s\":null, \"settable\":true, \"pc_synchronized\":null, \"action\":\"not_requested\"},\n"
        "  \"glucose\": {\"announced\":3, \"received\":3},\n"
        "  \"meal\": {\"announced\":0, \"received\":0, \"unmatched\":0},\n"
        "  \"readings\": [\n    { \"id\":     0,";
    CHECK_EQ(r.out.substr(0, head.size()), head);
    CHECK(std::string::npos!=r.out.find("\"timestamp\":\"2021/01/16 07:45\", \"mg/dL\": 98"));
    // the key carries the seconds the timestamp drops
    CHECK(std::string::npos!=r.out.find("\"key\":\"2021011607450000\" }"));
    CHECK(std::string::npos!=r.out.find("\"id\":     2"));
    CHECK_EQ(r.out.substr(r.out.size() - 9), std::string(" }\n  ]\n}\n"));
}

TEST(cli_outputs_meal_markers) {
    auto r = runCli(sim::sessionTrace(mealSession()));
    CHECK_EQ(r.code, 0);
    CHECK(std::string::npos!=r.out.find("  \"glucose\": {\"announced\":4, \"received\":4},\n  \"meal\": {\"announced\":4, \"received\":4, \"unmatched\":1},\n"));
    CHECK(std::string::npos!=r.out.find("\"status\":0, \"meal\":\"fasting\", \"key\":\""));
    CHECK(std::string::npos!=r.out.find("\"status\":0, \"meal\":\"after_meal\", \"key\":\""));
    CHECK(std::string::npos!=r.out.find("\"status\":0, \"meal\":\"bedtime\", \"key\":\""));
    CHECK(std::string::npos!=r.out.find("\"mg/dL\": 98, \"mmol/L\": 5.4, \"status\":0, \"key\":\""));
}

TEST(cli_sets_the_meter_clock) {
    sim::Session s;
    s.glucose = kTwoSegments;
    s.setTime = true;
    auto r = runCli(sim::sessionTrace(s), sim::replayArgs(s));
    CHECK_EQ(r.code, 0);
    CHECK(std::string::npos!=r.out.find(
        "  \"clock\": {\"meter\":\"2026/10/01 21:06:58\", \"pc\":\"2026/10/01 20:42:52\", \"offset_s\":1446, \"settable\":true, \"pc_synchronized\":true, \"action\":\"set\"},\n"
    ));
}

// the trace holds the set time request: replaying it without the same PC clock must fail
TEST(cli_set_time_trace_needs_its_args) {
    sim::Session s;
    s.glucose = kTwoSegments;
    s.setTime = true;
    auto r = runCli(sim::sessionTrace(s));
    CHECK_EQ(r.code, kExitTransfer);
    CHECK_EQ(r.out, std::string(""));
    CHECK(std::string::npos!=r.err.find("trace expects E700001A00180013010700120000"));
}

TEST(cli_meter_that_describes_nothing) {
    sim::Session s;
    s.glucose = kTwoSegments;
    s.describe = false;
    auto r = runCli(sim::sessionTrace(s));
    CHECK_EQ(r.code, 0);
    CHECK(0==r.out.find(
        "{\n  \"format\": 2,\n  \"meter\": null,\n  \"clock\": null,\n"
        "  \"glucose\": {\"announced\":null, \"received\":3},\n  \"meal\": null,\n"
    ));
}

TEST(cli_now_needs_replay) {
    auto r = runBinary("--now \"2026/10/01 20:42:52\"");
    CHECK_EQ(r.code, kExitUsage);
    CHECK_EQ(r.err, std::string("accuchek: --now only goes with --replay\n"));
    auto bad = runCli(sim::sessionTrace(kTwoSegments), "--now 2026-10-01");
    CHECK_EQ(bad.code, kExitUsage);
    CHECK(0==bad.err.find("accuchek: bad --now"));
}

// mktime used to normalize any date: 2026/13/45 99:99:99 became 2027/02/18
TEST(cli_now_must_be_a_real_local_time) {
    auto trace = sim::sessionTrace(kTwoSegments);
    for(const char *now : {"2026/13/45 99:99:99", "2026/02/30 12:00:00", "2026/03/29 02:30:00"}) {
        auto r = runCli(trace, std::string("--now \"") + now + "\"");
        CHECK_EQ(r.code, kExitUsage);
        CHECK_EQ(r.err, std::string("accuchek: bad --now ") + now + ", no such local time\n");
        CHECK_EQ(r.out, std::string(""));
    }
    auto trailing = runCli(trace, "--now \"2026/10/01 20:42:52x\"");
    CHECK_EQ(trailing.code, kExitUsage);
    CHECK(0==trailing.err.find("accuchek: bad --now 2026/10/01 20:42:52x, expected"));
    // both sides of the autumn change exist, the first one is taken
    CHECK_EQ(runCli(trace, "--now \"2026/10/25 02:30:00\"").code, kExitOk);
}

// atoi used to read "foo" as meter #0, and options given twice kept the last one
TEST(cli_rejects_ambiguous_arguments) {
    struct Case {
        const char *args;
        const char *err;
    };
    const Case cases[] = {
        {"foo", "bad DEVICE_INDEX foo, expected 0, 1, 2... (see accuchek --help)"},
        {"1x", "bad DEVICE_INDEX 1x, expected 0, 1, 2... (see accuchek --help)"},
        {"+1", "bad DEVICE_INDEX +1, expected 0, 1, 2... (see accuchek --help)"},
        {"99999999999", "bad DEVICE_INDEX 99999999999, expected 0, 1, 2... (see accuchek --help)"},
        {"0 1", "DEVICE_INDEX given twice"},
        {"--config a --config b", "--config given twice"},
        {"--replay a --replay b", "--replay given twice"},
        {"--capture a --capture b", "--capture given twice"},
        {"--merge a --merge b", "--merge given twice"},
        {"--wait 1 --wait 2", "--wait given twice"},
        {"--wait 0", "bad --wait 0, expected seconds from 1 to 3600"},
        {"--wait 3601", "bad --wait 3601, expected seconds from 1 to 3600"},
        {"--wait 10s", "bad --wait 10s, expected seconds from 1 to 3600"},
        {"--wait -5", "bad --wait -5, expected seconds from 1 to 3600"},
        {"--replay a --wait 5", "--wait waits for a meter, it does not go with --replay"},
        {"--replay a --capture b", "--capture records a meter, it does not go with --replay"},
        {"1 --replay a", "DEVICE_INDEX selects a meter, it does not go with --replay"},
    };
    for(const auto &c : cases) {
        auto r = runBinary(c.args);
        CHECK_EQ(r.code, kExitUsage);
        CHECK_EQ(r.err, std::string("accuchek: ") + c.err + "\n");
        CHECK_EQ(r.out, std::string(""));
    }
}

TEST(cli_outputs_flagged_samples) {
    auto r = runCli(sim::sessionTrace(kFlags));
    CHECK_EQ(r.code, 0);
    CHECK(std::string::npos!=r.out.find("\"mg/dL\":601, \"mmol/L\":33.4, \"status\":0, \"range\":\"high\""));
    CHECK(std::string::npos!=r.out.find("\"range\":\"low\""));
    CHECK(std::string::npos!=r.out.find("\"mg/dL\":140, \"mmol/L\": 7.8, \"status\":1, \"key\":\""));
}

// failures used to leave "[" plus some samples on stdout and exit 1, whatever the cause
TEST(cli_timeout_exit_code_and_message) {
    auto trace = replaceLine(sim::sessionTrace(kTwoSegments), kLineFirstSegment, "< !-7");
    auto r = runCli(trace);
    CHECK_EQ(r.code, kExitTransfer);
    CHECK_EQ(r.out, std::string(""));
    CHECK_EQ(r.err, std::string("accuchek: failed to receive message data segment: Operation timed out\n"));
}

TEST(cli_failure_after_some_samples_writes_none) {
    auto lastSegment = kLineFirstSegment + 2;
    auto trace = replaceLine(sim::sessionTrace(kTwoSegments), lastSegment, "< !-4");
    auto r = runCli(trace);
    CHECK_EQ(r.code, kExitTransfer);
    CHECK_EQ(r.out, std::string(""));
    CHECK(std::string::npos!=r.err.find("2 samples received before the error, none written"));
}

TEST(cli_abort_exit_code) {
    auto trace = replaceLine(sim::sessionTrace(kTwoSegments), kLineMdsAnswer, "< E60000020000");
    auto r = runCli(trace);
    CHECK_EQ(r.code, kExitProtocol);
    CHECK_EQ(r.out, std::string(""));
    CHECK(0==r.err.find("accuchek: received association abort"));
}

TEST(cli_empty_meter_outputs_no_readings) {
    auto r = runCli(sim::sessionTrace(kNoSegments));
    CHECK_EQ(r.code, kExitOk);
    CHECK_EQ(
        r.out,
        std::string("{\n  \"format\": 2,\n") + kMeterJson +
        "  \"clock\": {\"meter\":\"2026/10/01 21:06:58\", \"pc\":null, \"offset_s\":null, \"settable\":true, \"pc_synchronized\":null, \"action\":\"not_requested\"},\n"
        "  \"glucose\": {\"announced\":0, \"received\":0},\n"
        "  \"meal\": {\"announced\":0, \"received\":0, \"unmatched\":0},\n"
        "  \"readings\": []\n}\n"
    );
    CHECK_EQ(r.err, std::string(""));
}

// readings missing from a successful download used to go unnoticed unless the
// caller compared "announced" and "received" itself
TEST(cli_warns_when_readings_are_missing) {
    sim::Session s;
    s.glucose = kTwoSegments;
    s.glucoseCountError = 1;
    auto r = runCli(sim::sessionTrace(s));
    CHECK_EQ(r.code, kExitOk);
    CHECK_EQ(r.err, std::string("accuchek: warning: the meter announced 4 readings, 3 received\n"));
    CHECK(std::string::npos!=r.out.find("  \"glucose\": {\"announced\":4, \"received\":3},\n"));
    CHECK_EQ(runCli(sim::sessionTrace(kTwoSegments)).err, std::string(""));
}

// a full disk used to end in exit code 0 with the readings lost
TEST(cli_unwritable_stdout_is_an_error) {
    auto full = runCli(sim::sessionTrace(kTwoSegments), ">/dev/full");
    CHECK_EQ(full.code, kExitOutput);
    CHECK_EQ(full.err, std::string("accuchek: cannot write on stdout: No space left on device\n"));
    auto list = runBinary("--known-devices >/dev/full");
    CHECK_EQ(list.code, kExitOutput);
    auto help = runBinary("--help >/dev/full");
    CHECK_EQ(help.code, kExitOutput);
}

// with fd 1 closed, the --capture trace would get fd 1 and the JSON with it
TEST(cli_closed_stdout_is_refused_before_anything) {
    auto r = runCli(sim::sessionTrace(kTwoSegments), ">&-");
    CHECK_EQ(r.code, kExitOutput);
    CHECK_EQ(r.err, std::string("accuchek: stdout is closed, nowhere to write the readings\n"));
}

TEST(cli_unreadable_trace_is_a_usage_error) {
    auto r = runBinary("--replay /nonexistent.trace");
    CHECK_EQ(r.code, kExitUsage);
    CHECK_EQ(r.out, std::string(""));
    auto bad = runCli("garbage line\n");
    CHECK_EQ(bad.code, kExitUsage);
    CHECK(0==bad.err.find("accuchek: bad trace"));
}

TEST(cli_unknown_option_is_a_usage_error) {
    auto r = runBinary("--bogus");
    CHECK_EQ(r.code, kExitUsage);
    CHECK_EQ(r.err, std::string("accuchek: unknown or incomplete option --bogus (see accuchek --help)\n"));
}

TEST(cli_help_and_version) {
    for(const char *flag : {"--help", "-h"}) {
        auto r = runBinary(flag);
        CHECK_EQ(r.code, kExitOk);
        CHECK(0==r.out.find("usage: accuchek "));
        CHECK(std::string::npos!=r.out.find("--known-devices"));
        CHECK_EQ(r.err, std::string(""));
    }
}

// "2.1.0" or "2.1.0-3-gabcdef0-dirty": the X.Y.Z at the start of text
static bool releaseOf(
    const std::string &text,
    std::vector<int> &release
) {
    int x, y, z, length = 0;
    if(3!=sscanf(text.c_str(), "%d.%d.%d%n", &x, &y, &z, &length)) {
        return false;
    }
    release = {x, y, z};
    return '\0'==text[length] || '-'==text[length] || '\n'==text[length];
}

TEST(release_of_a_version) {
    std::vector<int> r;
    CHECK(releaseOf("2.10.0-3-gabcdef0-dirty", r));
    CHECK(std::vector<int>({2, 10, 0})==r);
    CHECK(!releaseOf("2.1", r));
    CHECK(!releaseOf("2.1.0rc1", r));
}

// git describe gives "2.1.0", "2.1.0-3-gabcdef0" or "2.1.0-dirty" once v2.1.0
// is tagged, the VERSION file elsewhere. Tagging a release without bumping
// VERSION fails here; bumping VERSION before tagging is the release commit.
TEST(cli_version_is_not_ahead_of_the_version_file) {
    std::string version;
    CHECK(readFile("VERSION", version));
    std::vector<int> file, built;
    CHECK(releaseOf(version, file));
    CHECK_EQ(version.back(), '\n');
    auto v = runBinary("--version");
    CHECK_EQ(v.code, kExitOk);
    CHECK_EQ(v.out.substr(0, 9), std::string("accuchek "));
    CHECK(releaseOf(v.out.substr(9), built));
    CHECK(built<=file);
    CHECK_EQ(v.out.back(), '\n');
}

// the udev rule must grant access to exactly the meters accuchek accepts
TEST(udev_rule_matches_known_devices) {
    std::string rule;
    CHECK(readFile("udev/70-accuchek.rules", rule));
    std::string ids;
    for(const char *key : {"ATTR{idVendor}==\"", "ATTR{idProduct}==\""}) {
        auto at = rule.find(key);
        CHECK(std::string::npos!=at);
        at += strlen(key);
        ids += rule.substr(at, rule.find('"', at) - at) + "\n";
    }
    std::string products;
    auto known = runBinary("--known-devices").out;
    for(size_t at=0; at<known.size(); at=known.find('\n', at) + 1) {
        products += (products.empty() ? "" : "|") + known.substr(at + 5, 4);
        CHECK_EQ(known.substr(at, 5), std::string("173a:"));
    }
    CHECK_EQ(ids, "173a\n" + products + "\n");
    CHECK(std::string::npos!=rule.find("TAG+=\"uaccess\""));
}

// ACCUCHEK_DBG used to send logs and hexdumps to stdout, inside the JSON
TEST(cli_debug_logs_stay_off_stdout) {
    auto quiet = runCli(sim::sessionTrace(kTwoSegments));
    auto debug = runCli(sim::sessionTrace(kTwoSegments), "", "env ACCUCHEK_DBG=1");
    CHECK_EQ(debug.code, kExitOk);
    CHECK_EQ(debug.out, quiet.out);
    CHECK(std::string::npos!=debug.err.find("BUFFER START"));
    CHECK(0==debug.err.find("accuchek[nfo] +"));
    CHECK_EQ(quiet.err, std::string(""));
}

// the old logger compiled $LOG as a regex: LOG='[' ended in std::regex_error, exit 134
TEST(cli_debug_ignores_log_variable) {
    auto r = runCli(sim::sessionTrace(kTwoSegments), "", "env ACCUCHEK_DBG=1 LOG='['");
    CHECK_EQ(r.code, kExitOk);
    CHECK(std::string::npos==r.err.find("regex"));
}

// text without every ', "key":"..."' of the readings: an output of accuchek before 2.2
static std::string withoutKeys(
    std::string text
) {
    const std::string marker = ", \"key\":\"";
    for(auto at = text.find(marker); std::string::npos!=at; at = text.find(marker, at)) {
        text.erase(at, marker.size() + 16 + 1);
    }
    return text;
}

static std::string replaceAll(
    std::string text,
    const std::string &from,
    const std::string &to
) {
    for(auto at = text.find(from); std::string::npos!=at; at = text.find(from, at + to.size())) {
        text.replace(at, from.size(), to);
    }
    return text;
}

TEST(cli_merge_with_itself_changes_nothing) {
    auto trace = sim::sessionTrace(mealSession());
    auto plain = runCli(trace);
    CHECK_EQ(plain.code, kExitOk);
    auto archive = writeTemp(plain.out);
    auto merged = runCli(trace, "--merge " + archive);
    CHECK_EQ(merged.code, kExitOk);
    CHECK_EQ(merged.err, std::string(""));
    CHECK_EQ(merged.out, plain.out);
    unlink(archive.c_str());
}

// a full meter drops its oldest reading for each new one: the archive keeps it
TEST(cli_merge_keeps_the_readings_the_meter_dropped) {
    sim::Session before;
    before.glucose = kTwoSegments;
    sim::Session after;
    after.glucose = {
        {{2021, 1, 15, 12, 30, 192, 0}},
        {{2021, 1, 16, 7, 45, 98, 0}, {2021, 1, 17, 9, 15, 150, 0}},
    };
    auto archive = writeTemp(runCli(sim::sessionTrace(before)).out);
    auto r = runCli(sim::sessionTrace(after), "--merge " + archive);
    CHECK_EQ(r.code, kExitOk);
    // the counters describe this download, the readings the whole history
    CHECK(std::string::npos!=r.out.find("  \"glucose\": {\"announced\":3, \"received\":3},\n"));
    for(const char *t : {"2021/01/15 08:00", "2021/01/15 12:30", "2021/01/16 07:45", "2021/01/17 09:15"}) {
        CHECK(std::string::npos!=r.out.find(t));
    }
    // the archive reading first, then the download in meter order
    CHECK(r.out.find("2021/01/15 08:00")<r.out.find("2021/01/15 12:30"));
    CHECK(r.out.find("2021/01/16 07:45")<r.out.find("2021/01/17 09:15"));
    CHECK(std::string::npos!=r.out.find("{ \"id\":     3, "));
    CHECK(std::string::npos==r.out.find("{ \"id\":     4, "));
    // the next download merges into this output the same way
    auto next = writeTemp(r.out);
    CHECK_EQ(runCli(sim::sessionTrace(after), "--merge " + next).out, r.out);
    unlink(archive.c_str());
    unlink(next.c_str());
}

// readings archived before 2.2 have no key: matched on minute, value and status
TEST(cli_merge_archive_without_keys) {
    auto trace = sim::sessionTrace(kTwoSegments);
    auto plain = runCli(trace);
    auto old = withoutKeys(plain.out);
    CHECK(std::string::npos==old.find("\"key\""));
    auto archive = writeTemp(old);
    auto r = runCli(trace, "--merge " + archive);
    CHECK_EQ(r.code, kExitOk);
    CHECK_EQ(r.out, plain.out);
    unlink(archive.c_str());
}

TEST(cli_merge_refuses_another_meter) {
    auto trace = sim::sessionTrace(kTwoSegments);
    auto other = replaceAll(runCli(trace).out, "92500000042", "92599999999");
    auto archive = writeTemp(other);
    auto r = runCli(trace, "--merge " + archive);
    CHECK_EQ(r.code, kExitUsage);
    CHECK_EQ(r.out, std::string(""));
    CHECK_EQ(r.err, "accuchek: archive " + archive + " holds the readings of meter 92599999999, this is meter 92500000042: keep one archive per meter\n");
    unlink(archive.c_str());
}

TEST(cli_merge_refuses_a_bad_archive) {
    auto trace = sim::sessionTrace(kTwoSegments);
    auto bad = writeTemp("{\"format\": 2, \"readings\": [{\"timestamp\":\"2026/10/01 08:00\"}]}");
    auto r = runCli(trace, "--merge " + bad);
    CHECK_EQ(r.code, kExitUsage);
    CHECK_EQ(r.out, std::string(""));
    CHECK_EQ(r.err, "accuchek: bad archive " + bad + ": reading 0: no \"mg/dL\"\n");
    unlink(bad.c_str());

    auto missing = runCli(trace, "--merge /nonexistent/archive.json");
    CHECK_EQ(missing.code, kExitUsage);
    CHECK_EQ(missing.err, std::string("accuchek: cannot read archive /nonexistent/archive.json\n"));
}

// "--merge a.json > a.json": the shell empties a.json before accuchek starts
TEST(cli_merge_into_the_archive_itself_is_refused) {
    auto trace = sim::sessionTrace(kTwoSegments);
    auto archive = writeTemp(runCli(trace).out);
    auto r = runCli(trace, "--merge " + archive + " >" + archive);
    CHECK_EQ(r.code, kExitUsage);
    CHECK(0==r.err.find("accuchek: stdout is the archive " + archive + " itself, emptied by the shell"));
    unlink(archive.c_str());
}

TEST(cli_csv_output) {
    auto r = runCli(sim::sessionTrace(kFlags), "--csv");
    CHECK_EQ(r.code, kExitOk);
    CHECK_EQ(r.err, std::string(""));
    CHECK_EQ(
        r.out,
        std::string(
            "id,key,epoch,timestamp,mg/dL,mmol/L,status,range,meal,error\n"
            "0,2026090108000000,1788242400,2026/09/01 08:00,120,6.7,0,,,\n"
            "1,2026090112000000,1788256800,2026/09/01 12:00,601,33.4,0,high,,\n"
            "2,2026090203150000,1788311700,2026/09/02 03:15,9,0.5,0,low,,\n"
            "3,2026090208000000,1788328800,2026/09/02 08:00,140,7.8,1,,,\n"
        )
    );
    auto meals = runCli(sim::sessionTrace(mealSession()), "--csv");
    CHECK(std::string::npos!=meals.out.find(",0,,fasting,\n"));
    auto empty = runCli(sim::sessionTrace(kNoSegments), "--csv");
    CHECK_EQ(empty.out, std::string("id,key,epoch,timestamp,mg/dL,mmol/L,status,range,meal,error\n"));
}

// every line has the 10 fields of the header, whatever the reading
TEST(cli_csv_with_merge_keeps_ten_fields) {
    auto trace = sim::sessionTrace(mealSession());
    auto archive = writeTemp(withoutKeys(runCli(sim::sessionTrace(kFlags)).out));
    auto r = runCli(trace, "--csv --merge " + archive);
    CHECK_EQ(r.code, kExitOk);
    size_t lines = 0;
    for(size_t at=0; at<r.out.size(); at=r.out.find('\n', at) + 1) {
        auto line = r.out.substr(at, r.out.find('\n', at) - at);
        CHECK_EQ(std::count(line.begin(), line.end(), ','), 9);
        ++lines;
    }
    // header, 4 archive readings of another day without key, 4 downloaded
    CHECK_EQ(lines, 9u);
    CHECK(std::string::npos!=r.out.find("\n0,,1788242400,2026/09/01 08:00,120,6.7,0,,,\n"));
    unlink(archive.c_str());
}

// without --wait a meter plugged in a second too late was "no meter found";
// #99 never exists, so a plugged meter is looked at but never read
TEST(cli_wait_gives_up_after_its_delay) {
    auto start = monotonicMs();
    auto r = runBinary("99 --wait 1");
    auto elapsed = monotonicMs() - start;
    CHECK(kExitNoDevice==r.code || kExitAccessDenied==r.code);
    if(kExitNoDevice==r.code) {
        CHECK(std::string::npos!=r.err.find(" after waiting 1 s\n"));
    }
    CHECK(900<=elapsed && elapsed<5000);
    CHECK_EQ(r.out, std::string(""));
}

// accuchek used to refuse to run unless root; USB access now comes from udev.
// Device #99 never exists, so a plugged meter is looked at but never read.
TEST(cli_runs_without_root) {
    auto r = runBinary("99");
    CHECK(kExitNoDevice==r.code || kExitAccessDenied==r.code || kExitTransfer==r.code);
    CHECK(std::string::npos==r.err.find("root"));
    CHECK(0==r.err.find("accuchek: "));
}
