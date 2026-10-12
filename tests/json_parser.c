/* Unit test for the relay's JSON reader (relay/json.c): strict RFC 8259
   acceptance, decoding, accessors, and the depth and node limits. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "../relay/json.h"

static int failures;

/* Parses a copy (the parser decodes in place), length given so NUL can be tested. */
static int parses(const char* text, size_t length, json_doc* doc) {
    char* copy = (char*)malloc(length + 1);
    int result;
    memcpy(copy, text, length);
    copy[length] = 0;
    result = json_parse(doc, copy, length) == 0;
    free(copy);
    return result;
}

static void expect(const char* text, size_t length, int valid) {
    json_doc doc = { NULL, 0, 0, 0 };
    if (parses(text, length, &doc) != valid) {
        printf("FAIL: %s should be %s\n", text, valid ? "accepted" : "rejected");
        ++failures;
    }
    json_release(&doc);
}

#define VALID(text) expect(text, sizeof(text) - 1, 1)
#define INVALID(text) expect(text, sizeof(text) - 1, 0)

static void check(int condition, const char* what) {
    if (!condition) {
        printf("FAIL: %s\n", what);
        ++failures;
    }
}

int main(void) {
    static const char* const numbers[] = { "0", "-0", "7", "-12", "120", "1.5", "-0.25", "1e5", "1E+5", "2e-3", "1.25E10" };
    static const char* const bad_numbers[] = {
        "01", "-01", "00", "-", "+1", "--1", ".5", "1.", "1.e5", "1e", "1e+", "1E-", "1-2", "1+2", "1.2.3",
        "1e5e5", "0x10", "1ee5", "-.5", "Infinity", "NaN"
    };
    char text[64], deep[256];
    size_t i;
    json_doc doc = { NULL, 0, 0, 0 };
    long long number;
    int flag;

    /* Numbers follow the RFC 8259 grammar, alone and inside containers. */
    for (i = 0; i < sizeof(numbers) / sizeof(numbers[0]); ++i) {
        expect(numbers[i], strlen(numbers[i]), 1);
        snprintf(text, sizeof(text), "[%s]", numbers[i]);
        expect(text, strlen(text), 1);
        snprintf(text, sizeof(text), "{\"n\":%s}", numbers[i]);
        expect(text, strlen(text), 1);
    }
    for (i = 0; i < sizeof(bad_numbers) / sizeof(bad_numbers[0]); ++i) {
        expect(bad_numbers[i], strlen(bad_numbers[i]), 0);
        snprintf(text, sizeof(text), "[%s]", bad_numbers[i]);
        expect(text, strlen(text), 0);
        snprintf(text, sizeof(text), "{\"n\":%s}", bad_numbers[i]);
        expect(text, strlen(text), 0);
    }

    /* Control characters must be escaped inside strings; escapes are decoded. */
    INVALID("\"a\nb\"");
    INVALID("\"a\tb\"");
    INVALID("\"a\rb\"");
    INVALID("{\"k\x01\":1}");
    INVALID("\"a\0b\"");
    VALID("\"a\\nb\\t\\u0001\"");
    VALID("\"\x7f\"");  /* DEL is not a control character in JSON */

    /* Other structural errors. */
    INVALID("");
    INVALID("[1,]");
    INVALID("{\"a\" 1}");
    INVALID("{\"a\":1,}");
    INVALID("\"unterminated");
    INVALID("\"bad escape \\x\"");
    INVALID("\"short \\u12\"");
    INVALID("1 2");
    INVALID("tru");
    INVALID("nul");
    VALID(" \r\n\t{ \"a\" : [ true , false , null , \"s\" , { } , [ ] ] } \n");

    /* Decoding and accessors. */
    {
        char source[] = "{\"name\":\"Jas\\u00e9 \\ud83d\\ude00 \\ud800!\",\"id\":42,\"big\":\"18446744073709551615\","
                        "\"half\":5.0,\"exp\":1e3,\"on\":true,\"one\":\"1\",\"neg\":-7}";
        check(json_parse(&doc, source, sizeof(source) - 1) == 0, "accessor document parses in place");
        check(json_string(&doc, json_get(&doc, 0, "name")) != NULL &&
              strcmp(json_string(&doc, json_get(&doc, 0, "name")), "Jas\xc3\xa9 \xf0\x9f\x98\x80 \xef\xbf\xbd!") == 0,
              "escapes decode to UTF-8; an unpaired surrogate becomes U+FFFD");
        check(json_integer(&doc, json_get(&doc, 0, "id"), &number) && number == 42, "integer");
        check(json_integer(&doc, json_get(&doc, 0, "neg"), &number) && number == -7, "negative integer");
        check(json_integer(&doc, json_get(&doc, 0, "half"), &number) && number == 5, "5.0 is an integer");
        check(!json_integer(&doc, json_get(&doc, 0, "exp"), &number), "exponent forms are not read as integers");
        check(!json_integer(&doc, json_get(&doc, 0, "big"), &number), "out-of-range integers are refused");
        check(json_boolean(&doc, json_get(&doc, 0, "on"), &flag) && flag == 1, "true");
        check(json_boolean(&doc, json_get(&doc, 0, "one"), &flag) && flag == 1, "\"1\" as a boolean");
        check(json_get(&doc, 0, "missing") == -1 && json_get(&doc, -1, "id") == -1, "missing members");
        check(json_string(&doc, json_get(&doc, 0, "id")) == NULL, "wrong type");
    }
    json_release(&doc);

    /* Nesting is limited to 64 levels. */
    for (i = 0; i < 64; ++i) deep[i] = '[';
    for (i = 0; i < 64; ++i) deep[64 + i] = ']';
    expect(deep, 128, 1);
    memmove(deep + 1, deep, 128);
    deep[0] = '[';
    deep[129] = ']';
    expect(deep, 130, 0);

    /* The node limit bounds memory for hostile input. */
    doc.limit = 4;
    {
        char source[] = "[1,2,3]";
        check(json_parse(&doc, source, sizeof(source) - 1) == 0, "within the node limit");
    }
    {
        char source[] = "[1,2,3,4]";
        check(json_parse(&doc, source, sizeof(source) - 1) != 0, "over the node limit");
    }
    json_release(&doc);

    if (failures) {
        printf("%d JSON parser check(s) failed\n", failures);
        return 1;
    }
    printf("PASS: relay JSON parser: strict numbers and strings, decoding, accessors, depth and node limits\n");
    return 0;
}
