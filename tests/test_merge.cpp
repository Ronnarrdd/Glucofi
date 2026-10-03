#include "check.h"
#include <merge.h>
#include <output.h>

using namespace accuchek;

// a reading as the meter gives it: date fields and the raw BCD time bytes
static Sample reading(
    int day,
    int hour,
    int minute,
    int second,
    uint16_t value,
    uint16_t status = 0
) {
    auto bcd = [](int v) { return uint64_t(((v / 10) << 4) | (v % 10)); };
    Sample s = {2026, 10, day, hour, minute, value, status, true};
    s.timeKey = (bcd(20) << 56) | (bcd(26) << 48) | (bcd(10) << 40) | (bcd(day) << 32) |
        (bcd(hour) << 24) | (bcd(minute) << 16) | (bcd(second) << 8);
    return s;
}

static Archive archiveOf(
    const std::string &text
) {
    Archive a;
    std::string error;
    CHECK(parseArchive(text, a, error));
    CHECK_EQ(error, std::string(""));
    return a;
}

static std::string archiveError(
    const std::string &text
) {
    Archive a;
    std::string error;
    CHECK(!parseArchive(text, a, error));
    return error;
}

static std::string wrapped(
    const std::string &readings
) {
    return "{\"format\": 2, \"meter\": {\"serial\": \"92500000042\"}, \"readings\": [" + readings + "]}";
}

// what accuchek writes reads back as the same samples, and writes the same again
TEST(merge_archive_round_trip) {
    std::vector<Sample> samples = {reading(1, 8, 0, 13, 104), reading(1, 12, 30, 0, 98), reading(2, 7, 5, 59, 140, 1)};
    samples[1].meal = kMDC_CTXT_GLU_MEAL_FASTING;
    samples.push_back(reading(2, 9, 0, 0, kValueHigh));
    samples.push_back(reading(2, 9, 10, 0, kValueLow, 0x0400));
    auto invalid = reading(3, 0, 0, 0, 77);
    invalid.timeKey = 0xFFFFFFFFFFFFFFFFull;
    invalid.validDate = false;
    samples.push_back(invalid);
    auto other = reading(3, 10, 0, 0, 120);
    other.meal = 0x1234;
    samples.push_back(other);

    SessionReport report;
    report.hasMeter = true;
    report.meter.serial = "92500000042";
    auto text = outputJson(report, samples);
    auto archive = archiveOf(text);
    CHECK_EQ(archive.serial, std::string("92500000042"));
    CHECK_EQ(archive.samples.size(), samples.size());
    CHECK_EQ(outputJson(report, archive.samples), text);
}

// versions before 2.0 wrote the readings array alone, without status nor key
TEST(merge_archive_of_old_versions) {
    auto a = archiveOf("[{ \"id\": 0, \"epoch\": 1617009120, \"timestamp\":\"2021/03/29 11:12\", \"mg/dL\":133, \"mmol/L\":  7.388889 }]");
    CHECK_EQ(a.samples.size(), 1u);
    CHECK_EQ(a.serial, std::string(""));
    if(1==a.samples.size()) {
        CHECK(!a.samples[0].hasTimeKey);
        CHECK_EQ(a.samples[0].value, 133);
        CHECK_EQ(a.samples[0].status, 0);
        CHECK_EQ(a.samples[0].minute, 12);
    }
    CHECK_EQ(archiveOf(wrapped("")).samples.size(), 0u);
}

// an edited or damaged archive is refused, never half read
TEST(merge_archive_refuses_inconsistent_readings) {
    struct Case {
        const char *reading;
        const char *error;
    };
    const Case cases[] = {
        {"1", "reading 0: not an object"},
        {"{\"timestamp\":\"2026/10/01 08:00\"}", "reading 0: no \"mg/dL\""},
        {"{\"timestamp\":\"2026/10/01 08:00\", \"mg/dL\":1.5}", "reading 0: \"mg/dL\" must be an integer from 0 to 65535"},
        {"{\"timestamp\":\"2026/10/01 08:00\", \"mg/dL\":-1}", "reading 0: \"mg/dL\" must be an integer from 0 to 65535"},
        {"{\"timestamp\":\"2026/10/01 08:00\", \"mg/dL\":90, \"status\":\"0\"}", "reading 0: \"status\" must be an integer from 0 to 65535"},
        {"{\"timestamp\":\"2026/13/01 08:00\", \"mg/dL\":90}", "reading 0: \"timestamp\" must be a date, YYYY/MM/DD HH:MM"},
        {"{\"timestamp\":\"2026/10/01 08:00:00\", \"mg/dL\":90}", "reading 0: \"timestamp\" must be a date, YYYY/MM/DD HH:MM"},
        {"{\"timestamp\":\"2026-10-01 08:00\", \"mg/dL\":90}", "reading 0: \"timestamp\" must be a date, YYYY/MM/DD HH:MM"},
        {"{\"timestamp\":null, \"mg/dL\":90}", "reading 0: \"timestamp\" must be a date, YYYY/MM/DD HH:MM"},
        {"{\"timestamp\":\"2026/10/01 08:00\", \"mg/dL\":90, \"key\":\"2026100108\"}", "reading 0: \"key\" must be 16 hex digits"},
        {"{\"timestamp\":\"2026/10/01 08:00\", \"mg/dL\":90, \"key\":\"2026100108010000\"}",
            "reading 0: \"key\" 2026100108010000 is not the time of \"timestamp\" 2026/10/01 08:00"},
        {"{\"timestamp\":\"2026/10/01 08:00\", \"mg/dL\":600, \"range\":\"high\"}",
            "reading 0: \"range\" must be \"high\" with 601 mg/dL or \"low\" with 9 mg/dL"},
        {"{\"timestamp\":\"2026/10/01 08:00\", \"mg/dL\":9, \"range\":\"under\"}",
            "reading 0: \"range\" must be \"high\" with 601 mg/dL or \"low\" with 9 mg/dL"},
        {"{\"timestamp\":\"2026/10/01 08:00\", \"mg/dL\":90, \"meal\":\"lunch\"}",
            "reading 0: \"meal\" must be fasting, before_meal, after_meal, casual, bedtime or other"},
        {"{\"timestamp\":\"2026/10/01 08:00\", \"mg/dL\":90, \"error\":\"invalid date\"}",
            "reading 0: a reading with an error has neither timestamp nor range"},
        {"{\"timestamp\":null, \"mg/dL\":90, \"error\":\"invalid date\", \"key\":\"2026100108000000\"}",
            "reading 0: \"key\" 2026100108000000 holds a valid date, the reading says invalid date"},
    };
    for(const auto &c : cases) {
        CHECK_EQ(archiveError(wrapped(c.reading)), std::string(c.error));
    }
    CHECK_EQ(archiveError(wrapped("{\"timestamp\":\"2026/10/01 08:00\", \"mg/dL\":90}, 2")), std::string("reading 1: not an object"));
    CHECK_EQ(archiveError("{\"format\": 3, \"readings\": []}"), std::string("not an accuchek output: \"format\" 2 expected"));
    CHECK_EQ(archiveError("{\"readings\": []}"), std::string("not an accuchek output: \"format\" 2 expected"));
    CHECK_EQ(archiveError("{\"format\": 2}"), std::string("not an accuchek output: no \"readings\" array"));
    CHECK_EQ(archiveError("\"readings\""), std::string("not an accuchek output: no \"readings\" array"));
    CHECK_EQ(archiveError(""), std::string("line 1: unexpected end of text"));
    // the key decides the date, the timestamp only has to agree with it
    auto a = archiveOf(wrapped("{\"timestamp\":\"2026/10/01 08:00\", \"mg/dL\":90, \"key\":\"2026100108004200\", \"extra\":[1]}"));
    CHECK_EQ(a.samples.size(), 1u);
    if(1==a.samples.size()) {
        CHECK_EQ(sampleKey(a.samples[0]), std::string("2026100108004200"));
    }
}

static std::string keys(
    const std::vector<Sample> &samples
) {
    std::string out;
    for(const auto &s : samples) {
        out += (s.hasTimeKey ? sampleKey(s).substr(6, 8) : std::string("nokey")) + ":" + std::to_string(s.value) + " ";
    }
    return out;
}

// the meter dropped its oldest reading and took a new one
TEST(merge_keeps_what_the_meter_dropped) {
    std::vector<Sample> archive = {reading(1, 8, 0, 0, 100), reading(1, 12, 0, 0, 110), reading(1, 19, 0, 0, 120)};
    std::vector<Sample> download = {reading(1, 12, 0, 0, 110), reading(1, 19, 0, 0, 120), reading(2, 8, 0, 0, 130)};
    std::vector<Sample> merged;
    CHECK_EQ(mergeSamples(archive, download, merged), 1u);
    CHECK_EQ(keys(merged), std::string("01080000:100 01120000:110 01190000:120 02080000:130 "));
    // merging again changes nothing
    std::vector<Sample> again;
    CHECK_EQ(mergeSamples(merged, download, again), 1u);
    CHECK_EQ(keys(again), keys(merged));
}

// same second but another value or status: another reading
TEST(merge_identity_is_key_value_and_status) {
    std::vector<Sample> archive = {reading(1, 8, 0, 0, 100), reading(1, 8, 0, 0, 101), reading(1, 8, 0, 0, 100, 1), reading(1, 8, 0, 0, 100)};
    std::vector<Sample> download = {reading(1, 8, 0, 0, 100)};
    std::vector<Sample> merged;
    // the archive duplicate goes, the two other readings stay
    CHECK_EQ(mergeSamples(archive, download, merged), 2u);
    CHECK_EQ(merged.size(), 3u);
    if(3==merged.size()) {
        CHECK_EQ(merged[0].value, 101);
        CHECK_EQ(merged[1].status, 1);
    }
}

// archives written before 2.2 have no key: same minute, value and status, one to one
TEST(merge_archive_without_keys) {
    auto old = [](Sample s) { s.hasTimeKey = false; s.timeKey = 0; return s; };
    std::vector<Sample> archive = {
        old(reading(1, 8, 0, 0, 100)), old(reading(1, 12, 0, 0, 110)), old(reading(1, 12, 0, 0, 110)), old(reading(1, 13, 0, 0, 90)),
    };
    // two readings at 12:00 in the archive, one left on the meter (seconds differ)
    std::vector<Sample> download = {reading(1, 12, 0, 31, 110), reading(1, 13, 0, 5, 91)};
    std::vector<Sample> merged;
    CHECK_EQ(mergeSamples(archive, download, merged), 3u);
    CHECK_EQ(keys(merged), std::string("nokey:100 nokey:110 nokey:90 01120031:110 01130005:91 "));

    // unreadable dates match on value and status
    auto bad = reading(1, 0, 0, 0, 77);
    bad.validDate = false;
    bad.timeKey = ~0ull;
    std::vector<Sample> badMerged;
    CHECK_EQ(mergeSamples({old(bad)}, {bad}, badMerged), 0u);
    CHECK_EQ(badMerged.size(), 1u);
}

TEST(merge_empty_sides) {
    std::vector<Sample> merged;
    CHECK_EQ(mergeSamples({}, {}, merged), 0u);
    CHECK(merged.empty());
    CHECK_EQ(mergeSamples({reading(1, 8, 0, 0, 100)}, {}, merged), 1u);
    CHECK_EQ(merged.size(), 1u);
    CHECK_EQ(mergeSamples({}, {reading(1, 8, 0, 0, 100)}, merged), 0u);
    CHECK_EQ(merged.size(), 1u);
}
