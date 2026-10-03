#include "fuzz.h"
#include <session.h>
#include <output.h>
#include <merge.h>
#include <trace.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <sys/mman.h>

using namespace accuchek;

namespace fuzz {

volatile long gCurrentIteration = -1;

GuardedBytes::GuardedBytes(
    const sim::Bytes &bytes
) {
    auto page = (size_t)sysconf(_SC_PAGESIZE);
    auto dataPages = (bytes.size() + page - 1) / page;
    mapSize = (dataPages + 1) * page;
    map = (uint8_t *)mmap(0, mapSize, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if(MAP_FAILED==map) {
        perror("mmap");
        _exit(2);
    }
    mprotect(map + dataPages * page, page, PROT_NONE);
    auto start = map + dataPages * page - bytes.size();
    if(!bytes.empty()) {
        memcpy(start, bytes.data(), bytes.size());
    }
    data = start;
    size = bytes.size();
}

GuardedBytes::~GuardedBytes() {
    munmap(map, mapSize);
}

// xorshift64*, deterministic across platforms
struct Rng {
    uint64_t s;
    explicit Rng(uint64_t seed) : s(seed ? seed : 0x9E3779B97F4A7C15ull) {}
    uint64_t next() {
        s ^= s >> 12;
        s ^= s << 25;
        s ^= s >> 27;
        return s * 2685821657736338717ull;
    }
    size_t below(size_t n) { return n ? next() % n : 0; }
};

static sim::Bytes mutate(
    Rng &rng,
    sim::Bytes b
) {
    auto rounds = 1 + rng.below(4);
    for(size_t k=0; k<rounds; ++k) {
        switch(rng.below(6)) {
            case 0:     // truncate
                b.resize(rng.below(b.size() + 1));
                break;
            case 1:     // flip bits
                if(!b.empty()) b[rng.below(b.size())] ^= uint8_t(1 << rng.below(8));
                break;
            case 2:     // smash a 16 bit field (often a length or a count)
                if(2<=b.size()) {
                    static const uint16_t values[] = {0x0000, 0x0001, 0x00FF, 0x7FFF, 0x8000, 0xFFFF};
                    auto at = rng.below(b.size() - 1);
                    auto v = (rng.below(2) ? values[rng.below(6)] : uint16_t(rng.next()));
                    b[at] = v >> 8;
                    b[at+1] = v & 0xFF;
                }
                break;
            case 3:     // random byte
                if(!b.empty()) b[rng.below(b.size())] = uint8_t(rng.next());
                break;
            case 4:     // append garbage
                for(auto n = rng.below(40); n; --n) b.push_back(uint8_t(rng.next()));
                break;
            default:    // pure noise
                b.resize(rng.below(200));
                for(auto &x : b) x = uint8_t(rng.next());
                break;
        }
    }
    return b;
}

static std::vector<sim::Record> randomRecords(
    Rng &rng
) {
    std::vector<sim::Record> records;
    for(auto n = rng.below(8); n; --n) {
        records.push_back({
            int(2000 + rng.below(40)), int(1 + rng.below(12)), int(1 + rng.below(28)),
            int(rng.below(24)), int(rng.below(60)), uint16_t(20 + rng.below(560)), 0
        });
    }
    return records;
}

static std::vector<sim::MealRecord> randomMeals(
    Rng &rng
) {
    static const uint16_t meals[] = {
        kMDC_CTXT_GLU_MEAL_PREPRANDIAL, kMDC_CTXT_GLU_MEAL_POSTPRANDIAL, kMDC_CTXT_GLU_MEAL_FASTING,
        kMDC_CTXT_GLU_MEAL_CASUAL, kMDC_CTXT_GLU_MEAL_BEDTIME, 0x1234,
    };
    std::vector<sim::MealRecord> records;
    for(auto n = rng.below(8); n; --n) {
        records.push_back({
            int(2000 + rng.below(40)), int(1 + rng.below(12)), int(1 + rng.below(28)),
            int(rng.below(24)), int(rng.below(60)), int(rng.below(60)), meals[rng.below(6)]
        });
    }
    return records;
}

static void fuzzParsers(
    Rng &rng,
    Stats &stats
) {
    sim::Bytes seed;
    switch(rng.below(7)) {
        case 0: seed = sim::configInfo(uint16_t(rng.next()), uint16_t(rng.next()), uint16_t(rng.below(5))); break;
        case 1: seed = sim::segmentHeaders(uint16_t(rng.next()), 0x0100); break;
        case 2: seed = sim::mdsAnswer(uint16_t(rng.next())); break;
        case 3: seed = sim::mdsAnswer(uint16_t(rng.next()), sim::Meter()); break;
        case 4: seed = sim::segmentInfoResponse(uint16_t(rng.next()), 0x0100, sim::guideSegments(rng.below(1000), rng.below(1000))); break;
        case 5: seed = sim::mealSegment(0x30, 0x0100, 0, randomMeals(rng), true, rng.below(2)); break;
        default: seed = sim::dataSegment(0x20, 0x0100, 0, randomRecords(rng), true, rng.below(2)); break;
    }
    GuardedBytes g(mutate(rng, seed));

    uint16_t invokeId = 0;
    readInvokeId(g.data, g.size, invokeId);

    uint16_t handle = 0;
    uint16_t nbSegments = 0;
    parseConfigInfo(g.data, g.size, handle, nbSegments);

    Segment segment;
    std::string error;
    if(parseSegment(g.data, g.size, segment, error)) {
        ++stats.parsedSegments;
        if(36 + 12 * segment.samples.size() > g.size) {
            ++stats.invariantFailures;
            fprintf(stderr, "iteration %ld: %zu samples parsed out of %zu bytes\n",
                (long)gCurrentIteration, segment.samples.size(), g.size);
        }
        for(const auto &s : segment.samples) {
            auto json = sampleJson(s, 0);
            if(s.validDate && (s.month<1 || 12<s.month || s.minute<0 || 59<s.minute)) {
                ++stats.invariantFailures;
                fprintf(stderr, "iteration %ld: invalid date accepted: %s\n", (long)gCurrentIteration, json.c_str());
            }
        }
    } else {
        ++stats.rejectedSegments;
    }

    MealSegment meals;
    if(parseMealSegment(g.data, g.size, meals, error)) {
        if(36 + 10 * meals.entries.size() > g.size) {
            ++stats.invariantFailures;
            fprintf(stderr, "iteration %ld: %zu meal markers parsed out of %zu bytes\n",
                (long)gCurrentIteration, meals.entries.size(), g.size);
        }
        for(const auto &m : meals.entries) {
            mealName(m.meal);
        }
    }

    MeterInfo info;
    if(parseMdsAnswer(g.data, g.size, info)) {
        jsonString(info.manufacturer + info.model + info.serial + info.firmware + info.systemId);
        if(info.hasClock && !(info.clock.valid && 1<=info.clock.month && info.clock.month<=12 && info.clock.second<=59)) {
            ++stats.invariantFailures;
            fprintf(stderr, "iteration %ld: invalid meter clock accepted\n", (long)gCurrentIteration);
        }
    }

    std::vector<SegmentInfo> segments;
    if(parseSegmentInfo(g.data, g.size, segments) && 6 * segments.size() > g.size) {
        ++stats.invariantFailures;
        fprintf(stderr, "iteration %ld: %zu segments described in %zu bytes\n",
            (long)gCurrentIteration, segments.size(), g.size);
    }
}

// a --merge archive written by accuchek, then mutated: what parses must
// write the same text again once read back, and merge without losing anything
static void fuzzArchive(
    Rng &rng,
    Stats &stats
) {
    std::vector<Sample> samples;
    for(const auto &r : randomRecords(rng)) {
        Sample s = {r.year, r.month, r.day, r.hour, r.minute, r.value, uint16_t(rng.below(3)), true};
        auto bcd = [](int v) { return uint64_t(((v / 10) << 4) | (v % 10)); };
        s.timeKey = (bcd(r.year / 100) << 56) | (bcd(r.year % 100) << 48) | (bcd(r.month) << 40) | (bcd(r.day) << 32) |
            (bcd(r.hour) << 24) | (bcd(r.minute) << 16) | (bcd(int(rng.below(60))) << 8);
        s.hasTimeKey = (0!=rng.below(4));
        if(0==rng.below(3)) {
            s.meal = kMDC_CTXT_GLU_MEAL_FASTING;
        }
        if(0==rng.below(8)) {
            s.value = rng.below(2) ? kValueHigh : kValueLow;
        }
        samples.push_back(s);
    }
    SessionReport report;
    auto text = outputJson(report, samples);
    std::string input;
    if(rng.below(2)) {
        sim::Bytes bytes(text.begin(), text.end());
        auto mutated = mutate(rng, bytes);
        input.assign(mutated.begin(), mutated.end());
    } else {
        // still JSON: one digit changed reaches the reading checks
        input = text;
        for(auto n = 1 + rng.below(3); n; --n) {
            auto at = rng.below(input.size());
            while(at<input.size() && !('0'<=input[at] && input[at]<='9')) {
                ++at;
            }
            if(at<input.size()) {
                input[at] = char('0' + rng.below(10));
            }
        }
    }

    Archive archive;
    std::string error;
    if(!parseArchive(input, archive, error)) {
        ++stats.archivesRejected;
        return;
    }
    ++stats.archivesParsed;
    auto once = outputJson(report, archive.samples);
    Archive back;
    if(!parseArchive(once, back, error) || outputJson(report, back.samples)!=once) {
        ++stats.invariantFailures;
        fprintf(stderr, "iteration %ld: archive not written back identically (%s)\n", (long)gCurrentIteration, error.c_str());
    }
    std::vector<Sample> merged;
    auto kept = mergeSamples(archive.samples, samples, merged);
    if(merged.size()!=kept + samples.size() || archive.samples.size()<kept) {
        ++stats.invariantFailures;
        fprintf(stderr, "iteration %ld: merge sizes\n", (long)gCurrentIteration);
    }
}

// a valid session where one device message is mutated
static void fuzzSession(
    Rng &rng,
    Stats &stats
) {
    sim::Session session;
    for(auto n = 1 + rng.below(3); n; --n) {
        session.glucose.push_back(randomRecords(rng));
    }
    if(rng.below(2)) {
        session.meals.push_back(randomMeals(rng));
    }
    session.describe = (0!=rng.below(4));
    session.setTime = session.describe && rng.below(2);
    auto trace = sim::sessionTrace(session);
    SessionOptions options;
    options.setTime = session.setTime;
    auto tm = sim::localTm(session.now);
    auto now = mktime(&tm);
    options.pcClock = [now]() { return PcClock{now, true, true}; };

    std::vector<std::string> lines;
    size_t start = 0;
    while(start<trace.size()) {
        auto nl = trace.find('\n', start);
        lines.push_back(trace.substr(start, nl - start));
        start = nl + 1;
    }
    std::vector<size_t> incoming;
    for(size_t i=0; i<lines.size(); ++i) {
        if('<'==lines[i][0]) incoming.push_back(i);
    }
    auto &victim = lines[incoming[rng.below(incoming.size())]];
    sim::Bytes bytes;
    for(size_t i=2; i+1<victim.size(); i+=2) {
        bytes.push_back(uint8_t(std::stoi(victim.substr(i, 2), 0, 16)));
    }
    victim = sim::line('<', mutate(rng, bytes));
    victim.pop_back();

    std::string mutated;
    for(const auto &l : lines) mutated += l + "\n";

    try {
        ReplayTransport transport(mutated);
        SessionReport report;
        downloadSamples(transport, options, report, [](const Sample &s) { sampleJson(s, 0); });
        ++stats.sessionsOk;
    } catch(const SessionError &) {
        ++stats.sessionsFailed;
    }
}

Stats run(
    uint64_t seed,
    long first,
    long count
) {
    Stats stats;
    for(long i=first; i<first+count; ++i) {
        gCurrentIteration = i;
        Rng rng(seed ^ (0x9E3779B97F4A7C15ull * uint64_t(i + 1)));
        if(rng.below(4)) {
            fuzzParsers(rng, stats);
        } else {
            fuzzSession(rng, stats);
        }
        // its own generator: the iterations above stay those of earlier seeds
        Rng archiveRng(seed ^ (0xD1B54A32D192ED03ull * uint64_t(i + 1)));
        fuzzArchive(archiveRng, stats);
        ++stats.iterations;
    }
    gCurrentIteration = -1;
    return stats;
}

} // namespace fuzz
