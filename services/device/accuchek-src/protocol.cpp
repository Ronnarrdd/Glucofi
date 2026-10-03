#include <log.h>
#include <protocol.h>
#include <stdio.h>
#include <string.h>
#include <inttypes.h>
#include <cmath>
#include <algorithm>

namespace accuchek {

const char *findKeyByValue(
    uint16_t value
) {
    #define x(a, b) if((b)==value) return #a;
        MDC_LIST
    #undef x
    return (const char *)0;
}

// one "key value" pair per line, anything after the value is ignored
Config parseConfig(
    const std::string &text
) {
    Config config;
    size_t start = 0;
    while(start<text.size()) {

        auto nl = text.find('\n', start);
        auto stop = (std::string::npos==nl ? text.size() : (1 + nl));
        auto line = std::string(text, start, stop - start) + '\n';    // last line may lack its newline
        start = stop;
        const char *end = line.data() + line.size();

        const char *firstSep = 0;
        const char *secondSep = 0;
        const char *secondFirst = 0;
        for(const char *p = line.data(); p<end; ++p) {
            auto c = p[0];
            auto validChar = (
                ('0'<=c && c<='9')  ||
                ('A'<=c && c<='Z')  ||
                ('a'<=c && c<='z')  ||
                ('_'==c)
            );
            if(false==validChar) {
                if(0==firstSep) {
                    firstSep = p;
                } else {
                    if(0==secondSep && 0!=secondFirst) {
                        secondSep = p;
                    }
                }
            } else {
                if(0!=firstSep) {
                    if(0==secondFirst) {
                        secondFirst = p;
                    }
                }
            }
        }
        if(0==firstSep || 0==secondFirst || 0==secondSep) {
            continue;
        }
        config[std::string((const char *)line.data(), firstSep)] = std::string(secondFirst, secondSep);
    }
    return config;
}

Config defaultConfig() {
    return {
        {"vendor_0x173a_device_0x21d5", "1"},   // roche accu-chek guide, model 929
        {"vendor_0x173a_device_0x21d7", "1"},   // similar model, same protocol
        {"vendor_0x173a_device_0x21d8", "1"},   // roche relion platinum, model 982
    };
}

Config configWithFile(
    const std::string &text
) {
    auto config = defaultConfig();
    for(const auto &kv : parseConfig(text)) {
        config[kv.first] = kv.second;
    }
    return config;
}

std::vector<std::string> allowedDevices(
    const Config &config
) {
    std::vector<std::string> devices;
    for(const auto &kv : config) {
        unsigned vendorId = 0;
        unsigned productId = 0;
        if(2==sscanf(kv.first.c_str(), "vendor_0x%4x_device_0x%4x", &vendorId, &productId) &&
                isDeviceAllowed(config, vendorId, productId)) {
            char buf[16];
            snprintf(buf, sizeof(buf), "%04x:%04x", vendorId, productId);
            devices.push_back(buf);
        }
    }
    std::sort(devices.begin(), devices.end());
    return devices;
}

bool isDeviceAllowed(
    const Config &config,
    uint16_t vendorId,
    uint16_t productId
) {
    char key[64];
    snprintf(
        key,
        sizeof(key),
        "vendor_0x%04x_device_0x%04x",
        vendorId,
        productId
    );
    auto it = config.find(key);
    return (config.end()!=it && "1"==it->second);
}

void be16(
    uint8_t *&p,
    uint16_t v
) {
    p[0] = (v >> 8) & 0xFF;
    p[1] = (v >> 0) & 0xFF;
    p += 2;
}

void be32(
    uint8_t *&p,
    uint32_t v
) {
    p[0] = (v >> 24) & 0xFF;
    p[1] = (v >> 16) & 0xFF;
    p[2] = (v >>  8) & 0xFF;
    p[3] = (v >>  0) & 0xFF;
    p += 4;
}

uint16_t be16r(
    const uint8_t *p,
    size_t &offset
) {
    auto hi = p[0 + offset];
    auto lo = p[1 + offset];
    offset += 2;
    return (((uint16_t)hi)<<8) | lo;
}

uint32_t be32r(
    const uint8_t *p,
    size_t &offset
) {
    uint32_t p0 = p[0 + offset];
    uint32_t p1 = p[1 + offset];
    uint32_t p2 = p[2 + offset];
    uint32_t p3 = p[3 + offset];
    offset += 4;
    return (
        (p0 << 24)  |
        (p1 << 16)  |
        (p2 <<  8)  |
        (p3 <<  0)
    );
}

bool Reader::skip(
    size_t n
) {
    if(!has(n)) {
        ok = false;
        return false;
    }
    offset += n;
    return true;
}

uint8_t Reader::u8() {
    if(!has(1)) {
        ok = false;
        return 0;
    }
    return data[offset++];
}

uint16_t Reader::u16() {
    if(!has(2)) {
        ok = false;
        return 0;
    }
    return be16r(data, offset);
}

uint32_t Reader::u32() {
    if(!has(4)) {
        ok = false;
        return 0;
    }
    return be32r(data, offset);
}

Reader Reader::sub(
    size_t n
) {
    if(!has(n)) {
        ok = false;
        return Reader(data, 0, 1);
    }
    Reader r(data + offset, n);
    offset += n;
    return r;
}

size_t buildAssociationResponse(
    uint8_t *buffer
) {
    auto p = buffer;
    memset(buffer, 0, kBufferSize);
    be16(p, kAPDU_TYPE_ASSOCIATION_RESPONSE); // msg type
    be16(p,         44);                      // length (p, excludes initial 4 bytes)
    be16(p,     0x0003);                      // accepted-unknown-config
    be16(p,      20601);                      // data-proto-id
    be16(p,         38);                      // data-proto-info length
    be32(p, 0x80000002);                      // protocolVersion
    be16(p,     0x8000);                      // encoding-rules = MDER
    be32(p, 0x80000000);                      // nomenclatureVersion
    be32(p,          0);                      // functionalUnits = normal association
    be32(p, 0x80000000);                      // systemType = sys-type-manager
    be16(p,          8);                      // system-id length
    be32(p, 0x12345678);                      // system-id high
    be32(p, 0x00000000);                      // zero
    be32(p, 0x00000000);                      // zero
    be32(p, 0x00000000);                      // zero
    be16(p,     0x0000);                      // zero
    return (p-buffer);
}

size_t buildConfigReceived(
    uint8_t *buffer,
    uint16_t invokeId
) {
    auto p = buffer;
    memset(buffer, 0, kBufferSize);
    be16(p, kAPDU_TYPE_PRESENTATION_APDU);               // msg type
    be16(p,     22);                                     // length
    be16(p,     20);                                     // octet stringlength
    be16(p, invokeId);                                   // invoke-id read from config
    be16(p, kDATA_ADPU_RESPONSE_CONFIRMED_EVENT_REPORT); //
    be16(p,     14);                                     // length
    be16(p,      0);                                     // obj-handle = 0
    be32(p,      0);                                     // currentTime = 0
    be16(p, kEVENT_TYPE_MDC_NOTI_CONFIG);                // event-type
    be16(p,      4);                                     // length
    be16(p, 0x4000);                                     // config-report-id = extended-config-start
    be16(p,      0);                                     // config-result = accepted-config
    return (p-buffer);
}

size_t buildMdsRequest(
    uint8_t *buffer,
    uint16_t invokeId
) {
    auto p = buffer;
    memset(buffer, 0, kBufferSize);
    be16(p, kAPDU_TYPE_PRESENTATION_APDU); // msg type
    be16(p,     14);                       // length
    be16(p,     12);                       // octet stringlength
    be16(p, (1+invokeId));                 // invoke-id from config
    be16(p, kDATA_ADPU_INVOKE_GET);        //
    be16(p,      6);                       // length
    be16(p,      0);                       // obj-handle = 0
    be32(p,      0);                       // currentTime = 0
    return (p-buffer);
}

size_t buildSegmentInfoRequest(
    uint8_t *buffer,
    uint16_t invokeId,
    uint16_t pmStoreHandle
) {
    auto p = buffer;
    memset(buffer, 0, kBufferSize);
    be16(p, kAPDU_TYPE_PRESENTATION_APDU); // msg type
    be16(p,     20);                       // length
    be16(p,     18);                       // octet stringlength
    be16(p, (1+invokeId));                 // invoke-id from prev answer
    be16(p, kDATA_ADPU_INVOKE_CONFIRMED_ACTION);
    be16(p,     12);                       // length of what follows (could also be zero)
    be16(p, pmStoreHandle);                // store handle
    be16(p, kACTION_TYPE_MDC_ACT_SEG_GET_INFO);
    be16(p,      6);                       // length
    be16(p,      1);                       // all segments
    be16(p,      2);                       // length
    be16(p,      0);                       // something
    return (p-buffer);
}

size_t buildTriggerTransfer(
    uint8_t *buffer,
    uint16_t invokeId,
    uint16_t pmStoreHandle,
    uint16_t segment
) {
    auto p = buffer;
    memset(buffer, 0, kBufferSize);
    be16(p, kAPDU_TYPE_PRESENTATION_APDU); // msg type
    be16(p,     16);                       // length
    be16(p,     14);                       // octet stringlength
    be16(p, (1+invokeId));                 // invoke-id from prev answer
    be16(p, kDATA_ADPU_INVOKE_CONFIRMED_ACTION);
    be16(p,      8);                       // length of what follows (could also be zero)
    be16(p, pmStoreHandle);                // store handle
    be16(p, kACTION_TYPE_MDC_ACT_SEG_TRIG_XFER);
    be16(p,      2);                       // length
    be16(p, segment);                      // segment instance number
    return (p-buffer);
}

static uint8_t bcd(
    int v
) {
    return uint8_t(((v / 10) << 4) | (v % 10));
}

size_t buildSetTime(
    uint8_t *buffer,
    uint16_t invokeId,
    const struct tm &local
) {
    auto p = buffer;
    memset(buffer, 0, kBufferSize);
    auto year = local.tm_year + 1900;
    be16(p, kAPDU_TYPE_PRESENTATION_APDU); // msg type
    be16(p,     26);                       // length
    be16(p,     24);                       // octet stringlength
    be16(p, (1+invokeId));                 // invoke-id from prev answer
    be16(p, kDATA_ADPU_INVOKE_CONFIRMED_ACTION);
    be16(p,     18);                       // length of what follows
    be16(p,      0);                       // MDS object handle
    be16(p, kACTION_TYPE_MDC_ACT_SEG_SET_TIME);
    be16(p,     12);                       // length
    *p++ = bcd(year / 100);                // AbsoluteTime, BCD
    *p++ = bcd(year % 100);
    *p++ = bcd(local.tm_mon + 1);
    *p++ = bcd(local.tm_mday);
    *p++ = bcd(local.tm_hour);
    *p++ = bcd(local.tm_min);
    *p++ = bcd(std::min(local.tm_sec, 59)); // leap second
    *p++ = 0;                              // hundredths
    be32(p,      0);                       // accuracy (FLOAT-Type), unknown
    return (p-buffer);
}

size_t buildSegmentAck(
    uint8_t *buffer,
    uint16_t invokeId,
    uint16_t pmStoreHandle,
    uint32_t u0,
    uint32_t u1,
    uint16_t u2
) {
    auto p = buffer;
    memset(buffer, 0, kBufferSize);
    be16(p, kAPDU_TYPE_PRESENTATION_APDU); // msg type
    be16(p,     30);                       // length
    be16(p,     28);                       // octet stringlength
    be16(p, invokeId);                     // invoke-id from prev answer
    be16(p, kDATA_ADPU_RESPONSE_CONFIRMED_EVENT_REPORT);
    be16(p,     22);                       // length of what follows (could also be zero)
    be16(p, pmStoreHandle);                // store handle
    be32(p, 0xFFFFFFFF);                   // relative time
    be16(p, kEVENT_TYPE_MDC_NOTI_SEGMENT_DATA);
    be16(p,     12);
    be32(p,     u0);
    be32(p,     u1);
    be16(p,     u2);
    be16(p, 0x0080);
    return (p-buffer);
}

size_t buildReleaseRequest(
    uint8_t *buffer
) {
    auto p = buffer;
    memset(buffer, 0, kBufferSize);
    be16(p, kAPDU_TYPE_ASSOCIATION_RELEASE_REQUEST); // msg type
    be16(p,      2);                       // length = 2
    be16(p, 0x0000);                       // normal release
    return (p-buffer);
}

bool readInvokeId(
    const uint8_t *buffer,
    size_t len,
    uint16_t &invokeId
) {
    Reader r(buffer, len, 6);
    invokeId = r.u16();
    return r.ok;
}

// find object of a given "class" in a config info message, returns a reader over its attributes
static bool findObject(
    Reader &msg,
    uint16_t objRequestedClass,
    uint16_t &objHandle,
    uint16_t &objAttrCount,
    Reader &attributes
) {
    msg.skip(24);
    auto count = msg.u16();
    msg.u16();
    if(!msg.ok) {
        return false;
    }
    LOG_NFO("got %d object in config info response", (int)count);
    for(int i=0; i<count && msg.ok; ++i) {
        auto objClass = msg.u16();
        auto handle = msg.u16();
        auto attrCount = msg.u16();
        auto objSize = msg.u16();
        auto obj = msg.sub(objSize);
        if(msg.ok && objRequestedClass==objClass) {
            objHandle = handle;
            objAttrCount = attrCount;
            attributes = obj;
            return true;
        }
    }
    return false;
}

// find attribute of a given "class" in an object, returns a reader over its value
static bool findAttribute(
    Reader object,
    uint16_t attributeCount,
    uint16_t attrRequestedClass,
    Reader &value
) {
    LOG_NFO(
        "looking for attribute of class %d among %d attributes",
        (int)attrRequestedClass,
        (int)attributeCount
    );
    for(int i=0; i<attributeCount && object.ok; ++i) {
        auto attrClass = object.u16();
        auto attrSize = object.u16();
        auto attr = object.sub(attrSize);
        if(object.ok && attrRequestedClass==attrClass) {
            value = attr;
            return true;
        }
    }
    return false;
}

bool parseConfigInfo(
    const uint8_t *buffer,
    size_t len,
    uint16_t &pmStoreHandle,
    uint16_t &nbSegments
) {
    LOG_NFO("parsing config info response");
    Reader msg(buffer, len);
    Reader pmStore(buffer, 0);
    uint16_t attrCount = 0;
    if(!findObject(msg, kMDC_MOC_VMO_PMSTORE, pmStoreHandle, attrCount, pmStore)) {
        LOG_WRN("failed to parse config buffer for pmStore");
        return false;
    }
    LOG_NFO(
        "found pmStore with %d attributes, handle = %d",
        (int)attrCount,
        (int)pmStoreHandle
    );

    Reader numSeg(buffer, 0);
    if(!findAttribute(pmStore, attrCount, kMDC_ATTR_NUM_SEG, numSeg)) {
        LOG_WRN("failed to parse pmStore for nbSegments");
        return false;
    }
    nbSegments = numSeg.u16();
    if(!numSeg.ok) {
        LOG_WRN("nbSegments attribute is too short");
        return false;
    }
    LOG_NFO("data is split into %d segments", (int)nbSegments);
    return true;
}

int decodeBcd(
    uint8_t x
) {
    auto hi = (x >> 4);
    auto lo = (x & 0xF);
    if(9<hi || 9<lo) {
        return -1;
    }
    return 10*hi + lo;
}

bool isValidDate(
    const Sample &s
) {
    return (
        0<=s.year &&
        1<=s.month && s.month<=12 &&
        1<=s.day && s.day<=31 &&
        0<=s.hour && s.hour<=23 &&
        0<=s.minute && s.minute<=59
    );
}

AbsoluteTime decodeAbsoluteTime(
    Reader r
) {
    AbsoluteTime t;
    auto cc = decodeBcd(r.u8());
    auto yy = decodeBcd(r.u8());
    t.year = (cc<0 || yy<0) ? -1 : (cc*100 + yy);
    t.month = decodeBcd(r.u8());
    t.day = decodeBcd(r.u8());
    t.hour = decodeBcd(r.u8());
    t.minute = decodeBcd(r.u8());
    t.second = decodeBcd(r.u8());
    t.valid = (
        r.ok &&
        0<=t.year &&
        1<=t.month && t.month<=12 &&
        1<=t.day && t.day<=31 &&
        0<=t.hour && t.hour<=23 &&
        0<=t.minute && t.minute<=59 &&
        0<=t.second && t.second<=59
    );
    return t;
}

static time_t localMktime(
    int year,
    int month,
    int day,
    int hour,
    int minute,
    int second
) {
    struct tm t;
    memset(&t, 0, sizeof(t));
    t.tm_sec = second;
    t.tm_min = minute;
    t.tm_hour = hour;
    t.tm_mday = day;
    t.tm_mon = (month-1);
    t.tm_year = (year - 1900);
    t.tm_isdst = -1;    // let the timezone rules decide, 0 would mean "winter time" all year
    return mktime(&t);
}

time_t localEpoch(
    const AbsoluteTime &t
) {
    return localMktime(t.year, t.month, t.day, t.hour, t.minute, t.second);
}

std::string formatTime(
    const AbsoluteTime &t
) {
    char buf[32];
    snprintf(buf, sizeof(buf), "%04d/%02d/%02d %02d:%02d:%02d", t.year, t.month, t.day, t.hour, t.minute, t.second);
    return buf;
}

// segment data event: header up to byte 30, entry count at 30-31, status
// flags at 32, entries from byte 36
static constexpr size_t kSegmentEntriesOffset = 36;
static constexpr size_t kSegmentEntrySize = 12;
static constexpr size_t kMealEntrySize = 10;

// common header of glucose and meal segments, false with error set when the
// message cannot hold the entries it announces
static bool parseSegmentHeader(
    const uint8_t *buffer,
    size_t len,
    size_t entrySize,
    uint32_t &u0,
    uint32_t &u1,
    uint16_t &u2,
    bool &last,
    std::string &error
) {
    Reader r(buffer, len, 22);
    u0 = r.u32();
    u1 = r.u32();
    u2 = r.u16();
    last = (0 != (0x40 & r.u8()));
    if(!r.ok || len<kSegmentEntriesOffset) {
        error = "data segment too short (" + std::to_string(len) + " bytes)";
        return false;
    }
    size_t nbEntries = u2;
    LOG_NFO("segment has %d entries", (int)nbEntries);
    if(nbEntries > (len - kSegmentEntriesOffset) / entrySize) {
        error = "data segment announces " + std::to_string(nbEntries) + " entries but holds " +
            std::to_string((len - kSegmentEntriesOffset) / entrySize);
        return false;
    }
    return true;
}

static uint64_t timeKeyAt(
    const uint8_t *buffer,
    size_t len,
    size_t offset
) {
    Reader r(buffer, len, offset);
    uint64_t hi = r.u32();
    return (hi << 32) | r.u32();
}

bool parseSegment(
    const uint8_t *buffer,
    size_t len,
    Segment &segment,
    std::string &error
) {
    segment.samples.clear();
    if(!parseSegmentHeader(buffer, len, kSegmentEntrySize, segment.u0, segment.u1, segment.u2, segment.last, error)) {
        return false;
    }
    for(size_t i=0; i<segment.u2; ++i) {
        auto offset = kSegmentEntriesOffset + i*kSegmentEntrySize;
        Reader e(buffer, len, offset);
        auto t = decodeAbsoluteTime(e.sub(8));
        Sample s;
        s.year = t.year;
        s.month = t.month;
        s.day = t.day;
        s.hour = t.hour;
        s.minute = t.minute;
        s.value = e.u16();
        s.status = e.u16();
        s.validDate = isValidDate(s);
        s.timeKey = timeKeyAt(buffer, len, offset);
        segment.samples.push_back(s);
    }
    return true;
}

bool parseMealSegment(
    const uint8_t *buffer,
    size_t len,
    MealSegment &segment,
    std::string &error
) {
    segment.entries.clear();
    if(!parseSegmentHeader(buffer, len, kMealEntrySize, segment.u0, segment.u1, segment.u2, segment.last, error)) {
        return false;
    }
    // a glucose segment read as markers would fit too: its byte count tells them apart
    auto bytes = Reader(buffer, len, 34).u16();
    if(bytes != segment.u2 * kMealEntrySize) {
        error = "meal marker segment holds " + std::to_string(bytes) + " bytes for " +
            std::to_string(segment.u2) + " entries of " + std::to_string(kMealEntrySize);
        return false;
    }
    for(size_t i=0; i<segment.u2; ++i) {
        auto offset = kSegmentEntriesOffset + i*kMealEntrySize;
        Reader e(buffer, len, offset + 8);
        segment.entries.push_back({timeKeyAt(buffer, len, offset), e.u16()});
    }
    return true;
}

// printable ASCII of an octet string, without the trailing NUL / space padding
static std::string printable(
    Reader r
) {
    std::string s;
    while(r.has(1)) {
        auto c = r.u8();
        s += (0x20<=c && c<0x7F) ? char(c) : (0==c ? '\0' : '?');
    }
    while(!s.empty() && ('\0'==s.back() || ' '==s.back())) {
        s.pop_back();
    }
    s.erase(std::remove(s.begin(), s.end(), '\0'), s.end());
    return s;
}

static bool octetString(
    Reader &r,
    std::string &s
) {
    auto n = r.u16();
    auto v = r.sub(n);
    if(!r.ok) {
        return false;
    }
    s = printable(v);
    return true;
}

// Roche writes "serial-number: 92500000042", keep what follows the label
static std::string afterLabel(
    const std::string &s
) {
    auto colon = s.find(": ");
    return (std::string::npos==colon ? s : s.substr(colon + 2));
}

// presentation APDU with the given data choice, positioned after its length field
static bool presentation(
    Reader &r,
    uint16_t choice
) {
    auto type = r.u16();
    r.skip(6);
    auto actual = r.u16();
    r.u16();
    return r.ok && kAPDU_TYPE_PRESENTATION_APDU==type && choice==actual;
}

bool parseMdsAnswer(
    const uint8_t *buffer,
    size_t len,
    MeterInfo &info
) {
    info = MeterInfo();
    Reader r(buffer, len);
    if(!presentation(r, kDATA_ADPU_RESPONSE_GET)) {
        return false;
    }
    r.u16();                        // MDS object handle
    auto count = r.u16();
    auto attrs = r.sub(r.u16());
    if(!r.ok) {
        return false;
    }
    for(int i=0; i<count; ++i) {
        auto id = attrs.u16();
        auto value = attrs.sub(attrs.u16());
        if(!attrs.ok) {
            return false;
        }
        switch(id) {
            case kMDC_ATTR_ID_MODEL:
                octetString(value, info.manufacturer);
                octetString(value, info.model);
                break;
            case kMDC_ATTR_SYS_ID: {
                auto n = value.u16();
                auto bytes = value.sub(n);
                char hex[3];
                while(bytes.has(1)) {
                    snprintf(hex, sizeof(hex), "%02X", bytes.u8());
                    info.systemId += hex;
                }
                break;
            }
            case kMDC_ATTR_ID_PROD_SPECN: {
                auto n = value.u16();
                auto specs = value.sub(value.u16());
                for(int k=0; k<n && specs.ok; ++k) {
                    auto type = specs.u16();
                    specs.u16();    // component id
                    std::string s;
                    if(!octetString(specs, s)) {
                        break;
                    }
                    s = afterLabel(s);
                    switch(type) {
                        case 1: info.serial = s; break;
                        case 3: info.hardware = s; break;
                        case 4: info.software = s; break;
                        case 5: info.firmware = s; break;
                        default: break;
                    }
                }
                break;
            }
            case kMDC_ATTR_TIME_ABS:
                info.clock = decodeAbsoluteTime(value);
                info.hasClock = info.clock.valid;
                break;
            case kMDC_ATTR_MDS_TIME_INFO: {
                auto capabilities = value.u16();
                info.clockSettable = value.ok && (0 != (kMDS_TIME_CAPAB_SET_CLOCK & capabilities));
                break;
            }
            default:
                break;
        }
    }
    return true;
}

// kind of the entries described by a PM_SEG_MAP attribute
static SegmentKind segmentKind(
    Reader map
) {
    auto header = map.u16();
    auto count = map.u16();
    auto elements = map.sub(map.u16());
    if(!map.ok || kSEG_ELEM_HDR_ABSOLUTE_TIME!=header || 1!=count) {
        return kSegmentOther;
    }
    auto objClass = elements.u16();
    auto partition = elements.u16();
    auto code = elements.u16();
    elements.u16();                 // handle
    auto attrCount = elements.u16();
    auto attrs = elements.sub(elements.u16());
    std::vector<std::pair<uint16_t, uint16_t>> layout;
    for(int i=0; i<attrCount && attrs.ok; ++i) {
        auto id = attrs.u16();
        auto size = attrs.u16();
        layout.push_back({id, size});
    }
    if(!elements.ok || !attrs.ok) {
        return kSegmentOther;
    }
    static const std::vector<std::pair<uint16_t, uint16_t>> glucose = {
        {kMDC_ATTR_NU_VAL_OBS_BASIC, 2}, {kMDC_ATTR_MSMT_STAT, 2},
    };
    static const std::vector<std::pair<uint16_t, uint16_t>> meal = {
        {kMDC_ATTR_ENUM_OBS_VAL_SIMP_OID, 2},
    };
    if(kMDC_MOC_VMO_METRIC_NU==objClass && 2==partition && kMDC_CONC_GLU_CONTROL!=code && glucose==layout) {
        return kSegmentGlucose;
    }
    if(kMDC_MOC_VMO_METRIC_ENUM==objClass && 128==partition && kMDC_CTXT_GLU_MEAL==code && meal==layout) {
        return kSegmentMeal;
    }
    return kSegmentOther;
}

bool parseSegmentInfo(
    const uint8_t *buffer,
    size_t len,
    std::vector<SegmentInfo> &segments
) {
    segments.clear();
    Reader r(buffer, len);
    if(!presentation(r, kDATA_ADPU_RESPONSE_CONFIRMED_ACTION)) {
        return false;
    }
    r.u16();                        // pm-store handle
    auto action = r.u16();
    r.u16();
    auto count = r.u16();
    auto list = r.sub(r.u16());
    if(!r.ok || kACTION_TYPE_MDC_ACT_SEG_GET_INFO!=action) {
        return false;
    }
    for(int i=0; i<count; ++i) {
        SegmentInfo info;
        info.instance = list.u16();
        auto attrCount = list.u16();
        auto attrs = list.sub(list.u16());
        if(!list.ok) {
            return false;
        }
        for(int k=0; k<attrCount; ++k) {
            auto id = attrs.u16();
            auto value = attrs.sub(attrs.u16());
            if(!attrs.ok) {
                return false;
            }
            switch(id) {
                case kMDC_ATTR_PM_SEG_MAP:
                    info.kind = segmentKind(value);
                    break;
                case kMDC_ATTR_PM_SEG_LABEL_STRING:
                    octetString(value, info.label);
                    break;
                case kMDC_ATTR_SEG_USAGE_CNT:
                    info.usageCount = value.u32();
                    info.hasUsageCount = value.ok;
                    break;
                default:
                    break;
            }
        }
        segments.push_back(info);
    }
    return true;
}

size_t attachMeals(
    std::vector<Sample> &samples,
    const std::vector<MealEntry> &meals
) {
    std::unordered_map<uint64_t, std::vector<size_t>> byTime;
    for(size_t i=0; i<samples.size(); ++i) {
        byTime[samples[i].timeKey].push_back(i);
    }
    size_t unmatched = 0;
    for(const auto &m : meals) {
        auto it = byTime.find(m.timeKey);
        if(byTime.end()==it) {
            ++unmatched;
            continue;
        }
        for(auto i : it->second) {
            samples[i].meal = m.meal;
        }
    }
    return unmatched;
}

const char *mealName(
    uint16_t meal
) {
    switch(meal) {
        case kMDC_CTXT_GLU_MEAL_PREPRANDIAL: return "before_meal";
        case kMDC_CTXT_GLU_MEAL_POSTPRANDIAL: return "after_meal";
        case kMDC_CTXT_GLU_MEAL_FASTING: return "fasting";
        case kMDC_CTXT_GLU_MEAL_CASUAL: return "casual";
        case kMDC_CTXT_GLU_MEAL_BEDTIME: return "bedtime";
        default: return "other";
    }
}

std::string jsonString(
    const std::string &s
) {
    std::string out = "\"";
    for(unsigned char c : s) {
        if('"'==c || '\\'==c) {
            out += '\\';
            out += char(c);
        } else if(c<0x20 || 0x7F<=c) {
            char buf[8];
            snprintf(buf, sizeof(buf), "\\u%04x", c);
            out += buf;
        } else {
            out += char(c);
        }
    }
    return out + "\"";
}

time_t sampleEpoch(
    const Sample &s
) {
    return localMktime(s.year, s.month, s.day, s.hour, s.minute, 0);
}

std::string sampleKey(
    const Sample &s
) {
    char key[17];
    snprintf(key, sizeof(key), "%016" PRIX64, s.timeKey);
    return key;
}

double mmolPerLiter(
    int mgdl
) {
    return std::round(mgdl * 10 / 18.0) / 10;
}

int reportedValue(
    const Sample &s,
    const char *&range
) {
    range = 0;
    if(kValueHigh==s.value) {
        range = "high";
        return kReportedHigh;
    }
    if(kValueLow==s.value) {
        range = "low";
        return kReportedLow;
    }
    return s.value;
}

std::string sampleJson(
    const Sample &s,
    int id
) {
    auto key = s.hasTimeKey ? ", \"key\":\"" + sampleKey(s) + "\"" : std::string();
    if(!s.validDate) {
        char buf[200];
        snprintf(
            buf,
            sizeof(buf),
            "{ \"id\":%6d, \"epoch\":null, \"timestamp\":null, \"mg/dL\":%3d, \"status\":%d, \"error\":\"invalid date\"%s }",
            id,
            (int)s.value,
            (int)s.status,
            key.c_str()
        );
        return buf;
    }

    const char *rangeName = 0;
    auto mgdl = reportedValue(s, rangeName);
    auto range = rangeName ? std::string(", \"range\":\"") + rangeName + "\"" : std::string();

    std::string meal;
    if(0!=s.meal) {
        meal = std::string(", \"meal\":\"") + mealName(s.meal) + "\"";
    }

    char buf[320];
    snprintf(
        buf,
        sizeof(buf),
        "{ \"id\":%6d, \"epoch\":%11" PRIu64 ", \"timestamp\":\"%04d/%02d/%02d %02d:%02d\", \"mg/dL\":%3d, \"mmol/L\":%4.1f, \"status\":%d%s%s%s }",
        id,
        (uint64_t)sampleEpoch(s),
        s.year,
        s.month,
        s.day,
        s.hour,
        s.minute,
        mgdl,
        mmolPerLiter(mgdl),
        (int)s.status,
        range.c_str(),
        meal.c_str(),
        key.c_str()
    );
    return buf;
}

} // namespace accuchek
