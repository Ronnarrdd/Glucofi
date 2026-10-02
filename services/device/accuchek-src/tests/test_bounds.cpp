#include "check.h"
#include "fuzz.h"
#include <session.h>
#include <trace.h>

using namespace accuchek;
using fuzz::GuardedBytes;

static const std::vector<sim::Record> kRecords = {
    {2026, 9, 1, 8, 0, 120, 0},
    {2026, 9, 1, 12, 0, 180, 0},
};

TEST(reader_stops_at_the_end) {
    const uint8_t bytes[] = {0x12, 0x34, 0x56};
    Reader r(bytes, sizeof(bytes));
    CHECK_EQ(r.u16(), 0x1234);
    CHECK(r.ok);
    CHECK_EQ(r.u16(), 0);
    CHECK(!r.ok);
    CHECK_EQ(r.u8(), 0);
    CHECK(!r.ok);
    Reader past(bytes, sizeof(bytes), 4);
    CHECK(!past.ok);
}

TEST(invoke_id_needs_eight_bytes) {
    GuardedBytes g(sim::Bytes{0xE7, 0x00, 0x00, 0x02, 0x00, 0x00, 0x12});
    uint16_t invokeId = 0;
    CHECK(!readInvokeId(g.data, g.size, invokeId));
}

TEST(segment_announcing_more_entries_than_received) {
    auto packet = sim::dataSegment(0x20, 0x0100, 0, kRecords, true, true);
    packet[30] = 0x00;
    packet[31] = 200;   // 200 entries announced, 2 present
    GuardedBytes g(packet);
    Segment segment;
    std::string error;
    CHECK(!parseSegment(g.data, g.size, segment, error));
    CHECK(std::string::npos!=error.find("announces 200 entries but holds 2"));
}

TEST(segment_header_cut_short) {
    auto packet = sim::dataSegment(0x20, 0x0100, 0, kRecords, true, true);
    for(size_t n=0; n<36; ++n) {
        GuardedBytes g(sim::Bytes(packet.begin(), packet.begin() + n));
        Segment segment;
        std::string error;
        CHECK(!parseSegment(g.data, g.size, segment, error));
    }
}

TEST(segment_last_entry_cut_short) {
    auto packet = sim::dataSegment(0x20, 0x0100, 0, kRecords, true, true);
    packet.pop_back();
    GuardedBytes g(packet);
    Segment segment;
    std::string error;
    CHECK(!parseSegment(g.data, g.size, segment, error));
}

TEST(config_info_truncated_anywhere) {
    auto packet = sim::configInfo(0x10, 0x0100, 1);
    for(size_t n=0; n<packet.size(); ++n) {
        GuardedBytes g(sim::Bytes(packet.begin(), packet.begin() + n));
        uint16_t handle = 0;
        uint16_t nbSegments = 0;
        CHECK(!parseConfigInfo(g.data, g.size, handle, nbSegments));
    }
}

TEST(config_info_object_size_past_the_end) {
    auto packet = sim::configInfo(0x10, 0x0100, 1);
    packet[34] = 0xFF;      // size of the first object
    packet[35] = 0xFF;
    GuardedBytes g(packet);
    uint16_t handle = 0;
    uint16_t nbSegments = 0;
    CHECK(!parseConfigInfo(g.data, g.size, handle, nbSegments));
}

TEST(config_info_huge_object_count) {
    auto packet = sim::configInfo(0x10, 0x0100, 1);
    packet[24] = 0xFF;
    packet[25] = 0xFF;
    GuardedBytes g(packet);
    uint16_t handle = 0;
    uint16_t nbSegments = 0;
    CHECK(parseConfigInfo(g.data, g.size, handle, nbSegments));     // pm-store still found
    CHECK_EQ(handle, 0x0100);
}

TEST(mds_answer_truncated_anywhere) {
    auto packet = sim::mdsAnswer(0x11, sim::Meter());
    for(size_t n=0; n<packet.size(); ++n) {
        GuardedBytes g(sim::Bytes(packet.begin(), packet.begin() + n));
        MeterInfo info;
        CHECK(!parseMdsAnswer(g.data, g.size, info));
    }
}

TEST(mds_answer_huge_attribute_count) {
    auto packet = sim::mdsAnswer(0x11, sim::Meter());
    packet[14] = 0xFF;
    packet[15] = 0xFF;
    GuardedBytes g(packet);
    MeterInfo info;
    CHECK(!parseMdsAnswer(g.data, g.size, info));
}

TEST(segment_info_truncated_anywhere) {
    auto packet = sim::segmentInfoResponse(0x12, 0x0100, sim::guideSegments(638, 576));
    for(size_t n=0; n<packet.size(); ++n) {
        GuardedBytes g(sim::Bytes(packet.begin(), packet.begin() + n));
        std::vector<SegmentInfo> segments;
        CHECK(!parseSegmentInfo(g.data, g.size, segments));
    }
}

TEST(segment_info_huge_segment_count) {
    auto packet = sim::segmentInfoResponse(0x12, 0x0100, sim::guideSegments(638, 576));
    packet[18] = 0xFF;
    packet[19] = 0xFF;
    GuardedBytes g(packet);
    std::vector<SegmentInfo> segments;
    CHECK(!parseSegmentInfo(g.data, g.size, segments));
}

TEST(meal_segment_cut_short) {
    auto packet = sim::mealSegment(0x30, 0x0100, 0, {{2026, 9, 30, 7, 31, 12, kMDC_CTXT_GLU_MEAL_FASTING}}, true, true);
    for(size_t n=0; n<packet.size(); ++n) {
        GuardedBytes g(sim::Bytes(packet.begin(), packet.begin() + n));
        MealSegment segment;
        std::string error;
        CHECK(!parseMealSegment(g.data, g.size, segment, error));
    }
}

// a glucose segment is not a marker segment: entries of 12 bytes, not 10
TEST(meal_parser_rejects_glucose_entries) {
    auto packet = sim::dataSegment(0x20, 0x0100, 0, kRecords, true, true);
    GuardedBytes g(packet);
    MealSegment segment;
    std::string error;
    CHECK(!parseMealSegment(g.data, g.size, segment, error));
}

TEST(invalid_bcd_date_is_flagged_not_trusted) {
    CHECK_EQ(decodeBcd(0x1A), -1);
    CHECK_EQ(decodeBcd(0xF0), -1);
    auto packet = sim::dataSegment(0x20, 0x0100, 0, kRecords, true, true);
    packet[36 + 2] = 0x13;  // month 13
    packet[48 + 4] = 0x2F;  // hour 2F, not BCD
    GuardedBytes g(packet);
    Segment segment;
    std::string error;
    CHECK(parseSegment(g.data, g.size, segment, error));
    CHECK_EQ(segment.samples.size(), 2u);
    CHECK(!segment.samples[0].validDate);
    CHECK(!segment.samples[1].validDate);
    auto json = sampleJson(segment.samples[0], 0);
    CHECK(std::string::npos!=json.find("\"epoch\":null, \"timestamp\":null"));
    CHECK(std::string::npos!=json.find("\"error\":\"invalid date\""));
}

TEST(session_with_truncated_segment_fails_cleanly) {
    auto trace = sim::sessionTrace({kRecords});
    auto seg = sim::dataSegment(0x20, 0x0100, 0, kRecords, true, true);
    auto full = sim::line('<', seg);
    seg.resize(40);
    auto pos = trace.find(full);
    CHECK(std::string::npos!=pos);
    trace.replace(pos, full.size(), sim::line('<', seg));
    std::string error;
    try {
        ReplayTransport t(trace);
        downloadSamples(t, [](const Sample &) {});
    } catch(const SessionError &e) {
        error = e.what();
    }
    CHECK(std::string::npos!=error.find("announces 2 entries but holds 0"));
}

TEST(fuzz_smoke) {
    auto stats = fuzz::run(1, 0, 3000);
    CHECK_EQ(stats.invariantFailures, 0);
    CHECK(0<stats.parsedSegments);
    CHECK(0<stats.rejectedSegments);
    CHECK(0<stats.sessionsFailed);
}
