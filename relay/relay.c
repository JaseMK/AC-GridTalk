/* GridTalk TS6 Relay (GridTalk-TS6-Relay.exe): forwards TeamSpeak 5/6 Remote
   Apps events to the GridTalk overlay as protocol v2 UDP packets (see docs/SENDER_PROTOCOL.md).

   One thread does all network work. It sleeps until a socket event, the
   one-second snapshot tick, a reconnect deadline, or a stop request. */
#define WIN32_LEAN_AND_MEAN
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>
#include <bcrypt.h>
#include <shellapi.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "json.h"

#ifndef GRIDTALK_VERSION
#error "GRIDTALK_VERSION must be defined by the build (CMake project version)"
#endif
#ifndef GRIDTALK_UDP_PORT
#define GRIDTALK_UDP_PORT 9999
#endif

#define STRINGIFY_INNER(value) #value
#define STRINGIFY(value) STRINGIFY_INNER(value)
#define SOURCE "teamspeak-remote"
#define APP_IDENTIFIER "gridtalk.relay"
#define DEFAULT_TS_PORT 5899
#define MAX_MESSAGE (8u << 20)          /* largest TeamSpeak message accepted */
#define MAX_NODES (1 << 20)
#define MAX_MEMBERS 4096                /* across every server tab */
#define MAX_CHANNEL_MEMBERS 256         /* the overlay's own limit */
#define NAME_BYTES 500                  /* keeps names within the protocol's 512 characters */
#define ID_CHARS 32
#define HANDSHAKE_TIMEOUT_MS 5000
#define APPROVAL_HINT_MS 1500
#define DENIED_RETRY_FACTOR 5
#define KEEP_BUFFER (1u << 20)          /* larger buffers are released after use */
#define STATUS_MESSAGE (WM_APP + 1)
#define TRAY_MESSAGE (WM_APP + 2)
#define MENU_EXIT 1
#define MENU_AUTOSTART 2
#define RUN_VALUE L"GridTalk TS6 Relay"

enum { STATUS_WAITING, STATUS_APPROVAL, STATUS_NO_SERVER, STATUS_LIVE };
/* Release packaging checks this text for the version and the default port. */
static const char build_info[] = "GridTalk TS6 Relay " GRIDTALK_VERSION " (default UDP 127.0.0.1:" STRINGIFY(GRIDTALK_UDP_PORT) ")";
static const wchar_t* const status_text[] = {
    L"GridTalk TS6 Relay: waiting for TeamSpeak",
    L"GridTalk TS6 Relay: allow GridTalk in TeamSpeak",
    L"GridTalk TS6 Relay: TeamSpeak is not on a server",
    L"GridTalk TS6 Relay: sending your channel to GridTalk",
};
static const char* const status_log[] = {
    "Waiting for TeamSpeak (Remote Apps on port %u)",
    "Waiting for you to allow GridTalk in TeamSpeak",
    "Connected to TeamSpeak; not on a server",
    "Sending the current channel to GridTalk",
};

typedef struct {
    unsigned connection, id;
    char channel[ID_CHARS];
    char* name;
    int talking;
} member;

typedef struct {
    unsigned id, self;      /* self is 0 until TeamSpeak reports our client ID */
    int input;              /* our microphone is open on this tab: the active one */
    ULONGLONG active_since;
} connection;

typedef struct {
    unsigned connection;
    char id[ID_CHARS];
    char* name;
} channel;

enum { LINK_IDLE, LINK_CONNECTING, LINK_HANDSHAKE, LINK_AUTH, LINK_READY };

static struct {
    SOCKET socket;
    WSAEVENT event;
    int state;
    ULONGLONG since, retry_at;
    char key[32];               /* Sec-WebSocket-Key */
    int sent_stored_key;
    unsigned char* rx;
    size_t rx_length, rx_capacity;
    char* message;
    size_t message_length, message_capacity;
    int message_open;
    int reported_failure;
} link_state = { INVALID_SOCKET };

static member* members;
static size_t member_count, member_capacity;
static connection* connections;
static size_t connection_count, connection_capacity;
static channel* channels;
static size_t channel_count, channel_capacity;
static json_doc doc = { NULL, 0, 0, MAX_NODES };

static unsigned short ts_port = DEFAULT_TS_PORT, udp_port = GRIDTALK_UDP_PORT;
static unsigned retry_ms = 3000;
static int dump_mode;
static wchar_t config_path[MAX_PATH];
/* Tests pass --run-key so they never touch the real Run key. */
static wchar_t run_key[200] = L"Software\\Microsoft\\Windows\\CurrentVersion\\Run";
static char api_key[257];
static HANDLE log_handle = INVALID_HANDLE_VALUE;
static HANDLE stop_event;
static HWND tray_window;
static int status = -1;
static SOCKET udp = INVALID_SOCKET;
static struct sockaddr_in udp_target;
static int reported_udp_error;
static unsigned long long snapshot_id;
static int published;       /* the overlay holds state from this relay */
static int roster_dirty;

/* ---- logging and status ------------------------------------------------ */

static void log_line(const char* format, ...) {
    char line[1024];
    SYSTEMTIME now;
    int used;
    va_list arguments;
    DWORD written;
    if (log_handle == INVALID_HANDLE_VALUE) return;
    GetLocalTime(&now);
    used = snprintf(line, sizeof(line), "%02u:%02u:%02u ", now.wHour, now.wMinute, now.wSecond);
    va_start(arguments, format);
    used += vsnprintf(line + used, sizeof(line) - (size_t)used - 2, format, arguments);
    va_end(arguments);
    if (used < 0 || (size_t)used > sizeof(line) - 3) used = (int)sizeof(line) - 3;
    line[used++] = '\r';
    line[used++] = '\n';
    WriteFile(log_handle, line, (DWORD)used, &written, NULL);
}

static void set_status(int value) {
    if (value == status) return;
    status = value;
    log_line(status_log[value], (unsigned)ts_port);
    if (tray_window) PostMessageW(tray_window, STATUS_MESSAGE, (WPARAM)value, 0);
}

/* ---- configuration ------------------------------------------------------ */

static int printable_key(const char* key);

static void load_config(void) {
    FILE* file = _wfopen(config_path, L"r");
    char line[400];
    api_key[0] = 0;
    if (!file) return;
    while (fgets(line, sizeof(line), file)) {
        size_t length;
        if (strncmp(line, "api_key=", 8) != 0) continue;
        length = strcspn(line + 8, "\r\n \t");
        if (length < sizeof(api_key)) {
            memcpy(api_key, line + 8, length);
            api_key[length] = 0;
        }
    }
    fclose(file);
    if (!printable_key(api_key)) api_key[0] = 0;
}

static void save_config(void) {
    wchar_t folder[MAX_PATH];
    wchar_t* slash;
    FILE* file;
    wcscpy_s(folder, MAX_PATH, config_path);
    slash = wcsrchr(folder, L'\\');
    if (slash) {
        *slash = 0;
        CreateDirectoryW(folder, NULL);
    }
    file = _wfopen(config_path, L"w");
    if (!file) {
        log_line("Could not save the TeamSpeak approval key");
        return;
    }
    fprintf(file, "; Written by GridTalk TS6 Relay. Delete this file to ask TeamSpeak for approval again.\n");
    fprintf(file, "api_key=%s\n", api_key);
    fclose(file);
}

static int printable_key(const char* key) {
    size_t length = strlen(key), i;
    if (length >= sizeof(api_key)) return 0;
    for (i = 0; i < length; ++i)
        if ((unsigned char)key[i] <= ' ' || (unsigned char)key[i] >= 0x7f || key[i] == '"' || key[i] == '\\') return 0;
    return 1;
}

/* ---- UDP output --------------------------------------------------------- */

static void udp_send(const char* packet) {
    if (udp == INVALID_SOCKET) return;
    if (sendto(udp, packet, (int)strlen(packet), 0, (const struct sockaddr*)&udp_target, sizeof(udp_target)) == SOCKET_ERROR &&
            !reported_udp_error) {
        reported_udp_error = 1;
        log_line("UDP send failed: Winsock error %d", WSAGetLastError());
    }
}

static int append(char* out, size_t capacity, size_t* used, const char* format, ...) {
    int written;
    va_list arguments;
    if (*used >= capacity) return 0;
    va_start(arguments, format);
    written = vsnprintf(out + *used, capacity - *used, format, arguments);
    va_end(arguments);
    if (written < 0 || (size_t)written >= capacity - *used) return 0;
    *used += (size_t)written;
    return 1;
}

/* Length of the valid UTF-8 sequence at p, or 0 for an invalid byte. */
static size_t utf8_sequence(const unsigned char* p) {
    unsigned cp;
    size_t length, i;
    if (p[0] < 0x80) return 1;
    if (p[0] >= 0xc2 && p[0] <= 0xdf) { length = 2; cp = p[0] & 0x1fu; }
    else if (p[0] >= 0xe0 && p[0] <= 0xef) { length = 3; cp = p[0] & 0x0fu; }
    else if (p[0] >= 0xf0 && p[0] <= 0xf4) { length = 4; cp = p[0] & 0x07u; }
    else return 0;
    for (i = 1; i < length; ++i) {
        if ((p[i] & 0xc0) != 0x80) return 0;  /* also stops at the terminating NUL */
        cp = (cp << 6) | (p[i] & 0x3fu);
    }
    if ((length == 3 && (cp < 0x800 || (cp >= 0xd800 && cp <= 0xdfff))) ||
            (length == 4 && (cp < 0x10000 || cp > 0x10ffff))) return 0;
    return length;
}

/* JSON-escapes up to limit bytes of text, never splitting a character. Invalid
   UTF-8 becomes U+FFFD, since the overlay drops packets it cannot decode. */
static int append_text(char* out, size_t capacity, size_t* used, const char* text, size_t limit) {
    static const char hex[] = "0123456789abcdef";
    const unsigned char* p = (const unsigned char*)(text ? text : "");
    size_t at = *used, taken = 0;
    while (*p) {
        size_t length = utf8_sequence(p), count;
        char escaped[6];
        const char* bytes = (const char*)p;
        count = length;
        if (!length) {
            bytes = "\xef\xbf\xbd";
            count = 3;
            length = 1;
        } else if (*p == '"' || *p == '\\') {
            escaped[0] = '\\';
            escaped[1] = (char)*p;
            bytes = escaped;
            count = 2;
        } else if (*p < 0x20) {
            escaped[0] = '\\'; escaped[1] = 'u'; escaped[2] = '0'; escaped[3] = '0';
            escaped[4] = hex[*p >> 4]; escaped[5] = hex[*p & 15];
            bytes = escaped;
            count = 6;
        }
        if (taken + length > limit) break;
        if (at + count >= capacity) return 0;
        memcpy(out + at, bytes, count);
        at += count;
        taken += length;
        p += length;
    }
    out[at] = 0;
    *used = at;
    return 1;
}

static void send_reset(void) {
    udp_send("{\"v\":2,\"source\":\"" SOURCE "\",\"event\":\"reset\"}");
}

static void send_bridge_status(const char* message) {
    char packet[512];
    size_t used = 0;
    if (append(packet, sizeof(packet), &used, "{\"v\":2,\"source\":\"" SOURCE "\",\"event\":\"bridge_status\",\"message\":\"") &&
            append_text(packet, sizeof(packet), &used, message, 256) &&
            append(packet, sizeof(packet), &used, "\"}")) {
        udp_send(packet);
        published = 1;
    }
}

/* ---- roster model -------------------------------------------------------- */

static int grow(void** array, size_t* capacity, size_t needed, size_t size) {
    size_t next;
    void* grown;
    if (needed <= *capacity) return 1;
    next = *capacity ? *capacity * 2 : 16;
    while (next < needed) next *= 2;
    grown = realloc(*array, next * size);
    if (!grown) return 0;
    *array = grown;
    *capacity = next;
    return 1;
}

static void set_text(char** slot, const char* text) {
    size_t length = strlen(text);
    char* copy;
    if (*slot && strcmp(*slot, text) == 0) return;
    copy = (char*)malloc(length + 1);
    if (!copy) return;
    memcpy(copy, text, length + 1);
    free(*slot);
    *slot = copy;
}

static connection* find_connection(unsigned id) {
    size_t i;
    for (i = 0; i < connection_count; ++i)
        if (connections[i].id == id) return &connections[i];
    return NULL;
}

static connection* add_connection(unsigned id) {
    connection* c = find_connection(id);
    if (c) return c;
    if (!grow((void**)&connections, &connection_capacity, connection_count + 1, sizeof(*connections))) return NULL;
    c = &connections[connection_count++];
    memset(c, 0, sizeof(*c));
    c->id = id;
    c->active_since = GetTickCount64();
    return c;
}

static member* find_member(unsigned connection_id, unsigned id) {
    size_t i;
    for (i = 0; i < member_count; ++i)
        if (members[i].connection == connection_id && members[i].id == id) return &members[i];
    return NULL;
}

static member* add_member(unsigned connection_id, unsigned id) {
    member* m = find_member(connection_id, id);
    if (m) return m;
    if (member_count >= MAX_MEMBERS ||
            !grow((void**)&members, &member_capacity, member_count + 1, sizeof(*members))) return NULL;
    m = &members[member_count++];
    memset(m, 0, sizeof(*m));
    m->connection = connection_id;
    m->id = id;
    return m;
}

static void remove_member(member* m) {
    free(m->name);
    *m = members[--member_count];
}

static void set_channel_name(unsigned connection_id, const char* id, const char* name) {
    size_t i;
    for (i = 0; i < channel_count; ++i) {
        if (channels[i].connection == connection_id && strcmp(channels[i].id, id) == 0) {
            set_text(&channels[i].name, name);
            return;
        }
    }
    if (!grow((void**)&channels, &channel_capacity, channel_count + 1, sizeof(*channels))) return;
    memset(&channels[channel_count], 0, sizeof(*channels));
    channels[channel_count].connection = connection_id;
    strcpy_s(channels[channel_count].id, ID_CHARS, id);
    set_text(&channels[channel_count].name, name);
    if (channels[channel_count].name) ++channel_count;
}

static const char* channel_name(unsigned connection_id, const char* id) {
    size_t i;
    for (i = 0; i < channel_count; ++i)
        if (channels[i].connection == connection_id && strcmp(channels[i].id, id) == 0) return channels[i].name;
    return "";
}

static void remove_connection(unsigned id) {
    size_t i = 0;
    while (i < member_count) {
        if (members[i].connection == id) remove_member(&members[i]);
        else ++i;
    }
    i = 0;
    while (i < channel_count) {
        if (channels[i].connection == id) {
            free(channels[i].name);
            channels[i] = channels[--channel_count];
        } else {
            ++i;
        }
    }
    for (i = 0; i < connection_count; ++i) {
        if (connections[i].id == id) {
            connections[i] = connections[--connection_count];
            break;
        }
    }
}

static void clear_model(void) {
    while (connection_count) remove_connection(connections[0].id);
    while (member_count) remove_member(&members[0]);
}

/* The server tab GridTalk shows: the one whose microphone is open, else the most
   recently activated tab on which TeamSpeak has placed us in a channel. */
static connection* active_connection(member** self_out) {
    connection* best = NULL;
    member* best_self = NULL;
    size_t i;
    for (i = 0; i < connection_count; ++i) {
        connection* c = &connections[i];
        member* self = c->self ? find_member(c->id, c->self) : NULL;
        if (!self || !self->channel[0]) continue;
        if (!best || c->input > best->input || (c->input == best->input && c->active_since > best->active_since)) {
            best = c;
            best_self = self;
        }
    }
    *self_out = best_self;
    return best;
}

static int in_view(unsigned connection_id, const char* channel_id) {
    member* self;
    connection* active = active_connection(&self);
    return active && active->id == connection_id && strcmp(self->channel, channel_id) == 0;
}

/* ---- GridTalk packets ---------------------------------------------------- */

static void send_snapshot(void) {
    static char packet[32768];
    member* list[MAX_CHANNEL_MEMBERS];
    member* self;
    connection* active = active_connection(&self);
    unsigned long long id = ++snapshot_id;
    size_t count = 0, page, pages, i, used;
    roster_dirty = 0;
    if (!active) {
        used = 0;
        if (append(packet, sizeof(packet), &used, "{\"v\":2,\"source\":\"" SOURCE "\",\"event\":\"state\",\"snapshot\":%llu,"
                   "\"page\":0,\"pages\":1,\"connected\":false,\"clients\":[]}", id)) udp_send(packet);
        published = 1;
        set_status(STATUS_NO_SERVER);
        return;
    }
    for (i = 0; i < member_count; ++i) {
        if (members[i].connection != active->id || strcmp(members[i].channel, self->channel) != 0) continue;
        if (count == MAX_CHANNEL_MEMBERS) {
            send_bridge_status("Channel exceeds 256 users");
            return;
        }
        list[count++] = &members[i];
    }
    pages = count ? (count + 7) / 8 : 1;
    for (page = 0; page < pages; ++page) {
        used = 0;
        if (!append(packet, sizeof(packet), &used, "{\"v\":2,\"source\":\"" SOURCE "\",\"event\":\"state\",\"snapshot\":%llu,"
                    "\"page\":%u,\"pages\":%u,\"connected\":true,\"server\":\"%u\",\"channel\":\"%s\",\"channel_name\":\"",
                    id, (unsigned)page, (unsigned)pages, active->id, self->channel) ||
                !append_text(packet, sizeof(packet), &used, channel_name(active->id, self->channel), NAME_BYTES) ||
                !append(packet, sizeof(packet), &used, "\",\"clients\":[")) return;
        for (i = page * 8; i < count && i < (page + 1) * 8; ++i) {
            if (!append(packet, sizeof(packet), &used, "%s{\"client_id\":%u,\"name\":\"", i == page * 8 ? "" : ",", list[i]->id) ||
                    !append_text(packet, sizeof(packet), &used, list[i]->name, NAME_BYTES) ||
                    !append(packet, sizeof(packet), &used, "\",\"talking\":%s,\"self\":%s}",
                            list[i]->talking ? "true" : "false", list[i] == self ? "true" : "false")) return;
        }
        if (!append(packet, sizeof(packet), &used, "]}")) return;
        udp_send(packet);
    }
    published = 1;
    set_status(STATUS_LIVE);
}

static void send_talk(const member* m, int whisper) {
    char packet[4096];
    size_t used = 0;
    member* self;
    connection* active = active_connection(&self);
    if (!active || active->id != m->connection || strcmp(m->channel, self->channel) != 0) return;
    if (append(packet, sizeof(packet), &used, "{\"v\":2,\"source\":\"" SOURCE "\",\"event\":\"talk\",\"server\":\"%u\","
               "\"channel\":\"%s\",\"client_id\":%u,\"name\":\"", m->connection, m->channel, m->id) &&
            append_text(packet, sizeof(packet), &used, m->name, NAME_BYTES) &&
            append(packet, sizeof(packet), &used, "\",\"talking\":%s,\"whisper\":%s,\"self\":%s}",
                   m->talking ? "true" : "false", whisper ? "true" : "false", m == self ? "true" : "false"))
        udp_send(packet);
}

/* ---- TeamSpeak messages --------------------------------------------------- */

static int read_id(int node, unsigned* out) {
    long long value;
    if (!json_integer(&doc, node, &value) || value < 0 || value > 0xffffffffLL) return 0;
    *out = (unsigned)value;
    return 1;
}

/* Channel IDs are 64-bit; keep their decimal text rather than converting. */
static int read_channel_id(int node, char out[ID_CHARS]) {
    const char* text;
    size_t length, i;
    if (node < 0 || (doc.nodes[node].type != JSON_NUMBER && doc.nodes[node].type != JSON_STRING)) return 0;
    text = doc.nodes[node].text;
    length = doc.nodes[node].length;
    if (!length || length >= ID_CHARS) return 0;
    for (i = 0; i < length; ++i)
        if (text[i] < '0' || text[i] > '9') return 0;
    while (length > 1 && text[0] == '0') { ++text; --length; }
    memcpy(out, text, length);
    out[length] = 0;
    return 1;
}

static int is_root_channel(const char* id) {
    return strcmp(id, "0") == 0;
}

static const char* flag_value(int node) {
    static const char* const tokens[] = { "0", "1", "2", "3", "true", "false" };
    static const char* const quoted[] = { "\"0\"", "\"1\"", "\"2\"", "\"3\"", "\"true\"", "\"false\"" };
    size_t i;
    if (node < 0) return "-";
    if (doc.nodes[node].type == JSON_TRUE) return "true";
    if (doc.nodes[node].type == JSON_FALSE) return "false";
    if (doc.nodes[node].type == JSON_NULL) return "null";
    if (doc.nodes[node].type == JSON_NUMBER || doc.nodes[node].type == JSON_STRING)
        for (i = 0; i < sizeof(tokens) / sizeof(tokens[0]); ++i)
            if (doc.nodes[node].length == strlen(tokens[i]) && memcmp(doc.nodes[node].text, tokens[i], doc.nodes[node].length) == 0)
                return doc.nodes[node].type == JSON_STRING ? quoted[i] : tokens[i];
    return "<other>";
}

static void dump_flags(const char* what, unsigned connection_id, unsigned id, int properties) {
    char text[256];
    size_t used = 0;
    static const char* const flags[] = { "flagTalking", "inputDeactivated", "inputMuted", "inputHardware", "isMuted", "outputMuted" };
    size_t i;
    connection* c = find_connection(connection_id);
    for (i = 0; i < sizeof(flags) / sizeof(flags[0]); ++i) {
        int node = json_get(&doc, properties, flags[i]);
        int value;
        if (node >= 0 && json_boolean(&doc, node, &value))
            append(text, sizeof(text), &used, " %s=%d", flags[i], value);
    }
    log_line("  %s: connection %u client %u%s%s", what, connection_id, id, c && c->self == id ? " (self)" : "", used ? text : "");
}

static void apply_properties(connection* c, member* m, int properties) {
    const char* nickname = json_string(&doc, json_get(&doc, properties, "nickname"));
    int value;
    if (nickname) set_text(&m->name, nickname);
    if (json_boolean(&doc, json_get(&doc, properties, "flagTalking"), &value)) m->talking = value;
    if (c && c->self == m->id && json_boolean(&doc, json_get(&doc, properties, "inputHardware"), &value)) {
        if (value && !c->input) c->active_since = GetTickCount64();
        c->input = value;
    }
}

static void load_channel_list(unsigned connection_id, int list) {
    int item;
    if (list < 0 || doc.nodes[list].type != JSON_ARRAY) return;
    for (item = doc.nodes[list].child; item >= 0; item = doc.nodes[item].next) {
        char id[ID_CHARS];
        const char* name = json_string(&doc, json_get(&doc, json_get(&doc, item, "properties"), "name"));
        if (read_channel_id(json_get(&doc, item, "id"), id) && name) set_channel_name(connection_id, id, name);
    }
}

static void load_channels(unsigned connection_id, int infos) {
    int sub = json_get(&doc, infos, "subChannels"), list;
    load_channel_list(connection_id, json_get(&doc, infos, "rootChannels"));
    if (sub >= 0 && doc.nodes[sub].type == JSON_OBJECT)
        for (list = doc.nodes[sub].child; list >= 0; list = doc.nodes[list].next) load_channel_list(connection_id, list);
}

static void load_clients(connection* c, int list) {
    int item;
    if (list < 0 || doc.nodes[list].type != JSON_ARRAY) return;
    for (item = doc.nodes[list].child; item >= 0; item = doc.nodes[item].next) {
        unsigned id;
        char channel_id[ID_CHARS];
        member* m;
        if (!read_id(json_get(&doc, item, "id"), &id) || !id ||
                !read_channel_id(json_get(&doc, item, "channelId"), channel_id) || is_root_channel(channel_id)) continue;
        m = add_member(c->id, id);
        if (!m) continue;
        strcpy_s(m->channel, ID_CHARS, channel_id);
        apply_properties(c, m, json_get(&doc, item, "properties"));
    }
}

static void handle_auth(int payload) {
    const char* key = json_string(&doc, json_get(&doc, payload, "apiKey"));
    int list = json_get(&doc, payload, "connections"), item;
    if (key && *key && strcmp(key, api_key) != 0 && printable_key(key)) {
        strcpy_s(api_key, sizeof(api_key), key);
        save_config();
        log_line("TeamSpeak approved GridTalk; approval saved");
    }
    clear_model();
    if (list >= 0 && doc.nodes[list].type == JSON_ARRAY) {
        for (item = doc.nodes[list].child; item >= 0; item = doc.nodes[item].next) {
            unsigned id, self = 0;
            connection* c;
            if (!read_id(json_get(&doc, item, "id"), &id) || !(c = add_connection(id))) continue;
            if (read_id(json_get(&doc, item, "clientId"), &self)) c->self = self;
            load_channels(id, json_get(&doc, item, "channelInfos"));
            load_clients(c, json_get(&doc, item, "clientInfos"));
        }
    }
    if (dump_mode) {
        long long current = -1;
        json_integer(&doc, json_get(&doc, payload, "currentConnectionId"), &current);
        log_line("  auth: %u server tab(s), current connection %lld", (unsigned)connection_count, current);
    }
    link_state.state = LINK_READY;
    link_state.reported_failure = 0;
    roster_dirty = 1;
}

static void handle_talk(unsigned connection_id, int payload) {
    unsigned id;
    long long talk_status;
    int whisper = 0;
    member* m;
    if (!read_id(json_get(&doc, payload, "clientId"), &id) ||
            !json_integer(&doc, json_get(&doc, payload, "status"), &talk_status)) return;
    json_boolean(&doc, json_get(&doc, payload, "isWhisper"), &whisper);
    m = find_member(connection_id, id);
    if (dump_mode)
        log_line("  talk: connection %u client %u status %lld whisper %d%s", connection_id, id, talk_status, whisper,
                 m ? (in_view(connection_id, m->channel) ? " (in view)" : "") : " (unknown client)");
    /* 2 is "talking while disabled": nothing is transmitted. TeamSpeak 6 reports our
       own talking here and as a flagTalking property, so send only real changes. */
    if (!m || m->talking == (talk_status == 1)) return;
    m->talking = talk_status == 1;
    send_talk(m, whisper);
}

static void handle_moved(unsigned connection_id, int payload) {
    unsigned id;
    char channel_id[ID_CHARS];
    connection* c;
    member* m;
    int before, is_self;
    if (!read_id(json_get(&doc, payload, "clientId"), &id) || !id ||
            !read_channel_id(json_get(&doc, payload, "newChannelId"), channel_id)) return;
    c = add_connection(connection_id);
    is_self = c && c->self == id;
    m = find_member(connection_id, id);
    before = m && in_view(connection_id, m->channel);
    if (is_root_channel(channel_id)) {  /* left the server */
        if (m) remove_member(m);
        if (is_self) remove_connection(connection_id);  /* c is no longer valid */
        roster_dirty |= before || is_self;
        return;
    }
    if (!m && !(m = add_member(connection_id, id))) return;
    strcpy_s(m->channel, ID_CHARS, channel_id);
    apply_properties(c, m, json_get(&doc, payload, "properties"));
    roster_dirty |= before || in_view(connection_id, channel_id) || is_self;
}

static void handle_properties(unsigned connection_id, int payload) {
    unsigned id;
    member* m;
    if (!read_id(json_get(&doc, payload, "clientId"), &id)) return;
    if (dump_mode) dump_flags("properties", connection_id, id, json_get(&doc, payload, "properties"));
    if (!(m = find_member(connection_id, id))) return;
    apply_properties(find_connection(connection_id), m, json_get(&doc, payload, "properties"));
    roster_dirty |= in_view(connection_id, m->channel);
}

static void handle_connect_status(unsigned connection_id, int payload) {
    long long value;
    unsigned self;
    connection* c;
    if (!json_integer(&doc, json_get(&doc, payload, "status"), &value)) return;
    if (dump_mode) log_line("  connection %u status %lld", connection_id, value);
    if (value == 0) {
        remove_connection(connection_id);
        roster_dirty = 1;
    } else if (value == 4 && (c = add_connection(connection_id)) != NULL) {  /* connection established */
        if (read_id(json_get(&doc, json_get(&doc, payload, "info"), "clientId"), &self)) c->self = self;
        c->active_since = GetTickCount64();
        roster_dirty = 1;
    }
}

static void handle_self_property(unsigned connection_id, int payload) {
    const char* flag = json_string(&doc, json_get(&doc, payload, "flag"));
    connection* c = find_connection(connection_id);
    int value;
    member* m;
    if (dump_mode && flag)
        log_line("  self property: connection %u %.40s %s -> %s", connection_id, flag,
                 flag_value(json_get(&doc, payload, "oldValue")), flag_value(json_get(&doc, payload, "newValue")));
    if (!flag || !c || !json_boolean(&doc, json_get(&doc, payload, "newValue"), &value)) return;
    if (strcmp(flag, "inputHardware") == 0) {
        if (value && !c->input) c->active_since = GetTickCount64();
        c->input = value;
        roster_dirty = 1;
    } else if (strcmp(flag, "flagTalking") == 0 && (m = find_member(connection_id, c->self)) != NULL &&
               m->talking != value) {
        m->talking = value;
        send_talk(m, 0);
    }
}

/* Describes a message's structure (field names and value types, never values),
   so --dump logs can be shared without anyone's names or keys. */
static void describe(int node, char* out, size_t capacity, size_t* used, int depth) {
    int child, count = 0;
    if (*used + 64 >= capacity) return;
    switch (doc.nodes[node].type) {
    case JSON_NULL: append(out, capacity, used, "null"); return;
    case JSON_TRUE: case JSON_FALSE: append(out, capacity, used, "bool"); return;
    case JSON_NUMBER: append(out, capacity, used, "num"); return;
    case JSON_STRING: append(out, capacity, used, "str"); return;
    case JSON_ARRAY:
        for (child = doc.nodes[node].child; child >= 0; child = doc.nodes[child].next) ++count;
        append(out, capacity, used, "[%d x ", count);
        if (count && depth < 8) describe(doc.nodes[node].child, out, capacity, used, depth + 1);
        append(out, capacity, used, "]");
        return;
    default:
        append(out, capacity, used, "{");
        for (child = doc.nodes[node].child; child >= 0 && depth < 8; child = doc.nodes[child].next) {
            /* Numeric keys (channel IDs) are summarised; property names are not personal. */
            int numeric = doc.nodes[child].key[0] >= '0' && doc.nodes[child].key[0] <= '9';
            append(out, capacity, used, "%s%s:", child == doc.nodes[node].child ? "" : ",", numeric ? "<id>" : doc.nodes[child].key);
            describe(child, out, capacity, used, depth + 1);
            if (numeric) break;
        }
        append(out, capacity, used, "}");
    }
}

static void dump_shape(const char* type) {
    static char seen[64][48];
    static int seen_count;
    static char text[4000];
    size_t used = 0;
    int i;
    for (i = 0; i < seen_count; ++i)
        if (strcmp(seen[i], type) == 0) return;
    if (seen_count < 64) strncpy_s(seen[seen_count++], sizeof(seen[0]), type, _TRUNCATE);
    describe(0, text, sizeof(text), &used, 0);
    log_line("TeamSpeak %s message shape: %s", type, text);
}

static void handle_message(char* text, size_t length) {
    const char* type;
    int payload;
    unsigned connection_id = 0;
    if (json_parse(&doc, text, length) != 0) {
        log_line("Ignored an unreadable TeamSpeak message (%u bytes)", (unsigned)length);
        return;
    }
    type = json_string(&doc, json_get(&doc, 0, "type"));
    payload = json_get(&doc, 0, "payload");
    if (!type) return;
    if (dump_mode) {
        if (strcmp(type, "log") != 0) log_line("TeamSpeak event: %s", type);  /* TeamSpeak's own log is chatty */
        dump_shape(type);
    }
    read_id(json_get(&doc, payload, "connectionId"), &connection_id);
    if (strcmp(type, "auth") == 0) handle_auth(payload);
    else if (link_state.state != LINK_READY) return;
    else if (strcmp(type, "talkStatusChanged") == 0) handle_talk(connection_id, payload);
    else if (strcmp(type, "clientMoved") == 0) handle_moved(connection_id, payload);
    else if (strcmp(type, "clientPropertiesUpdated") == 0) handle_properties(connection_id, payload);
    else if (strcmp(type, "connectStatusChanged") == 0) handle_connect_status(connection_id, payload);
    else if (strcmp(type, "clientSelfPropertyUpdated") == 0) handle_self_property(connection_id, payload);
    else if (strcmp(type, "channels") == 0) {
        if (add_connection(connection_id)) load_channels(connection_id, json_get(&doc, payload, "info"));
        roster_dirty = 1;
    } else if (strcmp(type, "channelCreated") == 0) {
        char id[ID_CHARS];
        const char* name = json_string(&doc, json_get(&doc, json_get(&doc, payload, "properties"), "name"));
        if (read_channel_id(json_get(&doc, payload, "channelId"), id) && name) set_channel_name(connection_id, id, name);
    }
}

/* ---- WebSocket client ------------------------------------------------------ */

static int random_bytes(void* buffer, ULONG length) {
    return BCRYPT_SUCCESS(BCryptGenRandom(NULL, (PUCHAR)buffer, length, BCRYPT_USE_SYSTEM_PREFERRED_RNG));
}

static void base64(const unsigned char* data, size_t length, char* out) {
    static const char table[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    size_t i;
    for (i = 0; i + 2 < length; i += 3) {
        unsigned v = (unsigned)data[i] << 16 | (unsigned)data[i + 1] << 8 | data[i + 2];
        *out++ = table[v >> 18]; *out++ = table[(v >> 12) & 63]; *out++ = table[(v >> 6) & 63]; *out++ = table[v & 63];
    }
    if (length - i == 1) {
        unsigned v = (unsigned)data[i] << 16;
        *out++ = table[v >> 18]; *out++ = table[(v >> 12) & 63]; *out++ = '='; *out++ = '=';
    } else if (length - i == 2) {
        unsigned v = (unsigned)data[i] << 16 | (unsigned)data[i + 1] << 8;
        *out++ = table[v >> 18]; *out++ = table[(v >> 12) & 63]; *out++ = table[(v >> 6) & 63]; *out++ = '=';
    }
    *out = 0;
}

static int sha1(const char* text, unsigned char digest[20]) {
    BCRYPT_ALG_HANDLE algorithm;
    BCRYPT_HASH_HANDLE hash;
    int ok;
    if (!BCRYPT_SUCCESS(BCryptOpenAlgorithmProvider(&algorithm, BCRYPT_SHA1_ALGORITHM, NULL, 0))) return 0;
    ok = BCRYPT_SUCCESS(BCryptCreateHash(algorithm, &hash, NULL, 0, NULL, 0, 0));
    if (ok) {
        ok = BCRYPT_SUCCESS(BCryptHashData(hash, (PUCHAR)text, (ULONG)strlen(text), 0)) &&
             BCRYPT_SUCCESS(BCryptFinishHash(hash, digest, 20, 0));
        BCryptDestroyHash(hash);
    }
    BCryptCloseAlgorithmProvider(algorithm, 0);
    return ok;
}

static int send_all(const char* data, size_t length) {
    while (length) {
        int sent = send(link_state.socket, data, length > 65536 ? 65536 : (int)length, 0);
        if (sent == SOCKET_ERROR) {
            fd_set writable;
            struct timeval wait = { 2, 0 };
            if (WSAGetLastError() != WSAEWOULDBLOCK) return 0;
            FD_ZERO(&writable);
            FD_SET(link_state.socket, &writable);
            if (select(0, NULL, &writable, NULL, &wait) != 1) return 0;
            continue;
        }
        data += sent;
        length -= (size_t)sent;
    }
    return 1;
}

/* Client frames are always masked (RFC 6455 5.3). */
static int ws_send(int opcode, const char* data, size_t length) {
    unsigned char mask[4];
    unsigned char* frame;
    size_t header = 0, i;
    int ok;
    frame = (unsigned char*)malloc(length + 14);
    if (!frame || !random_bytes(mask, sizeof(mask))) {
        free(frame);
        return 0;
    }
    frame[header++] = (unsigned char)(0x80 | opcode);
    if (length < 126) {
        frame[header++] = (unsigned char)(0x80 | length);
    } else if (length < 65536) {
        frame[header++] = (unsigned char)(0x80 | 126);
        frame[header++] = (unsigned char)(length >> 8);
        frame[header++] = (unsigned char)(length & 0xff);
    } else {
        frame[header++] = (unsigned char)(0x80 | 127);
        for (i = 0; i < 8; ++i) frame[header++] = (unsigned char)((unsigned long long)length >> (56 - 8 * i));
    }
    memcpy(frame + header, mask, 4);
    header += 4;
    for (i = 0; i < length; ++i) frame[header + i] = (unsigned char)(data[i] ^ mask[i & 3]);
    ok = send_all((const char*)frame, header + length);
    free(frame);
    return ok;
}

static void shrink_buffers(void) {
    if (link_state.rx_capacity > KEEP_BUFFER && link_state.rx_length == 0) {
        free(link_state.rx);
        link_state.rx = NULL;
        link_state.rx_capacity = 0;
    }
    if (link_state.message_capacity > KEEP_BUFFER && !link_state.message_open) {
        free(link_state.message);
        link_state.message = NULL;
        link_state.message_capacity = link_state.message_length = 0;
    }
    if (doc.capacity > 65536) json_release(&doc);
}

static void link_close(const char* reason) {
    ULONGLONG now = GetTickCount64();
    unsigned delay = retry_ms;
    int was = link_state.state;
    if (link_state.socket != INVALID_SOCKET) {
        closesocket(link_state.socket);
        link_state.socket = INVALID_SOCKET;
    }
    if (was == LINK_AUTH) {
        /* TeamSpeak closes the socket when the user declines, or when a stored
           key was revoked. Forget the key so the next attempt asks again. */
        if (link_state.sent_stored_key) {
            api_key[0] = 0;
            save_config();
        }
        delay *= DENIED_RETRY_FACTOR;
    }
    if (was >= LINK_HANDSHAKE || !link_state.reported_failure) log_line("%s", reason);
    link_state.reported_failure = 1;
    if (published) {
        send_reset();
        published = 0;
    }
    clear_model();
    link_state.state = LINK_IDLE;
    link_state.retry_at = now + delay;
    link_state.rx_length = link_state.message_length = 0;
    link_state.message_open = 0;
    shrink_buffers();
    set_status(STATUS_WAITING);
}

static void link_start(void) {
    struct sockaddr_in address;
    BOOL no_delay = TRUE;
    SOCKET s = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (s == INVALID_SOCKET) {
        link_state.retry_at = GetTickCount64() + retry_ms;
        return;
    }
    setsockopt(s, IPPROTO_TCP, TCP_NODELAY, (const char*)&no_delay, sizeof(no_delay));
    WSAEventSelect(s, link_state.event, FD_CONNECT | FD_READ | FD_CLOSE);  /* also makes s non-blocking */
    memset(&address, 0, sizeof(address));
    address.sin_family = AF_INET;
    address.sin_port = htons(ts_port);
    address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    link_state.socket = s;
    link_state.state = LINK_CONNECTING;
    link_state.since = GetTickCount64();
    if (connect(s, (const struct sockaddr*)&address, sizeof(address)) == SOCKET_ERROR && WSAGetLastError() != WSAEWOULDBLOCK)
        link_close("Could not open a connection to TeamSpeak");
}

static void link_connected(void) {
    unsigned char nonce[16];
    char request[512];
    if (!random_bytes(nonce, sizeof(nonce))) {
        link_close("Could not create a WebSocket key");
        return;
    }
    base64(nonce, sizeof(nonce), link_state.key);
    snprintf(request, sizeof(request),
             "GET / HTTP/1.1\r\nHost: localhost:%u\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
             "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n", (unsigned)ts_port, link_state.key);
    if (!send_all(request, strlen(request))) {
        link_close("Could not send the WebSocket handshake");
        return;
    }
    link_state.state = LINK_HANDSHAKE;
    link_state.since = GetTickCount64();
}

static void send_auth(void) {
    char message[1024];
    size_t used = 0;
    link_state.sent_stored_key = api_key[0] != 0;
    if (!append(message, sizeof(message), &used,
                "{\"type\":\"auth\",\"payload\":{\"identifier\":\"" APP_IDENTIFIER "\",\"version\":\"" GRIDTALK_VERSION "\","
                "\"name\":\"GridTalk\",\"description\":\"Shows who is talking in your channel in the GridTalk overlay "
                "for Assetto Corsa.\",\"content\":{\"apiKey\":\"%s\"}}}", api_key) ||
            !ws_send(1, message, used)) {
        link_close("Could not send the TeamSpeak sign-in request");
        return;
    }
    link_state.state = LINK_AUTH;
    link_state.since = GetTickCount64();
    if (link_state.sent_stored_key) log_line("Signing in to TeamSpeak with the saved approval");
    else log_line("Asking TeamSpeak for approval; allow GridTalk in TeamSpeak's Remote Apps prompt");
}

static const char* find_header(const char* headers, const char* name) {
    size_t length = strlen(name);
    const char* line = strstr(headers, "\r\n");
    while (line) {
        line += 2;
        if (_strnicmp(line, name, length) == 0 && line[length] == ':') {
            line += length + 1;
            while (*line == ' ' || *line == '\t') ++line;
            return line;
        }
        line = strstr(line, "\r\n");
    }
    return NULL;
}

/* Returns the bytes consumed by a complete handshake response, 0 if more data
   is needed, or -1 if TeamSpeak refused the upgrade. */
static int read_handshake(void) {
    char* end;
    char expected[64], text[128];
    unsigned char digest[20];
    const char* accept;
    size_t consumed;
    if (link_state.rx_capacity <= link_state.rx_length) return link_state.rx_length > 8192 ? -1 : 0;
    link_state.rx[link_state.rx_length] = 0;
    end = strstr((char*)link_state.rx, "\r\n\r\n");
    if (!end) return link_state.rx_length > 8192 ? -1 : 0;
    end[2] = 0;
    consumed = (size_t)(end - (char*)link_state.rx) + 4;
    snprintf(text, sizeof(text), "%s258EAFA5-E914-47DA-95CA-C5AB0DC85B11", link_state.key);
    if (strncmp((char*)link_state.rx, "HTTP/1.1 101", 12) != 0 || !sha1(text, digest)) return -1;
    base64(digest, sizeof(digest), expected);
    accept = find_header((char*)link_state.rx, "Sec-WebSocket-Accept");
    if (!accept || strncmp(accept, expected, strlen(expected)) != 0 ||
            (accept[strlen(expected)] != '\r' && accept[strlen(expected)] != ' ')) return -1;
    return (int)consumed;
}

static int append_message(const unsigned char* data, size_t length) {
    if (link_state.message_length + length > MAX_MESSAGE) return 0;
    if (!grow((void**)&link_state.message, &link_state.message_capacity, link_state.message_length + length + 1, 1)) return 0;
    memcpy(link_state.message + link_state.message_length, data, length);
    link_state.message_length += length;
    return 1;
}

/* Handles every complete frame in the receive buffer. Returns 0 to drop the link. */
static int read_frames(void) {
    size_t offset = 0;
    while (link_state.rx_length - offset >= 2) {
        unsigned char* frame = link_state.rx + offset;
        size_t available = link_state.rx_length - offset, header = 2, i;
        unsigned long long length = frame[1] & 0x7f;
        int fin = frame[0] & 0x80, opcode = frame[0] & 0x0f, masked = frame[1] & 0x80;
        unsigned char* payload;
        if (length == 126) {
            if (available < 4) break;
            length = (unsigned long long)frame[2] << 8 | frame[3];
            header = 4;
        } else if (length == 127) {
            if (available < 10) break;
            for (length = 0, i = 0; i < 8; ++i) length = length << 8 | frame[2 + i];
            header = 10;
        }
        if (length > MAX_MESSAGE) {
            log_line("TeamSpeak sent a message larger than %u bytes", MAX_MESSAGE);
            return 0;
        }
        if (masked) header += 4;
        if (available < header + length) break;
        payload = frame + header;
        if (masked)
            for (i = 0; i < length; ++i) payload[i] ^= frame[header - 4 + (i & 3)];
        offset += header + (size_t)length;
        switch (opcode) {
        case 0x8:  /* close: echo the status code, then drop */
            ws_send(0x8, (const char*)payload, length >= 2 ? 2 : 0);
            return 0;
        case 0x9:  /* ping */
            if (length <= 125 && !ws_send(0xA, (const char*)payload, (size_t)length)) return 0;
            break;
        case 0xA:
            break;
        case 0x1:
        case 0x2:
        case 0x0:
            if ((opcode == 0x0) != (link_state.message_open != 0)) return 0;  /* continuation mismatch */
            if (opcode != 0x0) link_state.message_length = 0;
            if (!append_message(payload, (size_t)length)) {
                log_line("TeamSpeak sent a message larger than %u bytes", MAX_MESSAGE);
                return 0;
            }
            link_state.message_open = !fin;
            if (fin) {
                link_state.message[link_state.message_length] = 0;
                handle_message(link_state.message, link_state.message_length);
                link_state.message_length = 0;
            }
            break;
        default:
            return 0;
        }
    }
    if (offset) {
        memmove(link_state.rx, link_state.rx + offset, link_state.rx_length - offset);
        link_state.rx_length -= offset;
    }
    return 1;
}

static void link_receive(int closing) {
    for (;;) {
        int received;
        if (!grow((void**)&link_state.rx, &link_state.rx_capacity, link_state.rx_length + 65537, 1) ||
                link_state.rx_length > MAX_MESSAGE + 65536) {
            link_close("TeamSpeak sent more data than the relay accepts");
            return;
        }
        received = recv(link_state.socket, (char*)link_state.rx + link_state.rx_length,
                        (int)(link_state.rx_capacity - link_state.rx_length - 1), 0);
        if (received > 0) {
            link_state.rx_length += (size_t)received;
            continue;
        }
        if (received == 0) closing = 1;
        else if (WSAGetLastError() != WSAEWOULDBLOCK) closing = 1;
        break;
    }
    if (link_state.state == LINK_HANDSHAKE) {
        int consumed = read_handshake();
        if (consumed < 0) {
            link_close("TeamSpeak refused the WebSocket connection");
            return;
        }
        if (consumed > 0) {
            memmove(link_state.rx, link_state.rx + consumed, link_state.rx_length - (size_t)consumed);
            link_state.rx_length -= (size_t)consumed;
            send_auth();
            if (link_state.state != LINK_AUTH) return;
        }
    }
    if (link_state.state >= LINK_AUTH && !read_frames()) {
        link_close(link_state.state == LINK_AUTH ? "TeamSpeak did not approve GridTalk" : "TeamSpeak closed the connection");
        return;
    }
    if (roster_dirty && link_state.state == LINK_READY) send_snapshot();
    shrink_buffers();
    if (closing) link_close(link_state.state == LINK_AUTH ? "TeamSpeak did not approve GridTalk" : "TeamSpeak closed the connection");
}

/* ---- main loop -------------------------------------------------------------- */

static void tick(ULONGLONG now) {
    if (link_state.state == LINK_READY) {
        send_snapshot();
    } else if (link_state.state == LINK_AUTH && now - link_state.since >= APPROVAL_HINT_MS) {
        set_status(STATUS_APPROVAL);
        send_bridge_status("Allow GridTalk in TeamSpeak (Remote Apps)");
    } else if ((link_state.state == LINK_CONNECTING || link_state.state == LINK_HANDSHAKE) &&
               now - link_state.since >= HANDSHAKE_TIMEOUT_MS) {
        link_close("TeamSpeak did not answer the WebSocket handshake");
    }
}

static void run(void) {
    ULONGLONG next_tick = GetTickCount64() + 1000;
    link_state.event = WSACreateEvent();
    link_state.retry_at = 0;
    snapshot_id = GetTickCount64();  /* increases across relay restarts, like the TS3 sender */
    send_reset();
    set_status(STATUS_WAITING);
    for (;;) {
        HANDLE handles[2];
        ULONGLONG now = GetTickCount64(), wake;
        DWORD result, count = 1;
        if (link_state.state == LINK_IDLE && now >= link_state.retry_at) link_start();
        if (now >= next_tick) {
            tick(now);
            next_tick = now + 1000;
        }
        wake = next_tick;
        if (link_state.state == LINK_IDLE && link_state.retry_at < wake) wake = link_state.retry_at;
        handles[0] = stop_event;
        if (link_state.socket != INVALID_SOCKET) handles[count++] = link_state.event;
        result = WaitForMultipleObjects(count, handles, FALSE, wake > now ? (DWORD)(wake - now) : 0);
        if (result == WAIT_OBJECT_0) break;
        if (result == WAIT_OBJECT_0 + 1 && link_state.socket != INVALID_SOCKET) {
            WSANETWORKEVENTS events;
            if (WSAEnumNetworkEvents(link_state.socket, link_state.event, &events) == SOCKET_ERROR) {
                link_close("Lost the connection to TeamSpeak");
                continue;
            }
            if (events.lNetworkEvents & FD_CONNECT) {
                if (events.iErrorCode[FD_CONNECT_BIT]) {
                    link_close("TeamSpeak is not reachable; is it running with Remote Apps enabled?");
                    continue;
                }
                link_connected();
            }
            if (link_state.socket != INVALID_SOCKET && (events.lNetworkEvents & (FD_READ | FD_CLOSE)))
                link_receive((events.lNetworkEvents & FD_CLOSE) != 0);
        }
    }
    if (link_state.socket != INVALID_SOCKET) {
        if (link_state.state >= LINK_AUTH) ws_send(0x8, "\x03\xe8", 2);  /* 1000: normal closure */
        closesocket(link_state.socket);
        link_state.socket = INVALID_SOCKET;
    }
    send_reset();
    clear_model();
    WSACloseEvent(link_state.event);
    log_line("Stopped");
}

/* ---- start with Windows --------------------------------------------------------- */

/* The per-user Run key starts the relay at sign-in; no administrator rights needed. */
static int autostart_command(wchar_t* command, size_t capacity) {
    wchar_t path[MAX_PATH];
    DWORD length = GetModuleFileNameW(NULL, path, MAX_PATH);
    if (!length || length >= MAX_PATH) return 0;
    return swprintf_s(command, capacity, L"\"%s\"", path) > 0;
}

static int autostart_enabled(void) {
    wchar_t expected[MAX_PATH + 3], value[MAX_PATH + 64];
    DWORD size = sizeof(value);
    if (!autostart_command(expected, MAX_PATH + 3) ||
            RegGetValueW(HKEY_CURRENT_USER, run_key, RUN_VALUE, RRF_RT_REG_SZ, NULL, value, &size) != ERROR_SUCCESS)
        return 0;
    return _wcsicmp(value, expected) == 0;  /* an entry for a moved copy counts as off */
}

static int set_autostart(int enable) {
    wchar_t command[MAX_PATH + 3];
    HKEY key;
    LSTATUS result;
    if (!enable) {
        result = RegDeleteKeyValueW(HKEY_CURRENT_USER, run_key, RUN_VALUE);
        return result == ERROR_SUCCESS || result == ERROR_FILE_NOT_FOUND;
    }
    if (!autostart_command(command, MAX_PATH + 3) ||
            RegCreateKeyExW(HKEY_CURRENT_USER, run_key, 0, NULL, 0, KEY_SET_VALUE, NULL, &key, NULL) != ERROR_SUCCESS)
        return 0;
    result = RegSetValueExW(key, RUN_VALUE, 0, REG_SZ, (const BYTE*)command, (DWORD)((wcslen(command) + 1) * sizeof(wchar_t)));
    RegCloseKey(key);
    return result == ERROR_SUCCESS;
}

/* ---- tray icon -------------------------------------------------------------- */

static UINT taskbar_created;
static HICON icons[2];  /* sending, idle */

static void load_icons(void) {
    /* The relay is DPI aware, so this is the size the tray really draws. */
    int width = GetSystemMetrics(SM_CXSMICON), height = GetSystemMetrics(SM_CYSMICON), i;
    for (i = 0; i < 2; ++i) {
        if (icons[i]) DestroyIcon(icons[i]);
        icons[i] = (HICON)LoadImageW(GetModuleHandleW(NULL), MAKEINTRESOURCEW(i + 1), IMAGE_ICON, width, height, 0);
    }
}

static void tray_icon(DWORD action) {
    NOTIFYICONDATAW icon;
    memset(&icon, 0, sizeof(icon));
    icon.cbSize = sizeof(icon);
    icon.hWnd = tray_window;
    icon.uID = 1;
    icon.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP;
    icon.uCallbackMessage = TRAY_MESSAGE;
    icon.hIcon = icons[status == STATUS_LIVE ? 0 : 1];
    if (!icon.hIcon) icon.hIcon = LoadIconW(NULL, (LPCWSTR)IDI_APPLICATION);
    wcscpy_s(icon.szTip, sizeof(icon.szTip) / sizeof(icon.szTip[0]), status_text[status < 0 ? STATUS_WAITING : status]);
    Shell_NotifyIconW(action, &icon);
}

static LRESULT CALLBACK tray_proc(HWND window, UINT message, WPARAM wparam, LPARAM lparam) {
    if (message == STATUS_MESSAGE) {
        tray_icon(NIM_MODIFY);
        return 0;
    }
    if (message == TRAY_MESSAGE && (lparam == WM_RBUTTONUP || lparam == WM_LBUTTONUP)) {
        POINT point;
        HMENU menu = CreatePopupMenu();
        AppendMenuW(menu, MF_STRING | MF_GRAYED, 0, status_text[status < 0 ? STATUS_WAITING : status]);
        AppendMenuW(menu, MF_SEPARATOR, 0, NULL);
        AppendMenuW(menu, MF_STRING | (autostart_enabled() ? MF_CHECKED : MF_UNCHECKED), MENU_AUTOSTART, L"Start with Windows");
        AppendMenuW(menu, MF_STRING, MENU_EXIT, L"Exit");
        GetCursorPos(&point);
        SetForegroundWindow(window);
        TrackPopupMenu(menu, TPM_RIGHTBUTTON, point.x, point.y, 0, window, NULL);
        DestroyMenu(menu);
        return 0;
    }
    if (message == WM_COMMAND && LOWORD(wparam) == MENU_AUTOSTART) {
        int enable = !autostart_enabled();
        if (set_autostart(enable)) log_line(enable ? "Will start with Windows" : "Will no longer start with Windows");
        else MessageBoxW(window, L"Could not change the Windows startup setting.", L"GridTalk TS6 Relay", MB_OK | MB_ICONWARNING);
        return 0;
    }
    if (message == WM_COMMAND && LOWORD(wparam) == MENU_EXIT) {
        DestroyWindow(window);
        return 0;
    }
    if (message == taskbar_created && taskbar_created) {  /* Explorer restarted, or the display scale changed */
        load_icons();
        tray_icon(NIM_ADD);
        return 0;
    }
    if (message == WM_DESTROY) {
        tray_icon(NIM_DELETE);
        PostQuitMessage(0);
        return 0;
    }
    return DefWindowProcW(window, message, wparam, lparam);
}

static DWORD WINAPI network_thread(LPVOID parameter) {
    (void)parameter;
    run();
    if (tray_window) PostMessageW(tray_window, WM_CLOSE, 0, 0);  /* stopped by --stop */
    return 0;
}

static BOOL WINAPI console_handler(DWORD signal) {
    (void)signal;
    SetEvent(stop_event);
    return TRUE;
}

/* ---- startup ---------------------------------------------------------------- */

static int parse_port(const wchar_t* text, unsigned short* port) {
    wchar_t* end;
    unsigned long value = wcstoul(text, &end, 10);
    if (*end || value < 1 || value > 65535) return 0;
    *port = (unsigned short)value;
    return 1;
}

static void open_log(int console) {
    wchar_t path[MAX_PATH];
    wchar_t* slash;
    if (console) {
        log_handle = GetStdHandle(STD_ERROR_HANDLE);
        if ((log_handle == NULL || log_handle == INVALID_HANDLE_VALUE) && AttachConsole(ATTACH_PARENT_PROCESS))
            log_handle = CreateFileW(L"CONOUT$", GENERIC_WRITE, FILE_SHARE_WRITE, NULL, OPEN_EXISTING, 0, NULL);
        if (log_handle == NULL) log_handle = INVALID_HANDLE_VALUE;
        return;
    }
    wcscpy_s(path, MAX_PATH, config_path);
    slash = wcsrchr(path, L'\\');
    if (!slash) return;
    wcscpy_s(slash + 1, MAX_PATH - (size_t)(slash + 1 - path), L"relay.log");
    log_handle = CreateFileW(path, GENERIC_WRITE, FILE_SHARE_READ, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
}

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE previous, PWSTR command_line, int show) {
    int argc, i, console = 0, stop = 0, autostart = -1;
    wchar_t** argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    wchar_t name[64], folder[MAX_PATH];
    HANDLE single, thread;
    WSADATA wsa;
    DWORD length;
    (void)previous; (void)command_line; (void)show;
    config_path[0] = 0;
    for (i = 1; argv && i < argc; ++i) {
        if (wcscmp(argv[i], L"--console") == 0) console = 1;
        else if (wcscmp(argv[i], L"--dump") == 0) dump_mode = 1;
        else if (wcscmp(argv[i], L"--stop") == 0) stop = 1;
        else if (wcscmp(argv[i], L"--ts-port") == 0 && i + 1 < argc && parse_port(argv[i + 1], &ts_port)) ++i;
        else if (wcscmp(argv[i], L"--udp-port") == 0 && i + 1 < argc && parse_port(argv[i + 1], &udp_port)) ++i;
        else if (wcscmp(argv[i], L"--retry-ms") == 0 && i + 1 < argc) retry_ms = (unsigned)wcstoul(argv[++i], NULL, 10);
        else if (wcscmp(argv[i], L"--config") == 0 && i + 1 < argc) wcscpy_s(config_path, MAX_PATH, argv[++i]);
        else if (wcscmp(argv[i], L"--start-with-windows") == 0 && i + 1 < argc &&
                 (wcscmp(argv[i + 1], L"on") == 0 || wcscmp(argv[i + 1], L"off") == 0))
            autostart = wcscmp(argv[++i], L"on") == 0;
        else if (wcscmp(argv[i], L"--run-key") == 0 && i + 1 < argc) wcscpy_s(run_key, 200, argv[++i]);
        else {
            MessageBoxW(NULL, L"Usage: GridTalk-TS6-Relay [--console] [--dump] [--stop] [--start-with-windows on|off]\n"
                        L"                           [--ts-port N] [--udp-port N] [--config FILE]",
                        L"GridTalk TS6 Relay", MB_OK | MB_ICONINFORMATION);
            return 2;
        }
    }
    if (argv) LocalFree(argv);
    if (retry_ms < 100) retry_ms = 100;
    if (autostart >= 0) return set_autostart(autostart) ? 0 : 1;
    swprintf_s(name, 64, L"Local\\GridTalkRelayStop-%u", (unsigned)udp_port);
    if (stop) {
        HANDLE running = OpenEventW(EVENT_MODIFY_STATE, FALSE, name);
        if (!running) return 1;
        SetEvent(running);
        CloseHandle(running);
        return 0;
    }
    if (!config_path[0]) {
        length = GetEnvironmentVariableW(L"APPDATA", folder, MAX_PATH);
        if (!length || length >= MAX_PATH - 32) return 1;
        swprintf_s(config_path, MAX_PATH, L"%s\\GridTalk\\relay.ini", folder);
        swprintf_s(folder + length, MAX_PATH - length, L"\\GridTalk");
        CreateDirectoryW(folder, NULL);
    }
    open_log(console);
    stop_event = CreateEventW(NULL, TRUE, FALSE, name);
    swprintf_s(name, 64, L"Local\\GridTalkRelay-%u", (unsigned)udp_port);
    single = CreateMutexW(NULL, FALSE, name);
    if (!stop_event || !single || GetLastError() == ERROR_ALREADY_EXISTS) {
        log_line("Another GridTalk TS6 Relay is already running");
        return 0;
    }
    if (WSAStartup(MAKEWORD(2, 2), &wsa)) return 1;
    udp = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    memset(&udp_target, 0, sizeof(udp_target));
    udp_target.sin_family = AF_INET;
    udp_target.sin_port = htons(udp_port);
    udp_target.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    load_config();
    log_line("%s: TeamSpeak port %u -> 127.0.0.1:%u", build_info, (unsigned)ts_port, (unsigned)udp_port);
    if (console) {
        SetConsoleCtrlHandler(console_handler, TRUE);
        run();
    } else {
        WNDCLASSW window_class;
        MSG message;
        SetProcessDPIAware();  /* a sharp tray icon at display scaling above 100% */
        memset(&window_class, 0, sizeof(window_class));
        window_class.lpfnWndProc = tray_proc;
        window_class.hInstance = instance;
        window_class.lpszClassName = L"GridTalkRelayTray";
        RegisterClassW(&window_class);
        taskbar_created = RegisterWindowMessageW(L"TaskbarCreated");
        tray_window = CreateWindowExW(0, window_class.lpszClassName, L"GridTalk TS6 Relay", 0, 0, 0, 0, 0, NULL, NULL, instance, NULL);
        if (!tray_window) return 1;
        load_icons();
        tray_icon(NIM_ADD);
        thread = CreateThread(NULL, 0, network_thread, NULL, 0, NULL);
        if (!thread) return 1;
        while (GetMessageW(&message, NULL, 0, 0) > 0) {
            TranslateMessage(&message);
            DispatchMessageW(&message);
        }
        tray_window = NULL;
        SetEvent(stop_event);
        WaitForSingleObject(thread, 5000);
        CloseHandle(thread);
    }
    closesocket(udp);
    WSACleanup();
    CloseHandle(single);
    return 0;
}
