/*

    simulated accuchek device: builds the packets a meter sends, laid out like
    the ones tidepool's driver and our parser read (ISO/IEEE 11073-20601 APDUs)
    and like the ones captured from an Accu-Chek Guide (model 925, firmware
    v1.9.6), and full session traces for ReplayTransport

 */

#ifndef __SIM_H__
    #define __SIM_H__

    #include <protocol.h>
    #include <trace.h>
    #include <string>
    #include <vector>
    #include <string.h>
    #include <stdint.h>
    #include <time.h>

    namespace sim {

    using Bytes = std::vector<uint8_t>;

    struct Writer {
        Bytes b;
        Writer &u8(uint8_t v) { b.push_back(v); return *this; }
        Writer &u16(uint16_t v) { u8(v >> 8); return u8(v & 0xFF); }
        Writer &u32(uint32_t v) { u16(v >> 16); return u16(v & 0xFFFF); }
        Writer &bytes(const Bytes &v) { b.insert(b.end(), v.begin(), v.end()); return *this; }
        Writer &octets(const std::string &s) { u16(s.size()); for(auto c : s) u8(c); return *this; }
        void set16(size_t at, uint16_t v) { b[at] = v >> 8; b[at+1] = v & 0xFF; }
    };

    inline uint8_t bcd(int v) {
        return uint8_t(((v / 10) << 4) | (v % 10));
    }

    struct Time {
        int year, month, day, hour, minute, second;
    };

    inline Bytes absoluteTime(const Time &t) {
        Writer w;
        w.u8(bcd(t.year / 100)).u8(bcd(t.year % 100)).u8(bcd(t.month)).u8(bcd(t.day));
        w.u8(bcd(t.hour)).u8(bcd(t.minute)).u8(bcd(t.second)).u8(0);
        return w.b;
    }

    // attribute: id, length, value
    inline Writer &attr(Writer &w, uint16_t id, const Bytes &value) {
        return w.u16(id).u16(value.size()).bytes(value);
    }

    inline Bytes u16Value(uint16_t v) { Writer w; w.u16(v); return w.b; }
    inline Bytes u32Value(uint32_t v) { Writer w; w.u32(v); return w.b; }

    // presentation APDU header with its three length fields patched by finish()
    inline Writer apdu(uint16_t invokeId, uint16_t choice) {
        Writer w;
        w.u16(accuchek::kAPDU_TYPE_PRESENTATION_APDU).u16(0).u16(0).u16(invokeId).u16(choice).u16(0);
        return w;
    }

    inline Bytes finish(Writer &w) {
        auto n = w.b.size();
        w.set16(2, n - 4);
        w.set16(4, n - 6);
        w.set16(10, n - 12);
        return w.b;
    }

    inline Bytes pairingRequest() {
        Writer w;
        w.u16(accuchek::kAPDU_TYPE_ASSOCIATION_REQUEST).u16(50).u32(0x80000000).u16(1).u16(42);
        while(w.b.size()<54) w.u8(0);
        return w.b;
    }

    inline Bytes configInfo(uint16_t invokeId, uint16_t pmStoreHandle, uint16_t nbSegments) {
        auto w = apdu(invokeId, 0x0101);
        w.u16(0).u32(0xFFFFFFFF).u16(accuchek::kEVENT_TYPE_MDC_NOTI_CONFIG).u16(0);
        w.u16(0x4000);                      // config report id
        w.u16(2).u16(0);                    // object count, length
        // a numeric metric first, so the parser has to skip it
        w.u16(accuchek::kMDC_MOC_VMO_METRIC_NU).u16(1).u16(1).u16(6);
        w.u16(accuchek::kMDC_ATTR_ID_TYPE).u16(2).u16(0x7144);
        // the pm-store holding the samples
        w.u16(accuchek::kMDC_MOC_VMO_PMSTORE).u16(pmStoreHandle).u16(2).u16(12);
        w.u16(accuchek::kMDC_ATTR_ID_HANDLE).u16(2).u16(pmStoreHandle);
        w.u16(accuchek::kMDC_ATTR_NUM_SEG).u16(2).u16(nbSegments);
        auto b = finish(w);
        w.b = b;
        w.set16(20, b.size() - 22);
        w.set16(26, b.size() - 28);
        return w.b;
    }

    // identity and clock of a meter, laid out like the Accu-Chek Guide 925 answer
    struct Meter {
        std::string manufacturer = "Roche";
        std::string model = "925";
        std::string serial = "92500000042";
        std::string firmware = "v1.9.6";
        std::string hardware = "G";
        Time clock = {2026, 10, 1, 21, 6, 58};
        bool settable = true;
    };

    // a meter that gives nothing in its MDS answer (old simulator, unknown models)
    inline Bytes mdsAnswer(uint16_t invokeId) {
        auto w = apdu(invokeId, accuchek::kDATA_ADPU_RESPONSE_GET);
        w.u16(0).u16(0).u16(0);
        return finish(w);
    }

    inline Bytes mdsAnswer(uint16_t invokeId, const Meter &m) {
        Writer model;
        model.octets(m.manufacturer + std::string(1, '\0')).octets(m.model + std::string(1, '\0'));
        Writer sysId;
        sysId.u16(8).u32(0x00601900).u32(0x00000042);
        Writer spec;
        Writer entries;
        entries.u16(1).u16(0).octets("serial-number: " + m.serial);
        entries.u16(3).u16(0x1000).octets("hw-revision: " + m.hardware + std::string(4, '\0'));
        entries.u16(5).u16(0).octets("fw-revision: " + m.firmware + std::string(1, '\0'));
        spec.u16(3).u16(entries.b.size()).bytes(entries.b);
        Writer timeInfo;
        timeInfo.u16(m.settable ? 0xC000 : 0x8000).u16(0x1F00).u32(0xFFFFFFFF).u16(0x0064).u16(0).u32(0);

        Writer attrs;
        attr(attrs, accuchek::kMDC_ATTR_ID_MODEL, model.b);
        attr(attrs, accuchek::kMDC_ATTR_SYS_ID, sysId.b);
        attr(attrs, accuchek::kMDC_ATTR_DEV_CONFIG_ID, u16Value(0x4000));
        attr(attrs, accuchek::kMDC_ATTR_ID_PROD_SPECN, spec.b);
        attr(attrs, accuchek::kMDC_ATTR_TIME_ABS, absoluteTime(m.clock));
        attr(attrs, accuchek::kMDC_ATTR_MDS_TIME_INFO, timeInfo.b);

        auto w = apdu(invokeId, accuchek::kDATA_ADPU_RESPONSE_GET);
        w.u16(0).u16(6).u16(attrs.b.size()).bytes(attrs.b);
        return finish(w);
    }

    // segment entry map: absolute time then one element and its attributes
    inline Bytes segmentMap(
        uint16_t objClass,
        uint16_t partition,
        uint16_t code,
        uint16_t handle,
        const std::vector<std::pair<uint16_t, uint16_t>> &attrs
    ) {
        Writer list;
        for(const auto &a : attrs) list.u16(a.first).u16(a.second);
        Writer element;
        element.u16(objClass).u16(partition).u16(code).u16(handle).u16(attrs.size()).u16(list.b.size()).bytes(list.b);
        Writer w;
        w.u16(accuchek::kSEG_ELEM_HDR_ABSOLUTE_TIME).u16(1).u16(element.b.size()).bytes(element.b);
        return w.b;
    }

    struct SegmentSpec {
        uint16_t instance;
        accuchek::SegmentKind kind;
        std::string label;
        uint32_t usageCount;
    };

    // the four segments of an Accu-Chek Guide: glucose, control, errors, meal markers
    inline std::vector<SegmentSpec> guideSegments(uint32_t glucose, uint32_t meals) {
        return {
            {0, accuchek::kSegmentGlucose, "PMSegGluc ", glucose},
            {1, accuchek::kSegmentOther, "PMSegCtrl ", 0},
            {2, accuchek::kSegmentOther, "PMSegError", 0},
            {3, accuchek::kSegmentMeal, "PMSegCMeal", meals},
        };
    }

    inline Bytes segmentInfoResponse(uint16_t invokeId, uint16_t pmStoreHandle, const std::vector<SegmentSpec> &segments) {
        using namespace accuchek;
        Writer list;
        for(const auto &s : segments) {
            Bytes map;
            switch(s.kind) {
                case kSegmentGlucose:
                    map = segmentMap(kMDC_MOC_VMO_METRIC_NU, 2, 0x7270, 1, {{kMDC_ATTR_NU_VAL_OBS_BASIC, 2}, {kMDC_ATTR_MSMT_STAT, 2}});
                    break;
                case kSegmentMeal:
                    map = segmentMap(kMDC_MOC_VMO_METRIC_ENUM, 128, kMDC_CTXT_GLU_MEAL, 4, {{kMDC_ATTR_ENUM_OBS_VAL_SIMP_OID, 2}});
                    break;
                default:
                    map = segmentMap(kMDC_MOC_VMO_METRIC_NU, 2, kMDC_CONC_GLU_CONTROL, 2, {{kMDC_ATTR_NU_VAL_OBS_BASIC, 2}, {kMDC_ATTR_MSMT_STAT, 2}});
                    break;
            }
            Writer label;
            label.octets(s.label);
            Writer attrs;
            attr(attrs, kMDC_ATTR_ID_INSTNO, u16Value(s.instance));
            attr(attrs, kMDC_ATTR_PM_SEG_MAP, map);
            attr(attrs, kMDC_ATTR_OP_STAT, u16Value(0));
            attr(attrs, kMDC_ATTR_PM_SEG_LABEL_STRING, label.b);
            attr(attrs, kMDC_ATTR_SEG_USAGE_CNT, u32Value(s.usageCount));
            list.u16(s.instance).u16(5).u16(attrs.b.size()).bytes(attrs.b);
        }
        auto w = apdu(invokeId, kDATA_ADPU_RESPONSE_CONFIRMED_ACTION);
        w.u16(pmStoreHandle).u16(kACTION_TYPE_MDC_ACT_SEG_GET_INFO).u16(0);
        w.u16(segments.size()).u16(list.b.size()).bytes(list.b);
        auto b = finish(w);
        w.b = b;
        w.set16(16, b.size() - 18);
        return w.b;
    }

    // segment info answer listing no segment (old simulator)
    inline Bytes actionResponse(uint16_t invokeId, uint16_t pmStoreHandle) {
        auto w = apdu(invokeId, accuchek::kDATA_ADPU_RESPONSE_CONFIRMED_ACTION);
        w.u16(pmStoreHandle).u16(accuchek::kACTION_TYPE_MDC_ACT_SEG_GET_INFO).u16(0);
        return finish(w);
    }

    // answer of an Accu-Chek Guide to the set time action, or a remote operation error
    inline Bytes setTimeAnswer(uint16_t invokeId, bool accepted = true) {
        if(!accepted) {
            auto w = apdu(invokeId, 0x0300);    // roer: remote operation error
            w.u16(3);                           // not-allowed-by-object
            w.u16(0);
            return finish(w);
        }
        auto w = apdu(invokeId, accuchek::kDATA_ADPU_RESPONSE_CONFIRMED_ACTION);
        w.u16(0).u16(accuchek::kACTION_TYPE_MDC_ACT_SEG_SET_TIME).u16(0);
        return finish(w);
    }

    inline Bytes segmentHeaders(uint16_t invokeId, uint16_t pmStoreHandle, uint16_t result = 0) {
        auto w = apdu(invokeId, accuchek::kDATA_ADPU_RESPONSE_CONFIRMED_ACTION);
        w.u16(pmStoreHandle).u16(accuchek::kACTION_TYPE_MDC_ACT_SEG_TRIG_XFER).u16(4).u16(0).u16(result);
        return finish(w);
    }

    struct Record {
        int year, month, day, hour, minute;
        uint16_t value;
        uint16_t status;
        int second = 0;
    };

    struct MealRecord {
        int year, month, day, hour, minute, second;
        uint16_t meal;
    };

    inline Bytes segmentEvent(
        uint16_t invokeId,
        uint16_t pmStoreHandle,
        uint32_t firstIndex,
        uint32_t count,
        const Bytes &entries,
        bool first,
        bool last
    ) {
        auto w = apdu(invokeId, 0x0101);
        w.u16(pmStoreHandle).u32(0xFFFFFFFF).u16(accuchek::kEVENT_TYPE_MDC_NOTI_SEGMENT_DATA).u16(0);
        w.u16(0).u32(firstIndex).u32(count);
        w.u16((first ? 0x8000 : 0) | (last ? 0x4000 : 0));
        w.u16(entries.size());
        w.bytes(entries);
        auto b = finish(w);
        w.b = b;
        w.set16(20, b.size() - 22);
        return w.b;
    }

    inline Bytes dataSegment(
        uint16_t invokeId,
        uint16_t pmStoreHandle,
        uint32_t firstIndex,
        const std::vector<Record> &records,
        bool first,
        bool last
    ) {
        Writer e;
        for(const auto &r : records) {
            e.bytes(absoluteTime({r.year, r.month, r.day, r.hour, r.minute, r.second}));
            e.u16(r.value).u16(r.status);
        }
        return segmentEvent(invokeId, pmStoreHandle, firstIndex, records.size(), e.b, first, last);
    }

    inline Bytes mealSegment(
        uint16_t invokeId,
        uint16_t pmStoreHandle,
        uint32_t firstIndex,
        const std::vector<MealRecord> &records,
        bool first,
        bool last
    ) {
        Writer e;
        for(const auto &r : records) {
            e.bytes(absoluteTime({r.year, r.month, r.day, r.hour, r.minute, r.second}));
            e.u16(r.meal);
        }
        return segmentEvent(invokeId, pmStoreHandle, firstIndex, records.size(), e.b, first, last);
    }

    inline Bytes releaseResponse() {
        Writer w;
        w.u16(accuchek::kAPDU_TYPE_ASSOCIATION_RELEASE_RESPONSE).u16(2).u16(0);
        return w.b;
    }

    inline std::string line(char kind, const Bytes &b) {
        return std::string(1, kind) + " " + accuchek::toHex(b.data(), b.size()) + "\n";
    }

    // what our side must send, computed with the builders under test
    inline Bytes sent(size_t (*build)(uint8_t *)) {
        uint8_t buf[accuchek::kBufferSize];
        auto n = build(buf);
        return Bytes(buf, buf + n);
    }

    template<typename F>
    inline Bytes sentWith(F build) {
        uint8_t buf[accuchek::kBufferSize];
        auto n = build(buf);
        return Bytes(buf, buf + n);
    }

    // everything a simulated session holds
    struct Session {
        std::vector<std::vector<Record>> glucose;   // one inner vector per data segment
        std::vector<std::vector<MealRecord>> meals; // same, for the meal marker segment
        bool describe = true;       // MDS identity and clock, segment map; false: old meters
        Meter meter;
        bool setTime = false;       // the session sets the meter clock to now
        Time now = {2026, 10, 1, 20, 42, 52};
        bool setTimeAccepted = true;
        uint16_t pmStoreHandle = 0x0100;
        int glucoseCountError = 0;  // added to the glucose usage count the meter announces
    };

    template<typename Records, typename Build>
    inline std::string transfer(
        const std::vector<Records> &segments,
        uint16_t &invokeId,
        uint16_t &meterInvokeId,
        uint16_t pmStoreHandle,
        uint16_t instance,
        Build build
    ) {
        std::string t;
        auto request = invokeId;
        t += line('>', sentWith([&](uint8_t *b) { return accuchek::buildTriggerTransfer(b, request, pmStoreHandle, instance); }));
        invokeId = request + 1;
        t += line('<', segmentHeaders(invokeId, pmStoreHandle, segments.empty() ? accuchek::kDATA_RESPONSE_EMPTY : 0));
        uint32_t index = 0;
        for(size_t k=0; k<segments.size(); ++k) {
            auto id = meterInvokeId++;
            auto seg = build(id, pmStoreHandle, index, segments[k], 0==k, k+1==segments.size());
            index += segments[k].size();
            t += line('<', seg);
            size_t o = 22;
            auto u0 = accuchek::be32r(seg.data(), o);
            auto u1 = accuchek::be32r(seg.data(), o);
            auto u2 = accuchek::be16r(seg.data(), o);
            t += line('>', sentWith([&](uint8_t *b) {
                return accuchek::buildSegmentAck(b, id, pmStoreHandle, u0, u1, u2);
            }));
        }
        return t;
    }

    inline uint32_t total(const std::vector<std::vector<Record>> &segments) {
        uint32_t n = 0;
        for(const auto &s : segments) n += s.size();
        return n;
    }

    inline uint32_t total(const std::vector<std::vector<MealRecord>> &segments) {
        uint32_t n = 0;
        for(const auto &s : segments) n += s.size();
        return n;
    }

    inline struct tm localTm(const Time &t) {
        struct tm tm;
        memset(&tm, 0, sizeof(tm));
        tm.tm_year = t.year - 1900;
        tm.tm_mon = t.month - 1;
        tm.tm_mday = t.day;
        tm.tm_hour = t.hour;
        tm.tm_min = t.minute;
        tm.tm_sec = t.second;
        tm.tm_isdst = -1;
        return tm;
    }

    // "--set-time --now ..." for a session that sets the clock
    inline std::string replayArgs(const Session &s) {
        if(!s.setTime) {
            return "";
        }
        char buf[64];
        snprintf(buf, sizeof(buf), "--set-time --now \"%04d/%02d/%02d %02d:%02d:%02d\"",
            s.now.year, s.now.month, s.now.day, s.now.hour, s.now.minute, s.now.second);
        return buf;
    }

    // trace of a complete session; replay it with replayArgs(s), which the
    // trace also records in its "# args:" comment for external replay tools
    inline std::string sessionTrace(const Session &s) {
        auto handle = s.pmStoreHandle;
        auto args = replayArgs(s);
        std::string t = "# simulated session\n";
        if(!args.empty()) {
            t += "# args: " + args + "\n";
        }
        t += "c 0000\n";
        t += line('<', pairingRequest());
        t += line('>', sent(accuchek::buildAssociationResponse));
        t += line('<', configInfo(0x0010, handle, s.describe ? 4 : 1));
        t += line('>', sentWith([](uint8_t *b) { return accuchek::buildConfigReceived(b, 0x0010); }));
        t += line('>', sentWith([](uint8_t *b) { return accuchek::buildMdsRequest(b, 0x0010); }));
        t += line('<', s.describe ? mdsAnswer(0x0011, s.meter) : mdsAnswer(0x0011));
        t += line('>', sentWith([&](uint8_t *b) { return accuchek::buildSegmentInfoRequest(b, 0x0011, handle); }));
        auto segments = guideSegments(uint32_t(int(total(s.glucose)) + s.glucoseCountError), total(s.meals));
        t += line('<', s.describe ? segmentInfoResponse(0x0012, handle, segments) : actionResponse(0x0012, handle));
        uint16_t invokeId = 0x0012;
        if(s.setTime) {
            auto now = localTm(s.now);
            mktime(&now);
            t += line('>', sentWith([&](uint8_t *b) { return accuchek::buildSetTime(b, invokeId, now); }));
            invokeId += 1;
            t += line('<', setTimeAnswer(invokeId, s.setTimeAccepted));
        }
        uint16_t meterInvokeId = 0x0020;
        t += transfer(s.glucose, invokeId, meterInvokeId, handle, 0, dataSegment);
        if(s.describe && !s.meals.empty()) {
            t += transfer(s.meals, invokeId, meterInvokeId, handle, 3, mealSegment);
        }
        t += line('>', sent(accuchek::buildReleaseRequest));
        t += line('<', releaseResponse());
        return t;
    }

    // trace of a complete session of a meter without meal markers, one inner
    // vector per data segment, no segment at all for an empty meter
    inline std::string sessionTrace(
        const std::vector<std::vector<Record>> &segments,
        uint16_t pmStoreHandle = 0x0100
    ) {
        Session s;
        s.glucose = segments;
        s.pmStoreHandle = pmStoreHandle;
        return sessionTrace(s);
    }

    } // namespace sim

#endif // __SIM_H__
