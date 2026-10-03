#include "check.h"
#include <log.h>
#include <trace.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <algorithm>

// what the logger writes on stderr while fn runs
template<typename F> static std::string captureStderr(
    F fn
) {
    char path[] = "/tmp/accuchek-log-XXXXXX";
    auto fd = mkstemp(path);
    fflush(stderr);
    auto saved = dup(2);
    dup2(fd, 2);
    fn();
    fflush(stderr);
    dup2(saved, 2);
    close(saved);
    close(fd);
    std::string text;
    accuchek::readFile(path, text);
    unlink(path);
    return text;
}

TEST(log_is_silent_when_quiet) {
    gQuiet = true;
    CHECK_EQ(captureStderr([]() { LOG_NFO("hidden %d", 1); LOG_WRN("hidden too"); }), std::string(""));
}

TEST(log_line_format) {
    gQuiet = false;
    auto text = captureStderr([]() { LOG_WRN("value %d\n", 42); });
    gQuiet = true;
    CHECK(0==text.find("accuchek[wrn] +"));
    CHECK(std::string::npos!=text.find(" test_log.cpp:"));
    // the trailing newline of the format is not doubled
    CHECK_EQ(text.substr(text.size() - 11), std::string(": value 42\n"));
    CHECK_EQ(std::count(text.begin(), text.end(), '\n'), 1);
}

TEST(log_truncates_long_messages) {
    std::string big(5000, 'x');
    gQuiet = false;
    auto text = captureStderr([&]() { LOG_NFO("%s", big.c_str()); });
    gQuiet = true;
    CHECK(text.size()<2200);
    CHECK_EQ(text.back(), '\n');
}
