// minimal test harness: TEST(name) { CHECK(...); CHECK_EQ(a, b); }

#ifndef __CHECK_H__
    #define __CHECK_H__

    #include <string>
    #include <vector>
    #include <sstream>
    #include <stdio.h>

    struct TestCase {
        const char *name;
        void (*fn)();
    };

    std::vector<TestCase> &testRegistry();
    void checkFailed(const char *file, int line, const std::string &what);

    #define TEST(name)                                                              \
        static void name();                                                         \
        static const bool name##_registered = (testRegistry().push_back({#name, name}), true); \
        static void name()

    #define CHECK(cond)                                                             \
        do {                                                                        \
            if(!(cond)) {                                                           \
                checkFailed(__FILE__, __LINE__, "CHECK(" #cond ")");                \
            }                                                                       \
        } while(0)

    #define CHECK_EQ(a, b)                                                          \
        do {                                                                        \
            auto _a = (a);                                                          \
            auto _b = (b);                                                          \
            if(!(_a==_b)) {                                                         \
                std::ostringstream _s;                                              \
                _s << "CHECK_EQ(" #a ", " #b ")\n    got:      " << _a              \
                   << "\n    expected: " << _b;                                     \
                checkFailed(__FILE__, __LINE__, _s.str());                          \
            }                                                                       \
        } while(0)

#endif // __CHECK_H__
