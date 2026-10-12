#include <stdlib.h>
#include <string.h>
#include "json.h"

#define MAX_DEPTH 64

typedef struct {
    json_doc* doc;
    char* p;
    char* end;
    int depth;
} parser;

static int add_node(parser* s, int type) {
    json_doc* doc = s->doc;
    json_node* node;
    if (doc->count == doc->capacity) {
        int capacity = doc->capacity ? doc->capacity * 2 : 256;
        json_node* nodes;
        if (doc->limit > 0 && capacity > doc->limit) capacity = doc->limit;
        if (capacity <= doc->count) return -1;
        nodes = (json_node*)realloc(doc->nodes, (size_t)capacity * sizeof(*nodes));
        if (!nodes) return -1;
        doc->nodes = nodes;
        doc->capacity = capacity;
    }
    node = &doc->nodes[doc->count];
    node->type = type;
    node->next = node->child = -1;
    node->key = node->text = NULL;
    node->length = 0;
    return doc->count++;
}

static void skip_space(parser* s) {
    while (s->p < s->end && (*s->p == ' ' || *s->p == '\t' || *s->p == '\n' || *s->p == '\r')) ++s->p;
}

static int hex4(const char* p, unsigned* value) {
    unsigned result = 0;
    int i;
    for (i = 0; i < 4; ++i) {
        char c = p[i];
        result <<= 4;
        if (c >= '0' && c <= '9') result |= (unsigned)(c - '0');
        else if (c >= 'a' && c <= 'f') result |= (unsigned)(c - 'a' + 10);
        else if (c >= 'A' && c <= 'F') result |= (unsigned)(c - 'A' + 10);
        else return 0;
    }
    *value = result;
    return 1;
}

static char* put_utf8(char* out, unsigned cp) {
    if (cp < 0x80) {
        *out++ = (char)cp;
    } else if (cp < 0x800) {
        *out++ = (char)(0xc0 | (cp >> 6));
        *out++ = (char)(0x80 | (cp & 0x3f));
    } else if (cp < 0x10000) {
        *out++ = (char)(0xe0 | (cp >> 12));
        *out++ = (char)(0x80 | ((cp >> 6) & 0x3f));
        *out++ = (char)(0x80 | (cp & 0x3f));
    } else {
        *out++ = (char)(0xf0 | (cp >> 18));
        *out++ = (char)(0x80 | ((cp >> 12) & 0x3f));
        *out++ = (char)(0x80 | ((cp >> 6) & 0x3f));
        *out++ = (char)(0x80 | (cp & 0x3f));
    }
    return out;
}

/* Decodes the string starting at the opening quote into the same buffer. The
   decoded form is never longer than its escaped source, so writing trails reading. */
static int parse_string(parser* s, const char** text, size_t* length) {
    char* start;
    char* out;
    if (s->p >= s->end || *s->p != '"') return 0;
    start = out = ++s->p;
    for (;;) {
        char c;
        if (s->p >= s->end) return 0;
        c = *s->p++;
        if (c == '"') break;
        if ((unsigned char)c < 0x20) return 0;  /* control characters must be escaped */
        if (c != '\\') {
            *out++ = c;
            continue;
        }
        if (s->p >= s->end) return 0;
        c = *s->p++;
        switch (c) {
        case '"': case '\\': case '/': *out++ = c; break;
        case 'b': *out++ = '\b'; break;
        case 'f': *out++ = '\f'; break;
        case 'n': *out++ = '\n'; break;
        case 'r': *out++ = '\r'; break;
        case 't': *out++ = '\t'; break;
        case 'u': {
            unsigned cp, low;
            if (s->end - s->p < 4 || !hex4(s->p, &cp)) return 0;
            s->p += 4;
            if (cp >= 0xd800 && cp <= 0xdbff && s->end - s->p >= 6 && s->p[0] == '\\' && s->p[1] == 'u' &&
                    hex4(s->p + 2, &low) && low >= 0xdc00 && low <= 0xdfff) {
                cp = 0x10000 + ((cp - 0xd800) << 10) + (low - 0xdc00);
                s->p += 6;
            } else if (cp >= 0xd800 && cp <= 0xdfff) {
                cp = 0xfffd;  /* unpaired surrogate */
            }
            out = put_utf8(out, cp);
            break;
        }
        default:
            return 0;
        }
    }
    *out = 0;  /* at or before the closing quote, which has been consumed */
    *text = start;
    *length = (size_t)(out - start);
    return 1;
}

/* Consumes one or more digits; returns 0 if there were none. */
static int skip_digits(parser* s) {
    char* start = s->p;
    while (s->p < s->end && *s->p >= '0' && *s->p <= '9') ++s->p;
    return s->p != start;
}

static int parse_literal(parser* s, const char* word, int type) {
    size_t length = strlen(word);
    if ((size_t)(s->end - s->p) < length || memcmp(s->p, word, length) != 0) return -1;
    s->p += length;
    return add_node(s, type);
}

static int parse_value(parser* s) {
    int node;
    skip_space(s);
    if (s->p >= s->end) return -1;
    switch (*s->p) {
    case '{':
    case '[': {
        int object = *s->p == '{', last = -1;
        char close = object ? '}' : ']';
        if (++s->depth > MAX_DEPTH) return -1;
        node = add_node(s, object ? JSON_OBJECT : JSON_ARRAY);
        if (node < 0) return -1;
        ++s->p;
        skip_space(s);
        if (s->p < s->end && *s->p == close) {
            ++s->p;
            --s->depth;
            return node;
        }
        for (;;) {
            const char* key = NULL;
            size_t key_length;
            int child;
            if (object) {
                skip_space(s);
                if (!parse_string(s, &key, &key_length)) return -1;
                skip_space(s);
                if (s->p >= s->end || *s->p != ':') return -1;
                ++s->p;
            }
            child = parse_value(s);
            if (child < 0) return -1;
            /* Index the array afresh: adding nodes may have moved it. */
            s->doc->nodes[child].key = key;
            if (last < 0) s->doc->nodes[node].child = child;
            else s->doc->nodes[last].next = child;
            last = child;
            skip_space(s);
            if (s->p < s->end && *s->p == ',') { ++s->p; continue; }
            if (s->p < s->end && *s->p == close) { ++s->p; break; }
            return -1;
        }
        --s->depth;
        return node;
    }
    case '"': {
        const char* text;
        size_t length;
        node = add_node(s, JSON_STRING);
        if (node < 0 || !parse_string(s, &text, &length)) return -1;
        s->doc->nodes[node].text = text;
        s->doc->nodes[node].length = length;
        return node;
    }
    case 't': return parse_literal(s, "true", JSON_TRUE);
    case 'f': return parse_literal(s, "false", JSON_FALSE);
    case 'n': return parse_literal(s, "null", JSON_NULL);
    default: {
        /* RFC 8259: -? (0 | [1-9][0-9]*) (. [0-9]+)? ([eE] [+-]? [0-9]+)? */
        char* start = s->p;
        if (*s->p == '-') ++s->p;
        if (s->p < s->end && *s->p == '0') ++s->p;
        else if (!skip_digits(s)) return -1;
        if (s->p < s->end && *s->p == '.') {
            ++s->p;
            if (!skip_digits(s)) return -1;
        }
        if (s->p < s->end && (*s->p == 'e' || *s->p == 'E')) {
            ++s->p;
            if (s->p < s->end && (*s->p == '+' || *s->p == '-')) ++s->p;
            if (!skip_digits(s)) return -1;
        }
        node = add_node(s, JSON_NUMBER);
        if (node < 0) return -1;
        s->doc->nodes[node].text = start;
        s->doc->nodes[node].length = (size_t)(s->p - start);
        return node;
    }
    }
}

int json_parse(json_doc* doc, char* text, size_t length) {
    parser s;
    s.doc = doc;
    s.p = text;
    s.end = text + length;
    s.depth = 0;
    doc->count = 0;
    if (parse_value(&s) != 0) return -1;
    skip_space(&s);
    return s.p == s.end ? 0 : -1;
}

void json_release(json_doc* doc) {
    free(doc->nodes);
    doc->nodes = NULL;
    doc->count = doc->capacity = 0;
}

int json_get(const json_doc* doc, int object, const char* key) {
    int child;
    if (object < 0 || doc->nodes[object].type != JSON_OBJECT) return -1;
    for (child = doc->nodes[object].child; child >= 0; child = doc->nodes[child].next)
        if (strcmp(doc->nodes[child].key, key) == 0) return child;
    return -1;
}

const char* json_string(const json_doc* doc, int node) {
    return node >= 0 && doc->nodes[node].type == JSON_STRING ? doc->nodes[node].text : NULL;
}

int json_integer(const json_doc* doc, int node, long long* value) {
    const char* p;
    size_t length, i = 0;
    unsigned long long result = 0;
    int negative = 0, digits = 0;
    if (node < 0 || (doc->nodes[node].type != JSON_NUMBER && doc->nodes[node].type != JSON_STRING)) return 0;
    p = doc->nodes[node].text;
    length = doc->nodes[node].length;
    if (i < length && p[i] == '-') { negative = 1; ++i; }
    for (; i < length && p[i] >= '0' && p[i] <= '9'; ++i, ++digits) {
        if (result > 922337203685477580ULL) return 0;
        result = result * 10 + (unsigned)(p[i] - '0');
    }
    if (digits && i < length && p[i] == '.') {  /* 5.0 is still an integer */
        for (++i; i < length && p[i] == '0'; ++i) {}
    }
    if (!digits || i != length || result > 9223372036854775807ULL) return 0;
    *value = negative ? -(long long)result : (long long)result;
    return 1;
}

int json_boolean(const json_doc* doc, int node, int* value) {
    long long number;
    if (node < 0) return 0;
    if (doc->nodes[node].type == JSON_TRUE || doc->nodes[node].type == JSON_FALSE) {
        *value = doc->nodes[node].type == JSON_TRUE;
        return 1;
    }
    if (doc->nodes[node].type == JSON_STRING &&
            (strcmp(doc->nodes[node].text, "true") == 0 || strcmp(doc->nodes[node].text, "false") == 0)) {
        *value = doc->nodes[node].text[0] == 't';
        return 1;
    }
    if (json_integer(doc, node, &number) && (number == 0 || number == 1)) {
        *value = (int)number;
        return 1;
    }
    return 0;
}
