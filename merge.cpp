#include <merge.h>
#include <json.h>
#include <map>
#include <set>
#include <tuple>

namespace accuchek {

// a code mealName() calls "other": the archive only kept the name
static constexpr uint16_t kMealOther = 0xFFFF;

static bool mealCode(
    const std::string &name,
    uint16_t &code
) {
    static const uint16_t codes[] = {
        kMDC_CTXT_GLU_MEAL_PREPRANDIAL, kMDC_CTXT_GLU_MEAL_POSTPRANDIAL, kMDC_CTXT_GLU_MEAL_FASTING,
        kMDC_CTXT_GLU_MEAL_CASUAL, kMDC_CTXT_GLU_MEAL_BEDTIME,
    };
    for(auto c : codes) {
        if(name==mealName(c)) {
            code = c;
            return true;
        }
    }
    if("other"==name) {
        code = kMealOther;
        return true;
    }
    return false;
}

// an integer member in [0, max]; absent gives fallback, or fails when fallback<0
static bool integerField(
    const json::Value &item,
    const char *name,
    long fallback,
    long max,
    long &out,
    std::string &error
) {
    auto v = item.find(name);
    if(0==v) {
        if(fallback<0) {
            error = std::string("no \"") + name + "\"";
            return false;
        }
        out = fallback;
        return true;
    }
    if(json::Value::kNumber!=v->type || !v->isInteger || v->integer<0 || max<v->integer) {
        error = std::string("\"") + name + "\" must be an integer from 0 to " + std::to_string(max);
        return false;
    }
    out = long(v->integer);
    return true;
}

// "2026/10/01 20:47", exactly
static bool parseTimestamp(
    const std::string &t,
    Sample &s
) {
    static const char kShape[] = "dddd/dd/dd dd:dd";
    if(sizeof(kShape) - 1!=t.size()) {
        return false;
    }
    for(size_t i=0; i<t.size(); ++i) {
        auto digit = ('0'<=t[i] && t[i]<='9');
        if('d'==kShape[i] ? !digit : t[i]!=kShape[i]) {
            return false;
        }
    }
    auto num = [&](size_t at, size_t n) { return std::stoi(t.substr(at, n)); };
    s.year = num(0, 4);
    s.month = num(5, 2);
    s.day = num(8, 2);
    s.hour = num(11, 2);
    s.minute = num(14, 2);
    return isValidDate(s);
}

// date fields of a sample from its 8 raw time bytes, as parseSegment does
static bool parseKey(
    const std::string &key,
    Sample &s
) {
    if(16!=key.size()) {
        return false;
    }
    uint8_t bytes[8];
    for(size_t i=0; i<8; ++i) {
        unsigned v = 0;
        for(size_t k=0; k<2; ++k) {
            auto c = key[2*i + k];
            int d = ('0'<=c && c<='9') ? c - '0' : ('A'<=c && c<='F') ? c - 'A' + 10 : ('a'<=c && c<='f') ? c - 'a' + 10 : -1;
            if(d<0) {
                return false;
            }
            v = (v << 4) | unsigned(d);
        }
        bytes[i] = uint8_t(v);
    }
    s.timeKey = 0;
    for(auto b : bytes) {
        s.timeKey = (s.timeKey << 8) | b;
    }
    auto t = decodeAbsoluteTime(Reader(bytes, sizeof(bytes)));
    s.year = t.year;
    s.month = t.month;
    s.day = t.day;
    s.hour = t.hour;
    s.minute = t.minute;
    s.validDate = isValidDate(s);
    return true;
}

static bool parseReading(
    const json::Value &item,
    Sample &s,
    std::string &error
) {
    if(json::Value::kObject!=item.type) {
        error = "not an object";
        return false;
    }
    s = Sample();
    long mgdl = 0;
    long status = 0;
    if(!integerField(item, "mg/dL", -1, 0xFFFF, mgdl, error) || !integerField(item, "status", 0, 0xFFFF, status, error)) {
        return false;
    }
    s.status = uint16_t(status);

    auto key = item.find("key");
    s.hasTimeKey = (0!=key);
    if(key && (json::Value::kString!=key->type || !parseKey(key->string, s))) {
        error = "\"key\" must be 16 hex digits";
        return false;
    }

    auto err = item.find("error");
    auto timestamp = item.find("timestamp");
    auto range = item.find("range");
    if(err && json::Value::kNull!=err->type) {
        // unreadable date on the meter: the raw value is written, no range
        if(json::Value::kString!=err->type) {
            error = "\"error\" must be a string";
            return false;
        }
        if((timestamp && json::Value::kNull!=timestamp->type) || range) {
            error = "a reading with an error has neither timestamp nor range";
            return false;
        }
        if(key && s.validDate) {
            error = "\"key\" " + key->string + " holds a valid date, the reading says " + err->string;
            return false;
        }
        s.validDate = false;
        s.value = uint16_t(mgdl);
    } else {
        Sample t;
        if(!timestamp || json::Value::kString!=timestamp->type || !parseTimestamp(timestamp->string, t)) {
            error = "\"timestamp\" must be a date, YYYY/MM/DD HH:MM";
            return false;
        }
        if(key && !(s.validDate && t.year==s.year && t.month==s.month && t.day==s.day && t.hour==s.hour && t.minute==s.minute)) {
            error = "\"key\" " + key->string + " is not the time of \"timestamp\" " + timestamp->string;
            return false;
        }
        s.year = t.year;
        s.month = t.month;
        s.day = t.day;
        s.hour = t.hour;
        s.minute = t.minute;
        s.validDate = true;
        s.value = uint16_t(mgdl);
        if(range) {
            auto high = (json::Value::kString==range->type && "high"==range->string);
            auto low = (json::Value::kString==range->type && "low"==range->string);
            if(!(high && kReportedHigh==mgdl) && !(low && kReportedLow==mgdl)) {
                error = "\"range\" must be \"high\" with 601 mg/dL or \"low\" with 9 mg/dL";
                return false;
            }
            s.value = (high ? kValueHigh : kValueLow);
        }
    }

    auto meal = item.find("meal");
    if(meal && (json::Value::kString!=meal->type || !mealCode(meal->string, s.meal))) {
        error = "\"meal\" must be fasting, before_meal, after_meal, casual, bedtime or other";
        return false;
    }
    return true;
}

bool parseArchive(
    const std::string &text,
    Archive &archive,
    std::string &error
) {
    archive = Archive();
    json::Value root;
    if(!json::parse(text, root, error)) {
        return false;
    }
    const json::Value *readings = 0;
    if(json::Value::kArray==root.type) {
        readings = &root;
    } else if(json::Value::kObject==root.type) {
        auto format = root.find("format");
        if(!format || json::Value::kNumber!=format->type || !format->isInteger || 2!=format->integer) {
            error = "not an accuchek output: \"format\" 2 expected";
            return false;
        }
        readings = root.find("readings");
        auto meter = root.find("meter");
        auto serial = meter ? meter->find("serial") : 0;
        if(serial && json::Value::kString==serial->type) {
            archive.serial = serial->string;
        }
    }
    if(!readings || json::Value::kArray!=readings->type) {
        error = "not an accuchek output: no \"readings\" array";
        return false;
    }
    for(size_t i=0; i<readings->array.size(); ++i) {
        Sample s;
        std::string why;
        if(!parseReading(readings->array[i], s, why)) {
            error = "reading " + std::to_string(i) + ": " + why;
            return false;
        }
        archive.samples.push_back(s);
    }
    return true;
}

using KeyIdentity = std::tuple<uint64_t, uint16_t, uint16_t>;
using MinuteIdentity = std::tuple<int, int, int, int, int, uint16_t, uint16_t>;

static KeyIdentity keyIdentity(
    const Sample &s
) {
    return KeyIdentity(s.timeKey, s.value, s.status);
}

// what an archive written without keys still tells apart; every unreadable
// date is the same minute
static MinuteIdentity minuteIdentity(
    const Sample &s
) {
    if(!s.validDate) {
        return MinuteIdentity(-1, -1, -1, -1, -1, s.value, s.status);
    }
    return MinuteIdentity(s.year, s.month, s.day, s.hour, s.minute, s.value, s.status);
}

size_t mergeSamples(
    const std::vector<Sample> &archive,
    const std::vector<Sample> &download,
    std::vector<Sample> &merged
) {
    std::set<KeyIdentity> downloaded;
    std::map<MinuteIdentity, size_t> unmatched;
    for(const auto &d : download) {
        downloaded.insert(keyIdentity(d));
        ++unmatched[minuteIdentity(d)];
    }
    merged.clear();
    std::set<KeyIdentity> seen;
    for(const auto &a : archive) {
        if(a.hasTimeKey) {
            auto id = keyIdentity(a);
            if(downloaded.count(id) || !seen.insert(id).second) {
                continue;
            }
        } else {
            auto it = unmatched.find(minuteIdentity(a));
            if(unmatched.end()!=it && 0<it->second) {
                --it->second;
                continue;
            }
        }
        merged.push_back(a);
    }
    auto kept = merged.size();
    merged.insert(merged.end(), download.begin(), download.end());
    return kept;
}

} // namespace accuchek
