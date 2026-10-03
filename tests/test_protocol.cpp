#include "check.h"
#include "sim.h"
#include <protocol.h>
#include <trace.h>

using namespace accuchek;

static Segment segmentOf(const sim::Bytes &packet) {
    Segment segment;
    std::string error;
    CHECK(parseSegment(packet.data(), packet.size(), segment, error));
    CHECK_EQ(error, std::string(""));
    return segment;
}

template<typename F>
static std::string builtHex(F build) {
    auto b = sim::sentWith(build);
    return toHex(b.data(), b.size());
}

// expected bytes typed from the field lists of the original upstream main.cpp

TEST(association_response_bytes) {
    CHECK_EQ(
        builtHex([](uint8_t *b) { return buildAssociationResponse(b); }),
        std::string("E300002C0003507900268000000280008000000000000000800000000008123456780000000000000000000000000000")
    );
}

TEST(config_received_bytes) {
    CHECK_EQ(
        builtHex([](uint8_t *b) { return buildConfigReceived(b, 0x0010); }),
        std::string("E7000016001400100201000E0000000000000D1C000440000000")
    );
}

TEST(mds_request_uses_next_invoke_id) {
    CHECK_EQ(
        builtHex([](uint8_t *b) { return buildMdsRequest(b, 0x0010); }),
        std::string("E700000E000C001101030006000000000000")
    );
}

TEST(segment_info_request_bytes) {
    CHECK_EQ(
        builtHex([](uint8_t *b) { return buildSegmentInfoRequest(b, 0x0011, 0x0100); }),
        std::string("E700001400120012" "0107000C01000C0D" "0006000100020000")
    );
}

TEST(trigger_transfer_bytes) {
    CHECK_EQ(
        builtHex([](uint8_t *b) { return buildTriggerTransfer(b, 0x0012, 0x0100); }),
        std::string("E7000010000E0013" "0107000801000C1C" "00020000")
    );
}

TEST(trigger_transfer_of_another_segment) {
    CHECK_EQ(
        builtHex([](uint8_t *b) { return buildTriggerTransfer(b, 0x0013, 0x0005, 3); }),
        std::string("E7000010000E0014" "0107000800050C1C" "00020003")
    );
}

// field list of tidepool's buildSetTimeRequest: MDS handle 0, AbsoluteTime BCD, accuracy 0
TEST(set_time_bytes) {
    auto local = sim::localTm({2026, 10, 1, 20, 42, 52});
    CHECK_EQ(
        builtHex([&](uint8_t *b) { return buildSetTime(b, 0x0012, local); }),
        std::string("E700001A00180013" "0107001200000C17" "000C" "2026100120425200" "00000000")
    );
}

TEST(segment_ack_echoes_segment_fields) {
    CHECK_EQ(
        builtHex([](uint8_t *b) { return buildSegmentAck(b, 0x0020, 0x0100, 1, 2, 3); }),
        std::string("E700001E001C0020" "020100160100FFFF" "FFFF0D21000C0000" "00010000000200030080")
    );
}

TEST(release_request_bytes) {
    CHECK_EQ(
        builtHex([](uint8_t *b) { return buildReleaseRequest(b); }),
        std::string("E40000020000")
    );
}

TEST(config_info_finds_pm_store_after_other_objects) {
    auto packet = sim::configInfo(0x0010, 0x0123, 4);
    uint16_t handle = 0;
    uint16_t nbSegments = 0;
    CHECK(parseConfigInfo(packet.data(), packet.size(), handle, nbSegments));
    CHECK_EQ(handle, 0x0123);
    CHECK_EQ(nbSegments, 4);
    uint16_t invokeId = 0;
    CHECK(readInvokeId(packet.data(), packet.size(), invokeId));
    CHECK_EQ(invokeId, 0x0010);
}

TEST(config_info_without_pm_store_is_rejected) {
    auto packet = sim::configInfo(0x0010, 0x0123, 4);
    packet[25] = 1;     // only the first object (numeric metric) remains
    uint16_t handle = 0;
    uint16_t nbSegments = 0;
    CHECK(!parseConfigInfo(packet.data(), packet.size(), handle, nbSegments));
}

TEST(bcd_decoding) {
    CHECK_EQ(decodeBcd(0x00), 0);
    CHECK_EQ(decodeBcd(0x09), 9);
    CHECK_EQ(decodeBcd(0x21), 21);
    CHECK_EQ(decodeBcd(0x59), 59);
}

TEST(segment_samples_and_flags) {
    auto packet = sim::dataSegment(0x0020, 0x0100, 7, {
        {2021, 3, 29, 11, 12, 133, 0},
        {2026, 12, 31, 23, 59, 409, 0x0001},
    }, true, true);
    auto segment = segmentOf(packet);
    CHECK(segment.last);
    CHECK_EQ(segment.u0, 0x00000000u);
    CHECK_EQ(segment.u1, 0x00070000u);
    CHECK_EQ(segment.u2, 2);
    CHECK_EQ(segment.samples.size(), 2u);
    const auto &a = segment.samples[0];
    CHECK_EQ(a.year, 2021);
    CHECK_EQ(a.month, 3);
    CHECK_EQ(a.day, 29);
    CHECK_EQ(a.hour, 11);
    CHECK_EQ(a.minute, 12);
    CHECK_EQ(a.value, 133);
    CHECK_EQ(a.status, 0);
    const auto &b = segment.samples[1];
    CHECK_EQ(b.year, 2026);
    CHECK_EQ(b.month, 12);
    CHECK_EQ(b.minute, 59);
    CHECK_EQ(b.value, 409);
    CHECK_EQ(b.status, 1);
}

TEST(segment_not_last) {
    auto packet = sim::dataSegment(0x0020, 0x0100, 0, {{2024, 1, 1, 8, 0, 100, 0}}, true, false);
    CHECK(!segmentOf(packet).last);
}

TEST(epoch_in_winter_is_local_time) {
    Sample s = {2021, 1, 15, 8, 0, 133, 0, true};
    CHECK_EQ((long long)sampleEpoch(s), 1610694000LL);
}

// the meter shows 07:36 on 2 July 2026: that is 05:36 UTC in Paris (CEST)
TEST(epoch_in_summer_is_local_time) {
    Sample s = {2026, 7, 2, 7, 36, 120, 0, true};
    CHECK_EQ((long long)sampleEpoch(s), 1782970560LL);
}

TEST(epoch_around_dst_changes) {
    Sample beforeSpring = {2026, 3, 29, 1, 59, 100, 0, true};
    Sample afterSpring = {2026, 3, 29, 3, 0, 100, 0, true};
    Sample beforeAutumn = {2026, 10, 25, 1, 59, 100, 0, true};
    Sample afterAutumn = {2026, 10, 25, 3, 0, 100, 0, true};
    CHECK_EQ((long long)sampleEpoch(beforeSpring), 1774745940LL);
    CHECK_EQ((long long)sampleEpoch(afterSpring), 1774746000LL);
    CHECK_EQ((long long)sampleEpoch(beforeAutumn), 1792886340LL);
    CHECK_EQ((long long)sampleEpoch(afterAutumn), 1792893600LL);

    // 02:30 happens twice on 25 October: either instant is acceptable
    Sample ambiguous = {2026, 10, 25, 2, 30, 100, 0, true};
    auto e = (long long)sampleEpoch(ambiguous);
    CHECK(1792888200LL==e || 1792891800LL==e);
}

TEST(sample_json_format) {
    Sample s = {2021, 1, 15, 8, 0, 133, 0, true};
    s.timeKey = 0x2021011508001700ull;
    CHECK_EQ(
        sampleJson(s, 0),
        std::string("{ \"id\":     0, \"epoch\": 1610694000, \"timestamp\":\"2021/01/15 08:00\", \"mg/dL\":133, \"mmol/L\": 7.4, \"status\":0, \"key\":\"2021011508001700\" }")
    );
    // a reading of an old archive has no key: none is made up
    s.hasTimeKey = false;
    CHECK_EQ(
        sampleJson(s, 0),
        std::string("{ \"id\":     0, \"epoch\": 1610694000, \"timestamp\":\"2021/01/15 08:00\", \"mg/dL\":133, \"mmol/L\": 7.4, \"status\":0 }")
    );
}

// the id is a position: a meter that dropped its oldest reading shifts every
// id, the key stays (raw time bytes, BCD: they read as the date)
TEST(sample_key_is_the_raw_meter_time) {
    Sample s = {2026, 10, 1, 20, 47, 104, 0, true};
    s.timeKey = 0x2026100120471300ull;
    CHECK_EQ(sampleKey(s), std::string("2026100120471300"));
    s.timeKey = 0x00000000000000ABull;
    CHECK_EQ(sampleKey(s), std::string("00000000000000AB"));
    s.timeKey = ~0ull;
    CHECK_EQ(sampleKey(s), std::string("FFFFFFFFFFFFFFFF"));
    // unreadable dates keep their key: it is how they are told apart
    s.validDate = false;
    CHECK(std::string::npos!=sampleJson(s, 0).find("\"error\":\"invalid date\", \"key\":\"FFFFFFFFFFFFFFFF\" }"));
}

// mg/dL / 18 used to be printed with 6 decimals (5.777778); a meter set to
// mmol/L shows one
TEST(mmol_per_liter_has_one_decimal) {
    CHECK_EQ(mmolPerLiter(104), 5.8);
    CHECK_EQ(mmolPerLiter(99), 5.5);
    CHECK_EQ(mmolPerLiter(9), 0.5);
    CHECK_EQ(mmolPerLiter(601), 33.4);
    CHECK_EQ(mmolPerLiter(70), 3.9);
    CHECK_EQ(mmolPerLiter(180), 10.0);
    Sample s = {2021, 1, 15, 8, 0, 104, 0, true};
    CHECK(std::string::npos!=sampleJson(s, 0).find("\"mg/dL\":104, \"mmol/L\": 5.8, "));
    s.value = 300;
    CHECK(std::string::npos!=sampleJson(s, 0).find("\"mg/dL\":300, \"mmol/L\":16.7, "));
}

TEST(sample_json_high_reading) {
    Sample s = {2021, 1, 15, 8, 0, kValueHigh, 0, true};
    auto json = sampleJson(s, 3);
    CHECK(std::string::npos!=json.find("\"mg/dL\":601"));
    CHECK(std::string::npos!=json.find("\"range\":\"high\""));
}

TEST(sample_json_low_reading) {
    Sample s = {2021, 1, 15, 8, 0, kValueLow, 0x0400, true};
    auto json = sampleJson(s, 3);
    CHECK(std::string::npos!=json.find("\"mg/dL\":  9"));
    CHECK(std::string::npos!=json.find("\"status\":1024, \"range\":\"low\", \"key\":"));
}

TEST(sample_json_keeps_flagged_status) {
    Sample s = {2021, 1, 15, 8, 0, 140, 0x0001, true};
    auto json = sampleJson(s, 3);
    CHECK(std::string::npos!=json.find("\"mg/dL\":140"));
    CHECK(std::string::npos!=json.find("\"status\":1, \"key\":"));
    CHECK(std::string::npos==json.find("range"));
}

TEST(config_file_parsing) {
    auto config = parseConfig(
        "vendor_0x173a_device_0x21d5 1 # roche accu-Chek model  929\n"
        "vendor_0x173a_device_0x21d7 0 # disabled\n"
        "\n"
        "garbage\n"
    );
    CHECK(isDeviceAllowed(config, 0x173a, 0x21d5));
    CHECK(!isDeviceAllowed(config, 0x173a, 0x21d7));
    CHECK(!isDeviceAllowed(config, 0x173a, 0x21d8));
}

TEST(config_last_line_without_newline) {
    auto config = parseConfig("vendor_0x173a_device_0x21d5 1");
    CHECK(isDeviceAllowed(config, 0x173a, 0x21d5));
}

// accuchek used to read ./config.txt and ignore every meter without it
TEST(known_devices_need_no_config_file) {
    auto config = defaultConfig();
    CHECK(isDeviceAllowed(config, 0x173a, 0x21d5));
    CHECK(isDeviceAllowed(config, 0x173a, 0x21d7));
    CHECK(isDeviceAllowed(config, 0x173a, 0x21d8));
    CHECK(!isDeviceAllowed(config, 0x173a, 0x1234));
    CHECK(!isDeviceAllowed(config, 0x046d, 0x21d5));
}

TEST(config_file_adds_and_disables_devices) {
    auto config = configWithFile(
        "vendor_0x173a_device_0x21d7 0\n"
        "vendor_0x173a_device_0x2222 1\n"
    );
    auto devices = allowedDevices(config);
    CHECK_EQ(devices.size(), 3u);
    CHECK_EQ(devices[0], std::string("173a:21d5"));
    CHECK_EQ(devices[1], std::string("173a:21d8"));
    CHECK_EQ(devices[2], std::string("173a:2222"));
}

static sim::Bytes fixtureHex(
    const char *path
) {
    std::string text;
    CHECK(readFile(path, text));
    sim::Bytes b;
    std::string hex;
    for(size_t start=0; start<text.size();) {
        auto nl = text.find('\n', start);
        auto line = text.substr(start, nl - start);
        start = (std::string::npos==nl ? text.size() : nl + 1);
        if(!line.empty() && '#'!=line[0]) hex += line;
    }
    for(size_t i=0; i+1<hex.size(); i+=2) {
        b.push_back(uint8_t(std::stoi(hex.substr(i, 2), 0, 16)));
    }
    return b;
}

// bytes captured from a real Accu-Chek Guide 925
TEST(mds_answer_of_a_real_guide) {
    auto packet = fixtureHex("tests/fixtures/guide925_mds_answer.hex");
    MeterInfo info;
    CHECK(parseMdsAnswer(packet.data(), packet.size(), info));
    CHECK_EQ(info.manufacturer, std::string("Roche"));
    CHECK_EQ(info.model, std::string("925"));
    CHECK_EQ(info.serial, std::string("92500000042"));
    CHECK_EQ(info.firmware, std::string("v1.9.6"));
    CHECK_EQ(info.hardware, std::string("G"));
    CHECK_EQ(info.systemId, std::string("0060190000000042"));
    CHECK(info.hasClock);
    CHECK_EQ(formatTime(info.clock), std::string("2026/10/01 21:06:58"));
    CHECK(info.clockSettable);
}

TEST(segment_info_of_a_real_guide) {
    auto packet = fixtureHex("tests/fixtures/guide925_segment_info.hex");
    std::vector<SegmentInfo> segments;
    CHECK(parseSegmentInfo(packet.data(), packet.size(), segments));
    CHECK_EQ(segments.size(), 4u);
    if(4!=segments.size()) {
        return;
    }
    CHECK_EQ(segments[0].kind, kSegmentGlucose);
    CHECK_EQ(segments[0].usageCount, 638u);
    CHECK_EQ(segments[0].label, std::string("PMSegGluc"));
    CHECK_EQ(segments[1].kind, kSegmentOther);     // control solution
    CHECK_EQ(segments[2].kind, kSegmentOther);     // meter errors
    CHECK_EQ(segments[3].kind, kSegmentMeal);
    CHECK_EQ(segments[3].instance, 3);
    CHECK_EQ(segments[3].usageCount, 576u);
    CHECK(segments[3].hasUsageCount);
}

TEST(simulated_answers_match_the_real_layout) {
    sim::Meter meter;
    auto mds = sim::mdsAnswer(0x0011, meter);
    MeterInfo info;
    CHECK(parseMdsAnswer(mds.data(), mds.size(), info));
    CHECK_EQ(info.serial, meter.serial);
    CHECK_EQ(formatTime(info.clock), std::string("2026/10/01 21:06:58"));
    auto real = fixtureHex("tests/fixtures/guide925_segment_info.hex");
    auto simulated = sim::segmentInfoResponse(real[6] << 8 | real[7], 0x0005, sim::guideSegments(638, 576));
    // same bytes except the segment start and end dates the simulator leaves out
    std::vector<SegmentInfo> a, b;
    CHECK(parseSegmentInfo(real.data(), real.size(), a));
    CHECK(parseSegmentInfo(simulated.data(), simulated.size(), b));
    CHECK_EQ(a.size(), b.size());
    for(size_t i=0; i<a.size() && i<b.size(); ++i) {
        CHECK_EQ(a[i].instance, b[i].instance);
        CHECK_EQ(a[i].kind, b[i].kind);
        CHECK_EQ(a[i].usageCount, b[i].usageCount);
    }
}

TEST(mds_answer_without_attributes) {
    auto packet = sim::mdsAnswer(0x0011);
    MeterInfo info;
    CHECK(parseMdsAnswer(packet.data(), packet.size(), info));
    CHECK(!info.hasClock);
    CHECK(!info.clockSettable);
    CHECK_EQ(info.serial, std::string(""));
}

TEST(mds_answer_clock_not_settable) {
    sim::Meter meter;
    meter.settable = false;
    auto packet = sim::mdsAnswer(0x0011, meter);
    MeterInfo info;
    CHECK(parseMdsAnswer(packet.data(), packet.size(), info));
    CHECK(info.hasClock);
    CHECK(!info.clockSettable);
}

TEST(mds_answer_with_invalid_clock) {
    sim::Meter meter;
    meter.clock = {2026, 13, 1, 21, 6, 58};
    auto packet = sim::mdsAnswer(0x0011, meter);
    MeterInfo info;
    CHECK(parseMdsAnswer(packet.data(), packet.size(), info));
    CHECK(!info.hasClock);
}

TEST(other_messages_are_not_mds_answers) {
    auto packet = sim::segmentHeaders(0x0013, 0x0100);
    MeterInfo info;
    CHECK(!parseMdsAnswer(packet.data(), packet.size(), info));
    std::vector<SegmentInfo> segments;
    CHECK(!parseSegmentInfo(packet.data(), packet.size(), segments));
}

TEST(segment_kind_needs_the_exact_entry_layout) {
    using sim::segmentMap;
    auto info = [](const sim::Bytes &map) {
        sim::Writer attrs;
        sim::attr(attrs, kMDC_ATTR_PM_SEG_MAP, map);
        auto w = sim::apdu(0x12, kDATA_ADPU_RESPONSE_CONFIRMED_ACTION);
        w.u16(5).u16(kACTION_TYPE_MDC_ACT_SEG_GET_INFO).u16(0);
        w.u16(1).u16(6 + attrs.b.size()).u16(7).u16(1).u16(attrs.b.size()).bytes(attrs.b);
        auto b = sim::finish(w);
        std::vector<SegmentInfo> segments;
        CHECK(parseSegmentInfo(b.data(), b.size(), segments));
        return segments.empty() ? kSegmentOther : segments[0].kind;
    };
    CHECK_EQ(info(segmentMap(kMDC_MOC_VMO_METRIC_ENUM, 128, kMDC_CTXT_GLU_MEAL, 4, {{kMDC_ATTR_ENUM_OBS_VAL_SIMP_OID, 2}})), kSegmentMeal);
    // meal markers stored as a 4 byte value: unknown layout, not read
    CHECK_EQ(info(segmentMap(kMDC_MOC_VMO_METRIC_ENUM, 128, kMDC_CTXT_GLU_MEAL, 4, {{kMDC_ATTR_ENUM_OBS_VAL_SIMP_OID, 4}})), kSegmentOther);
    // control solution results are not blood glucose
    CHECK_EQ(info(segmentMap(kMDC_MOC_VMO_METRIC_NU, 2, kMDC_CONC_GLU_CONTROL, 2, {{kMDC_ATTR_NU_VAL_OBS_BASIC, 2}, {kMDC_ATTR_MSMT_STAT, 2}})), kSegmentOther);
    CHECK_EQ(info(segmentMap(kMDC_MOC_VMO_METRIC_NU, 2, 0x7270, 1, {{kMDC_ATTR_NU_VAL_OBS_BASIC, 2}, {kMDC_ATTR_MSMT_STAT, 2}})), kSegmentGlucose);
    CHECK_EQ(info(segmentMap(kMDC_MOC_VMO_METRIC_NU, 2, 0x7270, 1, {{kMDC_ATTR_NU_VAL_OBS_BASIC, 2}})), kSegmentOther);
}

TEST(meal_segment_entries) {
    auto packet = sim::mealSegment(0x0030, 0x0100, 0, {
        {2026, 9, 30, 7, 31, 12, kMDC_CTXT_GLU_MEAL_FASTING},
        {2026, 9, 30, 13, 2, 0, kMDC_CTXT_GLU_MEAL_POSTPRANDIAL},
    }, true, true);
    MealSegment segment;
    std::string error;
    CHECK(parseMealSegment(packet.data(), packet.size(), segment, error));
    CHECK(segment.last);
    CHECK_EQ(segment.entries.size(), 2u);
    CHECK_EQ(segment.entries[0].meal, kMDC_CTXT_GLU_MEAL_FASTING);
    CHECK_EQ(segment.entries[0].timeKey, 0x2026093007311200ull);
    CHECK_EQ(segment.entries[1].meal, kMDC_CTXT_GLU_MEAL_POSTPRANDIAL);
}

TEST(meal_segment_announcing_more_entries_than_received) {
    auto packet = sim::mealSegment(0x0030, 0x0100, 0, {{2026, 9, 30, 7, 31, 12, kMDC_CTXT_GLU_MEAL_FASTING}}, true, true);
    packet[31] = 2;
    MealSegment segment;
    std::string error;
    CHECK(!parseMealSegment(packet.data(), packet.size(), segment, error));
    CHECK(std::string::npos!=error.find("announces 2 entries but holds 1"));
}

TEST(sample_time_key_includes_seconds) {
    auto packet = sim::dataSegment(0x0020, 0x0100, 0, {
        {2026, 9, 30, 7, 31, 120, 0, 12},
        {2026, 9, 30, 7, 31, 121, 0, 13},
    }, true, true);
    auto segment = segmentOf(packet);
    CHECK_EQ(segment.samples[0].timeKey, 0x2026093007311200ull);
    CHECK_EQ(segment.samples[1].timeKey, 0x2026093007311300ull);
    CHECK_EQ(segment.samples[0].meal, 0);
}

TEST(meals_attach_to_the_sample_of_the_same_second) {
    std::vector<Sample> samples(3);
    samples[0].timeKey = 0x2026093007311200ull;
    samples[1].timeKey = 0x2026093007311300ull;     // same minute, other second
    samples[2].timeKey = 0x2026093013020000ull;
    auto unmatched = attachMeals(samples, {
        {0x2026093007311200ull, kMDC_CTXT_GLU_MEAL_FASTING},
        {0x2026093013020000ull, kMDC_CTXT_GLU_MEAL_POSTPRANDIAL},
        {0x2026093022000000ull, kMDC_CTXT_GLU_MEAL_BEDTIME},
    });
    CHECK_EQ(unmatched, 1u);
    CHECK_EQ(samples[0].meal, kMDC_CTXT_GLU_MEAL_FASTING);
    CHECK_EQ(samples[1].meal, 0);
    CHECK_EQ(samples[2].meal, kMDC_CTXT_GLU_MEAL_POSTPRANDIAL);
}

TEST(meal_names) {
    CHECK_EQ(std::string(mealName(29260)), std::string("before_meal"));
    CHECK_EQ(std::string(mealName(29264)), std::string("after_meal"));
    CHECK_EQ(std::string(mealName(29268)), std::string("fasting"));
    CHECK_EQ(std::string(mealName(29272)), std::string("casual"));
    CHECK_EQ(std::string(mealName(29300)), std::string("bedtime"));
    CHECK_EQ(std::string(mealName(12345)), std::string("other"));
}

TEST(sample_json_with_meal) {
    Sample s = {2021, 1, 15, 8, 0, 133, 0, true};
    s.meal = kMDC_CTXT_GLU_MEAL_FASTING;
    s.timeKey = 0x2021011508001700ull;
    CHECK_EQ(
        sampleJson(s, 0),
        std::string("{ \"id\":     0, \"epoch\": 1610694000, \"timestamp\":\"2021/01/15 08:00\", \"mg/dL\":133, \"mmol/L\": 7.4, \"status\":0, \"meal\":\"fasting\", \"key\":\"2021011508001700\" }")
    );
}

TEST(json_string_escapes) {
    CHECK_EQ(jsonString("Roche"), std::string("\"Roche\""));
    CHECK_EQ(jsonString("a\"b\\c\n\x01"), std::string("\"a\\\"b\\\\c\\u000a\\u0001\""));
    CHECK_EQ(jsonString(std::string("\xC3\xA9")), std::string("\"\\u00c3\\u00a9\""));
}

TEST(absolute_time_decoding) {
    sim::Bytes b = {0x20, 0x26, 0x10, 0x01, 0x21, 0x06, 0x58, 0x00};
    auto t = decodeAbsoluteTime(Reader(b.data(), b.size()));
    CHECK(t.valid);
    CHECK_EQ(formatTime(t), std::string("2026/10/01 21:06:58"));
    b[6] = 0x60;    // 60 seconds
    CHECK(!decodeAbsoluteTime(Reader(b.data(), b.size())).valid);
    CHECK(!decodeAbsoluteTime(Reader(b.data(), 7)).valid);
}

TEST(replay_trace_parsing) {
    ReplayTransport t("# comment\nc 0000\n< E2 00\n> !-7\n");
    CHECK_EQ(t.lines.size(), 3u);
    CHECK_EQ(t.lines[1].bytes.size(), 2u);
    CHECK_EQ(t.lines[2].error, -7);
    bool threw = false;
    try {
        ReplayTransport bad("< E2Z0\n");
    } catch(const std::runtime_error &) {
        threw = true;
    }
    CHECK(threw);
}
