#include <log.h>
#include <time.h>
#include <stdio.h>
#include <stdarg.h>
#include <string.h>

bool gQuiet = true;

// seconds since the first log line, from the monotonic clock
static double elapsed() {
    static struct timespec start;
    static bool started = false;
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    if(!started) {
        start = now;
        started = true;
    }
    return double(now.tv_sec - start.tv_sec) + 1e-9 * double(now.tv_nsec - start.tv_nsec);
}

void logMessage(
    const char *level,
    const char *file,
    int line,
    const char *format,
    ...
) {
    const char *slash = strrchr(file, '/');
    char text[2048];
    va_list arg;
    va_start(arg, format);
    vsnprintf(text, sizeof(text), format, arg);
    va_end(arg);
    auto end = strlen(text);
    while(0<end && '\n'==text[end-1]) {
        text[--end] = 0;
    }
    // one fprintf per line: lines of concurrent writers to stderr stay whole
    fprintf(stderr, "accuchek[%s] %+.6f %s:%d: %s\n", level, elapsed(), slash ? slash + 1 : file, line, text);
}
