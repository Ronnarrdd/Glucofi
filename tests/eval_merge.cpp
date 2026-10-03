// make eval-merge ARCHIVES="a.json b.json ...": --merge on real downloads
//
// periodic eval, run on real outputs of accuchek (oldest first), which hold
// health data and never go in the repository. Prints counts only.
//
// for each file: read back as an archive, written again identically
// for each pair (older, newer), and for newer with its first k readings
// dropped (a full meter): every older reading is in the merge exactly once,
// the merge ends with the download, merging again changes nothing

#include <merge.h>
#include <output.h>
#include <trace.h>
#include <log.h>
#include <stdio.h>
#include <stdlib.h>
#include <tuple>

using namespace accuchek;

static long gFailures = 0;

static void failure(
    const std::string &what
) {
    ++gFailures;
    fprintf(stderr, "FAIL %s\n", what.c_str());
}

// what tells two readings apart in outputs without key: minute, value, status
static std::tuple<int, int, int, int, int, int, int> minuteOf(
    const Sample &s
) {
    if(!s.validDate) {
        return std::make_tuple(-1, -1, -1, -1, -1, int(s.value), int(s.status));
    }
    return std::make_tuple(s.year, s.month, s.day, s.hour, s.minute, int(s.value), int(s.status));
}

static size_t countOf(
    const std::vector<Sample> &samples,
    const Sample &s
) {
    size_t n = 0;
    for(const auto &x : samples) {
        n += (minuteOf(x)==minuteOf(s));
    }
    return n;
}

static void checkMerge(
    const std::string &name,
    const std::vector<Sample> &older,
    const std::vector<Sample> &newer,
    long &kept
) {
    std::vector<Sample> merged;
    kept = long(mergeSamples(older, newer, merged));
    if(merged.size()!=size_t(kept) + newer.size()) {
        failure(name + ": merge size");
    }
    for(size_t i=0; i<newer.size() && kept + i<merged.size(); ++i) {
        if(minuteOf(merged[kept + i])!=minuteOf(newer[i])) {
            failure(name + ": the merge does not end with the download");
            break;
        }
    }
    // naive count: no older reading lost, none doubled
    for(const auto &s : older) {
        auto expected = std::max(countOf(older, s), countOf(newer, s));
        if(countOf(merged, s)!=expected) {
            failure(name + ": a reading of the archive is lost or doubled");
            break;
        }
    }
    std::vector<Sample> again;
    mergeSamples(merged, newer, again);
    SessionReport report;
    if(outputJson(report, again)!=outputJson(report, merged)) {
        failure(name + ": merging again changes the output");
    }
}

int main(
    int argc,
    char *argv[]
) {
    gQuiet = true;
    std::vector<Archive> archives;
    long readings = 0;
    for(int i=1; i<argc; ++i) {
        std::string text;
        Archive a;
        std::string error;
        if(!readFile(argv[i], text)) {
            failure(std::string(argv[i]) + ": unreadable");
            continue;
        }
        if(!parseArchive(text, a, error)) {
            failure(std::string(argv[i]) + ": " + error);
            continue;
        }
        readings += long(a.samples.size());
        // written again, read again: same readings
        SessionReport report;
        Archive back;
        if(!parseArchive(outputJson(report, a.samples), back, error) || back.samples.size()!=a.samples.size()) {
            failure(std::string(argv[i]) + ": not read back");
        }
        archives.push_back(a);
    }
    long pairs = 0;
    long keptTotal = 0;
    long dropChecks = 0;
    for(size_t i=0; i+1<archives.size(); ++i) {
        auto name = std::string(argv[i + 1]) + " + " + argv[i + 2];
        long kept = 0;
        checkMerge(name, archives[i].samples, archives[i + 1].samples, kept);
        keptTotal += kept;
        ++pairs;
        for(size_t k : {size_t(1), size_t(10), size_t(100)}) {
            const auto &newer = archives[i + 1].samples;
            if(newer.size()<k) {
                continue;
            }
            std::vector<Sample> full(newer.begin() + k, newer.end());
            checkMerge(name + " (first " + std::to_string(k) + " dropped)", archives[i].samples, full, kept);
            ++dropChecks;
        }
    }
    printf(
        "eval-merge: %d files, %ld readings, %ld pairs (%ld archive readings kept), %ld full meter simulations, %ld failures\n",
        argc - 1, readings, pairs, keptTotal, dropChecks, gFailures
    );
    return (0==gFailures && 1<argc) ? 0 : 1;
}
