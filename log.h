/*

    logs on stderr, never on stdout (stdout carries the JSON)

    silent unless gQuiet is false (main sets it from ACCUCHEK_DBG); a log
    call never fails, never exits and never touches anything but stderr

 */

#ifndef __LOG_H__
    #define __LOG_H__

    extern bool gQuiet;

    // "accuchek[nfo] +0.012345 session.cpp:141: <message>\n" on stderr
    void logMessage(const char *level, const char *file, int line, const char *format, ...)
        __attribute__((format(printf, 4, 5)));

    #define LOG_AT(level, ...)                                      \
        do {                                                        \
            if(!gQuiet) {                                           \
                logMessage((level), __FILE__, __LINE__, __VA_ARGS__); \
            }                                                       \
        } while(0)

    #define LOG_NFO(...) LOG_AT("nfo", __VA_ARGS__)
    #define LOG_WRN(...) LOG_AT("wrn", __VA_ARGS__)

#endif // __LOG_H__
