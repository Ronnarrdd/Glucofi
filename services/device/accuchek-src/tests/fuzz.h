/*

    fuzzing of the parsers and of whole sessions with mutated device packets

    every packet handed to a parser is copied right before a PROT_NONE page,
    so reading a single byte past what was received crashes, even without
    AddressSanitizer

 */

#ifndef __FUZZ_H__
    #define __FUZZ_H__

    #include "sim.h"
    #include <stdint.h>

    namespace fuzz {

    // copy of some bytes ending exactly at a protected page
    struct GuardedBytes {
        explicit GuardedBytes(const sim::Bytes &bytes);
        ~GuardedBytes();
        GuardedBytes(const GuardedBytes &) = delete;
        GuardedBytes &operator=(const GuardedBytes &) = delete;

        const uint8_t *data;
        size_t size;

    private:
        uint8_t *map;
        size_t mapSize;
    };

    struct Stats {
        long iterations = 0;
        long parsedSegments = 0;
        long rejectedSegments = 0;
        long sessionsOk = 0;
        long sessionsFailed = 0;
        long archivesParsed = 0;
        long archivesRejected = 0;
        long invariantFailures = 0;
    };

    // deterministic for a given seed; iteration numbers start at first
    Stats run(uint64_t seed, long first, long count);

    // iteration being run, for crash reports
    extern volatile long gCurrentIteration;

    } // namespace fuzz

#endif // __FUZZ_H__
