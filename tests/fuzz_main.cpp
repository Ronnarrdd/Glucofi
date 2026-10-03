// make fuzz: periodic eval, usage: fuzz [--seed N] [--from N] [--count N]

#include "fuzz.h"
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <log.h>

static uint64_t gSeed = 20261001;

static void onCrash(
    int sig
) {
    char msg[160];
    auto n = snprintf(
        msg,
        sizeof(msg),
        "\nCRASH signal %d at iteration %ld -- reproduce: fuzz --seed %llu --from %ld --count 1\n",
        sig,
        (long)fuzz::gCurrentIteration,
        (unsigned long long)gSeed,
        (long)fuzz::gCurrentIteration
    );
    if(0<n) {
        auto w = write(2, msg, n);
        (void)w;
    }
    _exit(1);
}

int main(
    int argc,
    char *argv[]
) {
    long first = 0;
    long count = 200000;
    for(int i=1; i+1<argc; i+=2) {
        if(0==strcmp(argv[i], "--seed")) gSeed = strtoull(argv[i+1], 0, 10);
        else if(0==strcmp(argv[i], "--from")) first = atol(argv[i+1]);
        else if(0==strcmp(argv[i], "--count")) count = atol(argv[i+1]);
    }
    gQuiet = true;
    setenv("TZ", "Europe/Paris", 1);
    signal(SIGSEGV, onCrash);
    signal(SIGBUS, onCrash);
    signal(SIGABRT, onCrash);

    auto stats = fuzz::run(gSeed, first, count);
    printf(
        "fuzz seed=%llu: %ld iterations, segments parsed %ld / rejected %ld, sessions ok %ld / failed %ld, "
        "archives parsed %ld / rejected %ld, invariant failures %ld, crashes 0\n",
        (unsigned long long)gSeed,
        stats.iterations,
        stats.parsedSegments,
        stats.rejectedSegments,
        stats.sessionsOk,
        stats.sessionsFailed,
        stats.archivesParsed,
        stats.archivesRejected,
        stats.invariantFailures
    );
    return 0==stats.invariantFailures ? 0 : 1;
}
