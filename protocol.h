/*

    proprietary roche protocol: message builders and parsers, no I/O

    constants copied from:

        https://github.com/tidepool-org/uploader/tree/master/lib/drivers/roche

    these seem to be from the "Continua Health Alliance standard (ISO/IEEE 11073)"

    for the morbidly curious, see:
        https://en.wikipedia.org/wiki/Continua_Health_Alliance
        https://github.com/signove/antidote
        http://11073.org

 */

#ifndef __PROTOCOL_H__
    #define __PROTOCOL_H__

    #include <string>
    #include <vector>
    #include <time.h>
    #include <stddef.h>
    #include <stdint.h>
    #include <unordered_map>

    namespace accuchek {

    static constexpr size_t kBufferSize = 1024;

    static constexpr uint16_t kAPDU_TYPE_ASSOCIATION_REQUEST =             0xE200;
    static constexpr uint16_t kAPDU_TYPE_ASSOCIATION_RESPONSE =            0xE300;
    static constexpr uint16_t kAPDU_TYPE_ASSOCIATION_RELEASE_REQUEST =     0xE400;
    static constexpr uint16_t kAPDU_TYPE_ASSOCIATION_RELEASE_RESPONSE =    0xE500;
    static constexpr uint16_t kAPDU_TYPE_ASSOCIATION_ABORT =               0xE600;
    static constexpr uint16_t kAPDU_TYPE_PRESENTATION_APDU =               0xE700;

    static constexpr uint16_t kDATA_ADPU_INVOKE_GET =                      0x0103;
    static constexpr uint16_t kDATA_ADPU_INVOKE_CONFIRMED_ACTION =         0x0107;
    static constexpr uint16_t kDATA_ADPU_RESPONSE_CONFIRMED_EVENT_REPORT = 0x0201;
    static constexpr uint16_t kDATA_ADPU_RESPONSE_GET =                    0x0203;
    static constexpr uint16_t kDATA_ADPU_RESPONSE_CONFIRMED_ACTION =       0x0207;

    static constexpr uint16_t kEVENT_TYPE_MDC_NOTI_CONFIG =                0x0D1C;
    static constexpr uint16_t kEVENT_TYPE_MDC_NOTI_SEGMENT_DATA =          0x0D21;

    static constexpr uint16_t kACTION_TYPE_MDC_ACT_SEG_GET_INFO =          0x0C0D;
    static constexpr uint16_t kACTION_TYPE_MDC_ACT_SEG_GET_ID_LIST =       0x0C1E;
    static constexpr uint16_t kACTION_TYPE_MDC_ACT_SEG_TRIG_XFER =         0x0C1C;

    // data response of the segment transfer trigger (tidepool DATA_RESPONSE)
    static constexpr uint16_t kDATA_RESPONSE_EMPTY =                       0x0003;
    static constexpr uint16_t kACTION_TYPE_MDC_ACT_SEG_SET_TIME =          0x0C17;

    // ISO/IEEE 11073-10417 glucose nomenclature
    static constexpr uint16_t kMDC_CONC_GLU_CONTROL =                      29136;
    static constexpr uint16_t kMDC_CTXT_GLU_MEAL =                         29256;
    static constexpr uint16_t kMDC_CTXT_GLU_MEAL_PREPRANDIAL =             29260;
    static constexpr uint16_t kMDC_CTXT_GLU_MEAL_POSTPRANDIAL =            29264;
    static constexpr uint16_t kMDC_CTXT_GLU_MEAL_FASTING =                 29268;
    static constexpr uint16_t kMDC_CTXT_GLU_MEAL_CASUAL =                  29272;
    static constexpr uint16_t kMDC_CTXT_GLU_MEAL_BEDTIME =                 29300;

    // MdsTimeCapBits, bit 0 is the most significant one
    static constexpr uint16_t kMDS_TIME_CAPAB_SET_CLOCK =                  0x4000;
    // SegmEntryHeader: every entry starts with an 8 byte absolute time
    static constexpr uint16_t kSEG_ELEM_HDR_ABSOLUTE_TIME =                0x8000;

    #define MDC_LIST                                \
      x(MDC_MOC_VMO_METRIC, 4)                      \
      x(MDC_MOC_VMO_METRIC_ENUM, 5)                 \
      x(MDC_MOC_VMO_METRIC_NU, 6)                   \
      x(MDC_MOC_VMO_METRIC_SA_RT, 9)                \
      x(MDC_MOC_SCAN, 16)                           \
      x(MDC_MOC_SCAN_CFG, 17)                       \
      x(MDC_MOC_SCAN_CFG_EPI, 18)                   \
      x(MDC_MOC_SCAN_CFG_PERI, 19)                  \
      x(MDC_MOC_VMS_MDS_SIMP, 37)                   \
      x(MDC_MOC_VMO_PMSTORE, 61)                    \
      x(MDC_MOC_PM_SEGMENT, 62)                     \
      x(MDC_ATTR_CONFIRM_MODE, 2323)                \
      x(MDC_ATTR_CONFIRM_TIMEOUT, 2324)             \
      x(MDC_ATTR_TRANSPORT_TIMEOUT, 2694)           \
      x(MDC_ATTR_ID_HANDLE, 2337)                   \
      x(MDC_ATTR_ID_INSTNO, 2338)                   \
      x(MDC_ATTR_ID_LABEL_STRING, 2343)             \
      x(MDC_ATTR_ID_MODEL, 2344)                    \
      x(MDC_ATTR_ID_PHYSIO, 2347)                   \
      x(MDC_ATTR_ID_PROD_SPECN, 2349)               \
      x(MDC_ATTR_ID_TYPE, 2351)                     \
      x(MDC_ATTR_METRIC_STORE_CAPAC_CNT, 2369)      \
      x(MDC_ATTR_METRIC_STORE_SAMPLE_ALG, 2371)     \
      x(MDC_ATTR_METRIC_STORE_USAGE_CNT, 2372)      \
      x(MDC_ATTR_MSMT_STAT, 2375)                   \
      x(MDC_ATTR_NU_ACCUR_MSMT, 2378)               \
      x(MDC_ATTR_NU_CMPD_VAL_OBS, 2379)             \
      x(MDC_ATTR_NU_VAL_OBS, 2384)                  \
      x(MDC_ATTR_NUM_SEG, 2385)                     \
      x(MDC_ATTR_OP_STAT, 2387)                     \
      x(MDC_ATTR_POWER_STAT, 2389)                  \
      x(MDC_ATTR_SA_SPECN, 2413)                    \
      x(MDC_ATTR_SCALE_SPECN_I16, 2415)             \
      x(MDC_ATTR_SCALE_SPECN_I32, 2416)             \
      x(MDC_ATTR_SCALE_SPECN_I8, 2417)              \
      x(MDC_ATTR_SCAN_REP_PD, 2421)                 \
      x(MDC_ATTR_SEG_USAGE_CNT, 2427)               \
      x(MDC_ATTR_SYS_ID, 2436)                      \
      x(MDC_ATTR_SYS_TYPE, 2438)                    \
      x(MDC_ATTR_TIME_ABS, 2439)                    \
      x(MDC_ATTR_TIME_BATT_REMAIN, 2440)            \
      x(MDC_ATTR_TIME_END_SEG, 2442)                \
      x(MDC_ATTR_TIME_PD_SAMP, 2445)                \
      x(MDC_ATTR_TIME_REL, 2447)                    \
      x(MDC_ATTR_TIME_STAMP_ABS, 2448)              \
      x(MDC_ATTR_TIME_STAMP_REL, 2449)              \
      x(MDC_ATTR_TIME_START_SEG, 2450)              \
      x(MDC_ATTR_TX_WIND, 2453)                     \
      x(MDC_ATTR_UNIT_CODE, 2454)                   \
      x(MDC_ATTR_UNIT_LABEL_STRING, 2457)           \
      x(MDC_ATTR_VAL_BATT_CHARGE, 2460)             \
      x(MDC_ATTR_VAL_ENUM_OBS, 2462)                \
      x(MDC_ATTR_TIME_REL_HI_RES, 2536)             \
      x(MDC_ATTR_TIME_STAMP_REL_HI_RES, 2537)       \
      x(MDC_ATTR_DEV_CONFIG_ID, 2628)               \
      x(MDC_ATTR_MDS_TIME_INFO, 2629)               \
      x(MDC_ATTR_METRIC_SPEC_SMALL, 2630)           \
      x(MDC_ATTR_SOURCE_HANDLE_REF, 2631)           \
      x(MDC_ATTR_SIMP_SA_OBS_VAL, 2632)             \
      x(MDC_ATTR_ENUM_OBS_VAL_SIMP_OID, 2633)       \
      x(MDC_ATTR_ENUM_OBS_VAL_SIMP_STR, 2634)       \
      x(MDC_REG_CERT_DATA_LIST, 2635)               \
      x(MDC_ATTR_NU_VAL_OBS_BASIC, 2636)            \
      x(MDC_ATTR_PM_STORE_CAPAB, 2637)              \
      x(MDC_ATTR_PM_SEG_MAP, 2638)                  \
      x(MDC_ATTR_PM_SEG_PERSON_ID, 2639)            \
      x(MDC_ATTR_SEG_STATS, 2640)                   \
      x(MDC_ATTR_SEG_FIXED_DATA, 2641)              \
      x(MDC_ATTR_SCAN_HANDLE_ATTR_VAL_MAP, 2643)    \
      x(MDC_ATTR_SCAN_REP_PD_MIN, 2644)             \
      x(MDC_ATTR_ATTRIBUTE_VAL_MAP, 2645)           \
      x(MDC_ATTR_NU_VAL_OBS_SIMP, 2646)             \
      x(MDC_ATTR_PM_STORE_LABEL_STRING, 2647)       \
      x(MDC_ATTR_PM_SEG_LABEL_STRING, 2648)         \
      x(MDC_ATTR_TIME_PD_MSMT_ACTIVE, 2649)         \
      x(MDC_ATTR_SYS_TYPE_SPEC_LIST, 2650)          \
      x(MDC_ATTR_METRIC_ID_PART, 2655)              \
      x(MDC_ATTR_ENUM_OBS_VAL_PART, 2656)           \
      x(MDC_ATTR_SUPPLEMENTAL_TYPES, 2657)          \
      x(MDC_ATTR_TIME_ABS_ADJUST, 2658)             \
      x(MDC_ATTR_CLEAR_TIMEOUT, 2659)               \
      x(MDC_ATTR_TRANSFER_TIMEOUT, 2660)            \
      x(MDC_ATTR_ENUM_OBS_VAL_SIMP_BIT_STR, 2661)   \
      x(MDC_ATTR_ENUM_OBS_VAL_BASIC_BIT_STR, 2662)  \
      x(MDC_ATTR_METRIC_STRUCT_SMALL, 2675)         \
      x(MDC_ATTR_NU_CMPD_VAL_OBS_SIMP, 2676)        \
      x(MDC_ATTR_NU_CMPD_VAL_OBS_BASIC, 2677)       \
      x(MDC_ATTR_ID_PHYSIO_LIST, 2678)              \
      x(MDC_ATTR_SCAN_HANDLE_LIST, 2679)            \
      x(MDC_ATTR_TIME_BO, 2689)                     \
      x(MDC_ATTR_TIME_STAMP_BO, 2690)               \
      x(MDC_ATTR_TIME_START_SEG_BO, 2691)           \
      x(MDC_ATTR_TIME_END_SEG_BO, 2692)             \

    // all the MDC_* constants in one big enum
    enum MDC_ENUM {
        #define x(a, b) k##a = b,
            MDC_LIST
        #undef x
    };

    // get the name of a specific MDC_* constant as a string
    const char *findKeyByValue(uint16_t value);

    // config key value pair map: "vendor_0xVVVV_device_0xPPPP 1" enables a
    // device, "... 0" disables it (see config.txt)
    using Config = std::unordered_map<std::string, std::string>;
    Config parseConfig(const std::string &text);

    // devices known to work, enabled without any config file
    Config defaultConfig();

    // defaultConfig() with the entries of a config file on top
    Config configWithFile(const std::string &text);

    bool isDeviceAllowed(const Config &config, uint16_t vendorId, uint16_t productId);

    // "173a:21d5" for every enabled device, sorted
    std::vector<std::string> allowedDevices(const Config &config);

    // big endian helpers, writers shift ptr, readers shift offset
    void be16(uint8_t *&p, uint16_t v);
    void be32(uint8_t *&p, uint32_t v);
    uint16_t be16r(const uint8_t *p, size_t &offset);
    uint32_t be32r(const uint8_t *p, size_t &offset);

    // big endian reader that never reads past size: once a read would
    // overflow, ok turns false and every read returns 0
    struct Reader {
        const uint8_t *data;
        size_t size;
        size_t offset;
        bool ok;

        Reader(const uint8_t *_data, size_t _size, size_t _offset = 0)
            : data(_data), size(_size), offset(_offset), ok(_offset<=_size) {}

        bool has(size_t n) const { return ok && n<=size-offset; }
        bool skip(size_t n);
        uint8_t u8();
        uint16_t u16();
        uint32_t u32();

        // reader over the next n bytes, which are skipped here
        Reader sub(size_t n);
    };

    // outgoing messages, written at the start of buffer, return their size
    size_t buildAssociationResponse(uint8_t *buffer);
    size_t buildConfigReceived(uint8_t *buffer, uint16_t invokeId);
    size_t buildMdsRequest(uint8_t *buffer, uint16_t invokeId);
    size_t buildSegmentInfoRequest(uint8_t *buffer, uint16_t invokeId, uint16_t pmStoreHandle);
    size_t buildTriggerTransfer(uint8_t *buffer, uint16_t invokeId, uint16_t pmStoreHandle, uint16_t segment = 0);
    // set the meter clock (MDS object, handle 0) to a local date and time
    size_t buildSetTime(uint8_t *buffer, uint16_t invokeId, const struct tm &local);
    size_t buildSegmentAck(
        uint8_t *buffer,
        uint16_t invokeId,
        uint16_t pmStoreHandle,
        uint32_t u0,
        uint32_t u1,
        uint16_t u2
    );
    size_t buildReleaseRequest(uint8_t *buffer);

    // every parser below gets the number of bytes actually received and
    // returns false instead of reading past them

    // invoke id of an incoming presentation message
    bool readInvokeId(const uint8_t *buffer, size_t len, uint16_t &invokeId);

    // find pmStore handle and number of segments in the config info message
    bool parseConfigInfo(
        const uint8_t *buffer,
        size_t len,
        uint16_t &pmStoreHandle,
        uint16_t &nbSegments
    );

    // values the device stores instead of a number when the reading is off scale
    static constexpr uint16_t kValueHigh = 0x07FE;     // "HI": above the meter range
    static constexpr uint16_t kValueLow = 0x0802;      // "LO": below the meter range
    static constexpr int kReportedHigh = 601;           // tidepool convention: just above 600 mg/dL
    static constexpr int kReportedLow = 9;              // tidepool convention: just below 10 mg/dL

    // one blood glucose sample as stored by the device
    struct Sample {
        int year;
        int month;
        int day;
        int hour;
        int minute;
        uint16_t value;     // mg/dL
        uint16_t status;
        bool validDate;     // BCD digits and calendar ranges are sane
        uint64_t timeKey = 0;   // the 8 raw time bytes, shared with the meal entry of this sample
        uint16_t meal = 0;      // MDC_CTXT_GLU_MEAL_* code, 0 without a meal marker
    };

    // one data segment message
    struct Segment {
        uint32_t u0;        // echoed back in the ACK
        uint32_t u1;
        uint16_t u2;
        bool last;          // last segment in the stream
        std::vector<Sample> samples;
    };

    // one meal marker as stored by the device, in its own segment
    struct MealEntry {
        uint64_t timeKey;
        uint16_t meal;
    };

    struct MealSegment {
        uint32_t u0;
        uint32_t u1;
        uint16_t u2;
        bool last;
        std::vector<MealEntry> entries;
    };

    // decode the device's weird-ass encoding of datetime values (BCD), -1 if not BCD
    int decodeBcd(uint8_t x);

    // date and time as 8 BCD bytes (century, year, month, day, hour, minute, second, 1/100 s)
    struct AbsoluteTime {
        int year;
        int month;
        int day;
        int hour;
        int minute;
        int second;
        bool valid;
    };
    AbsoluteTime decodeAbsoluteTime(Reader r);

    // epoch of a meter date, computed from the device local time
    time_t localEpoch(const AbsoluteTime &t);

    // "2026/10/01 21:06:58"
    std::string formatTime(const AbsoluteTime &t);

    bool parseSegment(const uint8_t *buffer, size_t len, Segment &segment, std::string &error);
    bool parseMealSegment(const uint8_t *buffer, size_t len, MealSegment &segment, std::string &error);

    // identity and clock of the meter, from the answer to the MDS attribute request
    struct MeterInfo {
        std::string manufacturer;
        std::string model;
        std::string serial;
        std::string firmware;
        std::string hardware;
        std::string software;
        std::string systemId;       // EUI-64, hex
        bool hasClock = false;
        AbsoluteTime clock = {};
        bool clockSettable = false;
    };

    // false when the answer is malformed; a well formed answer may still lack attributes
    bool parseMdsAnswer(const uint8_t *buffer, size_t len, MeterInfo &info);

    // what a pm-segment holds, from the answer to the segment info request
    enum SegmentKind {
        kSegmentOther = 0,
        kSegmentGlucose,    // 12 byte entries: time, value, status
        kSegmentMeal,       // 10 byte entries: time, meal OID
    };

    struct SegmentInfo {
        uint16_t instance = 0;
        SegmentKind kind = kSegmentOther;
        std::string label;
        bool hasUsageCount = false;
        uint32_t usageCount = 0;    // entries stored in the segment
    };

    bool parseSegmentInfo(const uint8_t *buffer, size_t len, std::vector<SegmentInfo> &segments);

    // meal markers are attached to the glucose sample with the same raw time,
    // returns the number of markers without such a sample
    size_t attachMeals(std::vector<Sample> &samples, const std::vector<MealEntry> &meals);

    // "fasting", "before_meal"... for a MDC_CTXT_GLU_MEAL_* code, "other" for an unknown one
    const char *mealName(uint16_t meal);

    // epoch of a sample, computed from the device local time
    time_t sampleEpoch(const Sample &sample);

    // JSON object for one sample (no separator, no newline), every sample is
    // reported: "status" is the raw device status, off scale values get
    // "range":"high" or "range":"low" with mg/dL set to 601 or 9, samples
    // with an invalid date get null epoch/timestamp and an "error", samples
    // with a meal marker get "meal"
    std::string sampleJson(const Sample &sample, int id);

    // JSON string literal, quotes included, non printable bytes escaped
    std::string jsonString(const std::string &s);

    } // namespace accuchek

#endif // __PROTOCOL_H__
