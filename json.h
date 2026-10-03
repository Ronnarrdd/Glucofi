/*

    strict JSON reader (RFC 8259), for --merge archives

    small on purpose: it reads back what accuchek writes, any formatting,
    without a new build dependency. Refuses what a lenient parser would guess
    about: trailing commas, comments, NaN, duplicate keys, raw control
    characters in strings, nesting deeper than kMaxDepth

 */

#ifndef __JSON_H__
    #define __JSON_H__

    #include <string>
    #include <vector>
    #include <utility>
    #include <stdint.h>

    namespace accuchek {
    namespace json {

    static constexpr int kMaxDepth = 64;

    struct Value {
        enum Type { kNull, kBool, kNumber, kString, kArray, kObject };
        Type type = kNull;
        bool boolean = false;
        double number = 0;
        bool isInteger = false;     // written without fraction nor exponent, fits int64
        int64_t integer = 0;
        std::string string;         // UTF-8, escapes decoded
        std::vector<Value> array;
        std::vector<std::pair<std::string, Value>> object;    // in file order

        // member of an object, 0 when absent or not an object
        const Value *find(const std::string &key) const;
    };

    // one value, whitespace around it; false with error ("line 3: ...") otherwise
    bool parse(const std::string &text, Value &out, std::string &error);

    } // namespace json
    } // namespace accuchek

#endif // __JSON_H__
