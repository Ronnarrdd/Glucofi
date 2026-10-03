#include "check.h"
#include <output.h>

using namespace accuchek;

static Sample sampleAt(
    int day,
    uint16_t value
) {
    Sample s = {};
    s.year = 2026;
    s.month = 10;
    s.day = day;
    s.hour = 8;
    s.minute = 0;
    s.value = value;
    s.validDate = true;
    return s;
}

// a meter that describes nothing: every optional block is null
TEST(output_without_meter_report) {
    SessionReport report;
    report.glucose.received = 0;
    CHECK_EQ(
        outputJson(report, {}),
        std::string(
            "{\n  \"format\": 2,\n  \"meter\": null,\n  \"clock\": null,\n"
            "  \"glucose\": {\"announced\":null, \"received\":0},\n  \"meal\": null,\n"
            "  \"readings\": []\n}\n"
        )
    );
}

TEST(output_with_meter_clock_and_meals) {
    SessionReport report;
    report.hasMeter = true;
    report.meter.manufacturer = "Roche";
    report.meter.model = "925";
    report.meter.serial = "1\"2";
    report.meter.hasClock = true;
    report.meter.clock = {2026, 10, 1, 21, 6, 58, true};
    report.meter.clockSettable = true;
    struct tm pc = {};
    pc.tm_year = 126;
    pc.tm_mon = 9;
    pc.tm_mday = 1;
    pc.tm_hour = 20;
    pc.tm_min = 42;
    pc.tm_sec = 52;
    pc.tm_isdst = -1;
    report.pc = {mktime(&pc), true, false};
    report.hasClockOffset = true;
    report.clockOffsetS = 1446;
    report.clockAction = kClockPcNotSynchronized;
    report.glucose = {true, 2, 2};
    report.hasMealSegment = true;
    report.meal = {true, 3, 3};
    report.mealsUnmatched = 1;
    auto out = outputJson(report, {sampleAt(1, 104), sampleAt(2, 98)});
    CHECK(0==out.find(
        "{\n  \"format\": 2,\n"
        "  \"meter\": {\"manufacturer\":\"Roche\", \"model\":\"925\", \"serial\":\"1\\\"2\", \"firmware\":\"\", "
        "\"hardware\":\"\", \"software\":\"\", \"system_id\":\"\"},\n"
        "  \"clock\": {\"meter\":\"2026/10/01 21:06:58\", \"pc\":\"2026/10/01 20:42:52\", \"offset_s\":1446, "
        "\"settable\":true, \"pc_synchronized\":false, \"action\":\"pc_not_synchronized\"},\n"
        "  \"glucose\": {\"announced\":2, \"received\":2},\n"
        "  \"meal\": {\"announced\":3, \"received\":3, \"unmatched\":1},\n"
        "  \"readings\": [\n    { \"id\":     0,"
    ));
    CHECK(std::string::npos!=out.find(" },\n    { \"id\":     1,"));
    CHECK_EQ(out.substr(out.size() - 9), std::string(" }\n  ]\n}\n"));
}

TEST(output_count_warnings) {
    SessionReport report;
    CHECK(countWarnings(report).empty());
    report.glucose = {false, 0, 12};        // not announced: nothing to compare
    CHECK(countWarnings(report).empty());
    report.glucose = {true, 12, 12};
    report.meal = {true, 3, 1};             // ignored without a meal segment
    CHECK(countWarnings(report).empty());
    report.glucose = {true, 638, 637};
    report.hasMealSegment = true;
    auto warnings = countWarnings(report);
    CHECK_EQ(warnings.size(), 2u);
    if(2==warnings.size()) {
        CHECK_EQ(warnings[0], std::string("the meter announced 638 readings, 637 received"));
        CHECK_EQ(warnings[1], std::string("the meter announced 3 meal markers, 1 received"));
    }
}

// an unreadable date keeps the fields JSON gives it: raw value, key, error
TEST(output_csv_invalid_date_and_old_archive) {
    auto bad = sampleAt(1, 77);
    bad.validDate = false;
    bad.timeKey = ~0ull;
    bad.meal = kMDC_CTXT_GLU_MEAL_FASTING;
    auto old = sampleAt(2, 98);
    old.hasTimeKey = false;
    CHECK_EQ(
        outputCsv({bad, old}),
        std::string(
            "id,key,epoch,timestamp,mg/dL,mmol/L,status,range,meal,error\n"
            "0,FFFFFFFFFFFFFFFF,,,77,,0,,,invalid date\n"
            "1,,1790920800,2026/10/02 08:00,98,5.4,0,,,\n"
        )
    );
}

// replaying without --now: the PC side of the clock is unknown
TEST(output_clock_with_unknown_pc) {
    SessionReport report;
    report.hasMeter = true;
    report.meter.hasClock = true;
    report.meter.clock = {2026, 10, 1, 21, 6, 58, true};
    auto out = outputJson(report, {});
    CHECK(std::string::npos!=out.find(
        "\"pc\":null, \"offset_s\":null, \"settable\":false, \"pc_synchronized\":null, \"action\":\"not_requested\""
    ));
}
