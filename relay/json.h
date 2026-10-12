/* Minimal in-place JSON reader for TeamSpeak Remote Apps messages. */
#ifndef GRIDTALK_JSON_H
#define GRIDTALK_JSON_H
#include <stddef.h>

enum { JSON_NULL, JSON_FALSE, JSON_TRUE, JSON_NUMBER, JSON_STRING, JSON_ARRAY, JSON_OBJECT };

typedef struct {
    int type;
    int next;           /* next sibling, or -1 */
    int child;          /* first child of an array or object, or -1 */
    const char* key;    /* member name inside an object, otherwise NULL */
    const char* text;   /* decoded string (NUL-terminated), or a number's literal text */
    size_t length;      /* bytes in text */
} json_node;

typedef struct {
    json_node* nodes;
    int count, capacity;
    int limit;          /* maximum nodes per document; 0 for no limit */
} json_doc;

/* Parses length bytes of text, which must be followed by a NUL. Strings are
   decoded into the same buffer. The root is node 0. Returns 0, or -1 for input
   that is not strict RFC 8259 JSON (including unescaped control characters and
   malformed numbers), nesting deeper than 64 levels, or more than limit nodes. */
int json_parse(json_doc* doc, char* text, size_t length);
void json_release(json_doc* doc);

/* Accessors return -1 / NULL / 0 for a missing node (index -1) or a wrong type. */
int json_get(const json_doc* doc, int object, const char* key);
const char* json_string(const json_doc* doc, int node);
/* A number or numeric string with an integer value that fits in 64 bits. */
int json_integer(const json_doc* doc, int node, long long* value);
/* true/false, 0/1, or the strings "true"/"false"/"0"/"1". */
int json_boolean(const json_doc* doc, int node, int* value);
#endif
