/* Exercise checked append/escape helpers without adding production exports. */
#include "../src/plugin.c"
#undef NDEBUG /* These boundary checks must run in Release builds too. */
#include <assert.h>
int main(void) {
    char output[16];
    size_t used = 0;
    assert(!escape_name("x", NULL, 0));
    assert(escape_name("\1", output, 7));
    assert(strcmp(output, "\\u0001") == 0);
    assert(!escape_name("\1", output, 6) && output[0] == 0);
    assert(escape_name("\"\\", output, sizeof(output)));
    assert(strcmp(output, "\\\"\\\\") == 0);
    assert(append_json(output, sizeof(output), &used, "%s", "abc"));
    assert(used == 3 && strcmp(output, "abc") == 0);
    assert(!append_json(output, 5, &used, "%s", "12345"));
    assert(used == 3 && strcmp(output, "abc") == 0);
    used = sizeof(output);
    assert(!append_json(output, sizeof(output), &used, "%s", "x"));
    puts("PASS: checked native formatter/escape capacity boundaries");
    return 0;
}
