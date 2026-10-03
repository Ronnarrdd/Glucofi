#include <json.h>
#include <errno.h>
#include <stdlib.h>
#include <string.h>
#include <algorithm>

namespace accuchek {
namespace json {

const Value *Value::find(
    const std::string &key
) const {
    if(kObject!=type) {
        return 0;
    }
    for(const auto &member : object) {
        if(member.first==key) {
            return &member.second;
        }
    }
    return 0;
}

namespace {

struct Parser {
    const std::string &text;
    size_t at = 0;
    std::string error;

    explicit Parser(const std::string &_text) : text(_text) {}

    bool fail(
        const std::string &what
    ) {
        if(error.empty()) {
            auto line = 1 + std::count(text.begin(), text.begin() + std::min(at, text.size()), '\n');
            error = "line " + std::to_string(line) + ": " + what;
        }
        return false;
    }

    bool atEnd() const { return at>=text.size(); }
    char peek() const { return atEnd() ? 0 : text[at]; }

    void skipSpace() {
        while(!atEnd() && (' '==text[at] || '\t'==text[at] || '\n'==text[at] || '\r'==text[at])) {
            ++at;
        }
    }

    bool literal(
        const char *word
    ) {
        auto n = strlen(word);
        if(0!=text.compare(at, n, word)) {
            return fail("unexpected character");
        }
        at += n;
        return true;
    }

    static void appendUtf8(
        std::string &out,
        uint32_t c
    ) {
        if(c<0x80) {
            out += char(c);
        } else if(c<0x800) {
            out += char(0xC0 | (c >> 6));
            out += char(0x80 | (c & 0x3F));
        } else if(c<0x10000) {
            out += char(0xE0 | (c >> 12));
            out += char(0x80 | ((c >> 6) & 0x3F));
            out += char(0x80 | (c & 0x3F));
        } else {
            out += char(0xF0 | (c >> 18));
            out += char(0x80 | ((c >> 12) & 0x3F));
            out += char(0x80 | ((c >> 6) & 0x3F));
            out += char(0x80 | (c & 0x3F));
        }
    }

    bool hex4(
        uint32_t &v
    ) {
        v = 0;
        for(int i=0; i<4; ++i, ++at) {
            auto c = peek();
            int d = ('0'<=c && c<='9') ? c - '0' : ('a'<=c && c<='f') ? c - 'a' + 10 : ('A'<=c && c<='F') ? c - 'A' + 10 : -1;
            if(d<0) {
                return fail("bad \\u escape");
            }
            v = (v << 4) | uint32_t(d);
        }
        return true;
    }

    bool string(
        std::string &out
    ) {
        ++at;   // opening quote
        out.clear();
        while(true) {
            if(atEnd()) {
                return fail("unterminated string");
            }
            auto c = (unsigned char)text[at++];
            if('"'==c) {
                return true;
            }
            if(c<0x20) {
                return fail("control character in string");
            }
            if('\\'!=c) {
                out += char(c);
                continue;
            }
            if(atEnd()) {
                return fail("unterminated string");
            }
            auto e = text[at++];
            switch(e) {
                case '"': out += '"'; break;
                case '\\': out += '\\'; break;
                case '/': out += '/'; break;
                case 'b': out += '\b'; break;
                case 'f': out += '\f'; break;
                case 'n': out += '\n'; break;
                case 'r': out += '\r'; break;
                case 't': out += '\t'; break;
                case 'u': {
                    uint32_t u = 0;
                    if(!hex4(u)) {
                        return false;
                    }
                    if(0xD800<=u && u<=0xDBFF) {
                        uint32_t low = 0;
                        if(0!=text.compare(at, 2, "\\u") || (at += 2, !hex4(low)) || low<0xDC00 || 0xDFFF<low) {
                            return fail("lone surrogate in \\u escape");
                        }
                        u = 0x10000 + ((u - 0xD800) << 10) + (low - 0xDC00);
                    } else if(0xDC00<=u && u<=0xDFFF) {
                        return fail("lone surrogate in \\u escape");
                    }
                    appendUtf8(out, u);
                    break;
                }
                default:
                    return fail("bad escape in string");
            }
        }
    }

    bool digits() {
        auto start = at;
        while(!atEnd() && '0'<=text[at] && text[at]<='9') {
            ++at;
        }
        return at>start;
    }

    bool number(
        Value &v
    ) {
        auto start = at;
        if('-'==peek()) {
            ++at;
        }
        if('0'==peek()) {
            ++at;
        } else if(!digits()) {
            return fail("bad number");
        }
        auto integral = true;
        if('.'==peek()) {
            ++at;
            integral = false;
            if(!digits()) {
                return fail("bad number");
            }
        }
        if('e'==peek() || 'E'==peek()) {
            ++at;
            integral = false;
            if('+'==peek() || '-'==peek()) {
                ++at;
            }
            if(!digits()) {
                return fail("bad number");
            }
        }
        auto token = text.substr(start, at - start);
        v.type = Value::kNumber;
        v.number = strtod(token.c_str(), 0);
        if(integral) {
            errno = 0;
            v.integer = strtoll(token.c_str(), 0, 10);
            v.isInteger = (0==errno);
        }
        return true;
    }

    bool value(
        Value &v,
        int depth
    ) {
        skipSpace();
        if(atEnd()) {
            return fail("unexpected end of text");
        }
        switch(peek()) {
            case '{': return object(v, depth + 1);
            case '[': return array(v, depth + 1);
            case '"': v.type = Value::kString; return string(v.string);
            case 't': v.type = Value::kBool; v.boolean = true; return literal("true");
            case 'f': v.type = Value::kBool; v.boolean = false; return literal("false");
            case 'n': v.type = Value::kNull; return literal("null");
            default:
                if('-'==peek() || ('0'<=peek() && peek()<='9')) {
                    return number(v);
                }
                return fail("unexpected character");
        }
    }

    bool array(
        Value &v,
        int depth
    ) {
        if(kMaxDepth<depth) {
            return fail("nested too deep");
        }
        ++at;
        v.type = Value::kArray;
        skipSpace();
        if(']'==peek()) {
            ++at;
            return true;
        }
        while(true) {
            v.array.emplace_back();
            if(!value(v.array.back(), depth)) {
                return false;
            }
            skipSpace();
            if(','==peek()) {
                ++at;
                continue;
            }
            if(']'==peek()) {
                ++at;
                return true;
            }
            return fail("expected , or ] in array");
        }
    }

    bool object(
        Value &v,
        int depth
    ) {
        if(kMaxDepth<depth) {
            return fail("nested too deep");
        }
        ++at;
        v.type = Value::kObject;
        skipSpace();
        if('}'==peek()) {
            ++at;
            return true;
        }
        while(true) {
            skipSpace();
            if('"'!=peek()) {
                return fail("expected a string key in object");
            }
            std::string key;
            if(!string(key)) {
                return false;
            }
            if(0!=v.find(key)) {
                return fail("duplicate key \"" + key + "\"");
            }
            skipSpace();
            if(':'!=peek()) {
                return fail("expected : after key");
            }
            ++at;
            v.object.emplace_back(key, Value());
            if(!value(v.object.back().second, depth)) {
                return false;
            }
            skipSpace();
            if(','==peek()) {
                ++at;
                continue;
            }
            if('}'==peek()) {
                ++at;
                return true;
            }
            return fail("expected , or } in object");
        }
    }
};

} // namespace

bool parse(
    const std::string &text,
    Value &out,
    std::string &error
) {
    Parser p(text);
    out = Value();
    if(!p.value(out, 0)) {
        error = p.error;
        return false;
    }
    p.skipSpace();
    if(!p.atEnd()) {
        p.fail("text after the JSON value");
        error = p.error;
        return false;
    }
    return true;
}

} // namespace json
} // namespace accuchek
