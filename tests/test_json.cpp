#include "check.h"
#include <json.h>

using namespace accuchek;

static json::Value parsed(
    const std::string &text
) {
    json::Value v;
    std::string error;
    CHECK(json::parse(text, v, error));
    CHECK_EQ(error, std::string(""));
    return v;
}

static std::string refused(
    const std::string &text
) {
    json::Value v;
    std::string error;
    CHECK(!json::parse(text, v, error));
    return error;
}

TEST(json_values) {
    auto v = parsed(" {\"a\": [1, -2.5, 3e2, true, false, null, \"x\"], \"b\": {}} \n");
    CHECK_EQ((int)v.type, (int)json::Value::kObject);
    auto a = v.find("a");
    CHECK(0!=a);
    if(a) {
        CHECK_EQ(a->array.size(), 7u);
        CHECK(a->array[0].isInteger);
        CHECK_EQ(a->array[0].integer, 1);
        CHECK(!a->array[1].isInteger);
        CHECK_EQ(a->array[1].number, -2.5);
        CHECK(!a->array[2].isInteger);
        CHECK_EQ(a->array[2].number, 300.0);
        CHECK(a->array[3].boolean);
        CHECK_EQ((int)a->array[5].type, (int)json::Value::kNull);
        CHECK_EQ(a->array[6].string, std::string("x"));
    }
    CHECK(0!=v.find("b"));
    CHECK(0==v.find("c"));
    CHECK(0==a->find("a"));     // not an object
    CHECK_EQ(parsed("[]").array.size(), 0u);
    CHECK_EQ(parsed("-0").integer, 0);
    CHECK_EQ(parsed("9223372036854775807").integer, 9223372036854775807LL);
    CHECK(!parsed("9223372036854775808").isInteger);
}

TEST(json_string_escapes_decoded) {
    CHECK_EQ(parsed("\"a\\\"b\\\\c\\/\\n\\t\"").string, std::string("a\"b\\c/\n\t"));
    CHECK_EQ(parsed("\"\\u00e9\"").string, std::string("\xC3\xA9"));
    CHECK_EQ(parsed("\"\\u20AC\"").string, std::string("\xE2\x82\xAC"));
    CHECK_EQ(parsed("\"\\ud83d\\ude00\"").string, std::string("\xF0\x9F\x98\x80"));
    // what jsonString() writes comes back unchanged
    CHECK_EQ(parsed("\"\\u000a\\u0001\"").string, std::string("\n\x01"));
}

TEST(json_refuses_what_it_would_have_to_guess) {
    CHECK_EQ(refused(""), std::string("line 1: unexpected end of text"));
    CHECK_EQ(refused("[1,]"), std::string("line 1: unexpected character"));
    CHECK_EQ(refused("{\"a\":1,}"), std::string("line 1: expected a string key in object"));
    CHECK_EQ(refused("{\"a\":1, \"a\":2}"), std::string("line 1: duplicate key \"a\""));
    CHECK_EQ(refused("[1] [2]"), std::string("line 1: text after the JSON value"));
    CHECK_EQ(refused("\n\n[01]"), std::string("line 3: expected , or ] in array"));
    CHECK_EQ(refused("[NaN]"), std::string("line 1: unexpected character"));
    CHECK_EQ(refused("[1.]"), std::string("line 1: bad number"));
    CHECK_EQ(refused("[-]"), std::string("line 1: bad number"));
    CHECK_EQ(refused("[1e]"), std::string("line 1: bad number"));
    CHECK_EQ(refused("[+1]"), std::string("line 1: unexpected character"));
    CHECK_EQ(refused("// c\n[]"), std::string("line 1: unexpected character"));
    CHECK_EQ(refused("\"a\nb\""), std::string("line 2: control character in string"));
    CHECK_EQ(refused("\"abc"), std::string("line 1: unterminated string"));
    CHECK_EQ(refused("\"\\x\""), std::string("line 1: bad escape in string"));
    CHECK_EQ(refused("\"\\u12\""), std::string("line 1: bad \\u escape"));
    CHECK_EQ(refused("\"\\ud83d\""), std::string("line 1: lone surrogate in \\u escape"));
    CHECK_EQ(refused("\"\\ude00\""), std::string("line 1: lone surrogate in \\u escape"));
    CHECK_EQ(refused("{\"a\" 1}"), std::string("line 1: expected : after key"));
    CHECK_EQ(refused("tru"), std::string("line 1: unexpected character"));
    CHECK_EQ(refused("[1 2]"), std::string("line 1: expected , or ] in array"));
}

// a deeply nested file must not exhaust the stack
TEST(json_nesting_is_bounded) {
    std::string ok(json::kMaxDepth, '[');
    ok += std::string(json::kMaxDepth, ']');
    parsed(ok);
    std::string deep(100000, '[');
    CHECK_EQ(refused(deep), std::string("line 1: nested too deep"));
    std::string objects;
    for(int i=0; i<1000; ++i) {
        objects += "{\"a\":";
    }
    CHECK_EQ(refused(objects), std::string("line 1: nested too deep"));
}
