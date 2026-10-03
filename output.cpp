#include <output.h>
#include <time.h>

namespace accuchek {

static std::string countJson(
    const SegmentCount &count
) {
    return "{\"announced\":" + (count.announced ? std::to_string(count.expected) : std::string("null")) +
        ", \"received\":" + std::to_string(count.received) + "}";
}

static std::string localTimeString(
    time_t t
) {
    struct tm local;
    localtime_r(&t, &local);
    char buf[32];
    strftime(buf, sizeof(buf), "%Y/%m/%d %H:%M:%S", &local);
    return buf;
}

static const char *jsonBool(
    bool b
) {
    return b ? "true" : "false";
}

std::string outputJson(
    const SessionReport &r,
    const std::vector<Sample> &samples
) {
    std::string out = "{\n  \"format\": 2,\n";
    if(r.hasMeter) {
        const auto &m = r.meter;
        out += "  \"meter\": {\"manufacturer\":" + jsonString(m.manufacturer) +
            ", \"model\":" + jsonString(m.model) +
            ", \"serial\":" + jsonString(m.serial) +
            ", \"firmware\":" + jsonString(m.firmware) +
            ", \"hardware\":" + jsonString(m.hardware) +
            ", \"software\":" + jsonString(m.software) +
            ", \"system_id\":" + jsonString(m.systemId) + "},\n";
    } else {
        out += "  \"meter\": null,\n";
    }
    if(r.hasMeter && r.meter.hasClock) {
        out += "  \"clock\": {\"meter\":\"" + formatTime(r.meter.clock) + "\"" +
            ", \"pc\":" + (r.pc.known ? jsonString(localTimeString(r.pc.now)) : std::string("null")) +
            ", \"offset_s\":" + (r.hasClockOffset ? std::to_string(r.clockOffsetS) : std::string("null")) +
            ", \"settable\":" + jsonBool(r.meter.clockSettable) +
            ", \"pc_synchronized\":" + (r.pc.known ? jsonBool(r.pc.synchronized) : "null") +
            ", \"action\":\"" + clockActionName(r.clockAction) + "\"},\n";
    } else {
        out += "  \"clock\": null,\n";
    }
    out += "  \"glucose\": " + countJson(r.glucose) + ",\n";
    if(r.hasMealSegment) {
        auto meal = countJson(r.meal);
        meal.pop_back();
        out += "  \"meal\": " + meal + ", \"unmatched\":" + std::to_string(r.mealsUnmatched) + "},\n";
    } else {
        out += "  \"meal\": null,\n";
    }
    out += "  \"readings\": [";
    for(size_t i=0; i<samples.size(); ++i) {
        out += (0==i ? "\n    " : ",\n    ") + sampleJson(samples[i], int(i));
    }
    out += samples.empty() ? "]\n}\n" : "\n  ]\n}\n";
    return out;
}

static void checkCount(
    std::vector<std::string> &warnings,
    const char *what,
    const SegmentCount &count
) {
    if(count.announced && count.expected!=count.received) {
        warnings.push_back(
            std::string("the meter announced ") + std::to_string(count.expected) + " " + what +
            ", " + std::to_string(count.received) + " received"
        );
    }
}

std::vector<std::string> countWarnings(
    const SessionReport &report
) {
    std::vector<std::string> warnings;
    checkCount(warnings, "readings", report.glucose);
    if(report.hasMealSegment) {
        checkCount(warnings, "meal markers", report.meal);
    }
    return warnings;
}

} // namespace accuchek
