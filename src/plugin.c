#define WIN32_LEAN_AND_MEAN
#include <winsock2.h>
#include <ws2tcpip.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "teamspeak/public_definitions.h"
#include "teamspeak/public_errors.h"
#include "ts3_functions.h"

#define EXPORT __declspec(dllexport)
#define STRINGIFY_INNER(value) #value
#define STRINGIFY(value) STRINGIFY_INNER(value)
#define STRINGIFY_PORT STRINGIFY(TS_AC_UDP_PORT)
static struct TS3Functions api;
static SOCKET udp = INVALID_SOCKET;
static struct sockaddr_in destination;
static UINT_PTR timer_id;
static unsigned long long snapshot_id;
static int reported_send_error;
static int reported_snapshot;
static void send_snapshot(void);
static void escape_name(const char* name, char* output);
static VOID CALLBACK snapshot_timer(HWND window, UINT message, UINT_PTR timer, DWORD time) {
    (void)window; (void)message; (void)timer; (void)time;
    send_snapshot();
}
static void log_message(const char* text, enum LogLevel level) {
    if (api.logMessage) api.logMessage(text, level, "AC Speaking UDP", 0);
}

EXPORT const char* ts3plugin_name(void) { return "AC Speaking UDP"; }
EXPORT const char* ts3plugin_version(void) { return "0.3.0"; }
EXPORT int ts3plugin_apiVersion(void) { return 26; }
EXPORT const char* ts3plugin_author(void) { return "TS-AC-plugin"; }
EXPORT const char* ts3plugin_description(void) { return "Sends speaking events to localhost over UDP."; }
EXPORT void ts3plugin_setFunctionPointers(const struct TS3Functions funcs) { api = funcs; }

static void send_packet(const char* packet) {
    if (udp != INVALID_SOCKET && sendto(udp, packet, (int)strlen(packet), 0,
               (const struct sockaddr*)&destination, sizeof(destination)) == SOCKET_ERROR && !reported_send_error) {
        char error[128];
        snprintf(error, sizeof(error), "UDP send failed: Winsock error %d", WSAGetLastError());
        log_message(error, LogLevel_ERROR);
        reported_send_error = 1;
    }
}

EXPORT int ts3plugin_init(void) {
    WSADATA data;
    u_long nonblocking = 1;
    if (WSAStartup(MAKEWORD(2, 2), &data)) return 1;
    udp = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (udp == INVALID_SOCKET || ioctlsocket(udp, FIONBIO, &nonblocking)) {
        if (udp != INVALID_SOCKET) closesocket(udp);
        udp = INVALID_SOCKET;
        WSACleanup();
        return 1;
    }
    destination.sin_family = AF_INET;
    destination.sin_port = htons(TS_AC_UDP_PORT);
    destination.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    reported_send_error = 0;
    reported_snapshot = 0;
    snapshot_id = GetTickCount64();
    /* Runs on the TeamSpeak init thread's message loop, never a worker thread. */
    timer_id = SetTimer(NULL, 0, 1000, snapshot_timer);
    if (!timer_id) {
        log_message("Could not start channel snapshot timer", LogLevel_ERROR);
        closesocket(udp); udp = INVALID_SOCKET; WSACleanup(); return 1;
    }
    log_message("v0.3.0 started: speaking events + 1-second channel snapshots to 127.0.0.1:" STRINGIFY_PORT, LogLevel_INFO);
    send_packet("{\"v\":1,\"event\":\"reset\"}");
    send_snapshot();
    return 0;
}

EXPORT void ts3plugin_shutdown(void) {
    if (timer_id) { KillTimer(NULL, timer_id); timer_id = 0; }
    send_packet("{\"v\":1,\"event\":\"reset\"}");
    if (udp != INVALID_SOCKET) {
        closesocket(udp);
        udp = INVALID_SOCKET;
        WSACleanup();
    }
}

static void get_name(uint64 server, anyID client, char* escaped) {
    char name[512] = {0};
    if (api.getClientDisplayName(server, client, name, sizeof(name)) != ERROR_ok) name[0] = 0;
    name[sizeof(name) - 1] = 0;
    escape_name(name, escaped);
}

static void send_snapshot(void) {
    uint64 server, channel = 0;
    anyID self = 0, *clients = NULL;
    char channel_name[512] = {0}, escaped_channel[3073], *sdk_name = NULL;
    char packet[32768];
    size_t count = 0, pages, page;
    unsigned long long id = ++snapshot_id;
    if (!api.getCurrentServerConnectionHandlerID) return;
    server = api.getCurrentServerConnectionHandlerID();
    if (!server || api.getClientID(server, &self) != ERROR_ok ||
        api.getChannelOfClient(server, self, &channel) != ERROR_ok) {
        snprintf(packet, sizeof(packet), "{\"v\":2,\"event\":\"state\",\"snapshot\":%llu,\"page\":0,\"pages\":1,\"connected\":false,\"clients\":[]}", id);
        send_packet(packet);
        return;
    }
    if (api.getChannelClientList(server, channel, &clients) != ERROR_ok) {
        send_packet("{\"v\":2,\"event\":\"bridge_status\",\"message\":\"Channel roster query failed\"}");
        return;
    }
    if (api.getChannelVariableAsString(server, channel, CHANNEL_NAME, &sdk_name) == ERROR_ok && sdk_name) {
        size_t length = strlen(sdk_name);
        if (length >= sizeof(channel_name)) {
            length = sizeof(channel_name) - 1;
            while (length && (((unsigned char)sdk_name[length] & 0xc0) == 0x80)) --length;
        }
        memcpy(channel_name, sdk_name, length);
        channel_name[length] = 0;
        api.freeMemory(sdk_name);
    }
    escape_name(channel_name, escaped_channel);
    while (clients[count]) ++count;
    pages = count ? (count + 7) / 8 : 1;
    for (page = 0; page < pages; ++page) {
        size_t index, used;
        used = (size_t)snprintf(packet, sizeof(packet),
            "{\"v\":2,\"event\":\"state\",\"snapshot\":%llu,\"page\":%u,\"pages\":%u,\"connected\":true,\"server\":\"%llu\",\"channel\":\"%llu\",\"channel_name\":\"%s\",\"clients\":[",
            id, (unsigned)page, (unsigned)pages, (unsigned long long)server, (unsigned long long)channel, escaped_channel);
        for (index = page * 8; index < count && index < (page + 1) * 8; ++index) {
            char escaped[3073];
            int talking = 0;
            unsigned int talk_error;
            get_name(server, clients[index], escaped);
            /* TeamSpeak requires the self API for the local transmitter. */
            if (clients[index] == self)
                talk_error = api.getClientSelfVariableAsInt(server, CLIENT_FLAG_TALKING, &talking);
            else
                talk_error = api.getClientVariableAsInt(server, clients[index], CLIENT_FLAG_TALKING, &talking);
            used += (size_t)snprintf(packet + used, sizeof(packet) - used,
                "%s{\"client_id\":%u,\"name\":\"%s\",\"talking\":%s,\"self\":%s}",
                index == page * 8 ? "" : ",", (unsigned)clients[index], escaped,
                talk_error != ERROR_ok ? "null" : (talking == STATUS_TALKING ? "true" : "false"),
                clients[index] == self ? "true" : "false");
        }
        snprintf(packet + used, sizeof(packet) - used, "]}");
        send_packet(packet);
    }
    api.freeMemory(clients);
    if (!reported_snapshot) {
        log_message("Channel snapshot sent successfully (roster available)", LogLevel_INFO);
        reported_snapshot = 1;
    }
}

/* JSON-escape a complete SDK nickname without truncating UTF-8 sequences. */
static void escape_name(const char* name, char* output) {
    static const char hex[] = "0123456789abcdef";
    const unsigned char* p = (const unsigned char*)name;
    while (*p) {
        unsigned char c = *p++;
        if (c == '"' || c == '\\') { *output++ = '\\'; *output++ = (char)c; }
        else if (c < 32) {
            *output++ = '\\'; *output++ = 'u'; *output++ = '0'; *output++ = '0';
            *output++ = hex[c >> 4]; *output++ = hex[c & 15];
        } else *output++ = (char)c;
    }
    *output = 0;
}

EXPORT void ts3plugin_onTalkStatusChangeEvent(uint64 server, int status, int whisper, anyID client) {
    char escaped[3073], packet[3584];
    uint64 channel = 0, client_channel = 0;
    anyID self = 0;
    int is_self;
    if (status != STATUS_TALKING && status != STATUS_NOT_TALKING) return;
    is_self = api.getClientID(server, &self) == ERROR_ok && self == client;
    if (api.getCurrentServerConnectionHandlerID && server != api.getCurrentServerConnectionHandlerID()) return;
    if (api.getChannelOfClient && (api.getChannelOfClient(server, self, &channel) != ERROR_ok ||
        api.getChannelOfClient(server, client, &client_channel) != ERROR_ok || channel != client_channel)) return;
    get_name(server, client, escaped);
    snprintf(packet, sizeof(packet),
        "{\"v\":2,\"event\":\"talk\",\"server\":\"%llu\",\"channel\":\"%llu\",\"client_id\":%u,\"name\":\"%s\",\"talking\":%s,\"whisper\":%s,\"self\":%s}",
        (unsigned long long)server, (unsigned long long)channel, (unsigned int)client, escaped,
        status == STATUS_TALKING ? "true" : "false", whisper ? "true" : "false", is_self ? "true" : "false");
    send_packet(packet);
}

/* Local PTT transitions are delivered independently of remote talk events. */
EXPORT void ts3plugin_onClientSelfVariableUpdateEvent(uint64 server, int flag, const char* old_value, const char* new_value) {
    anyID self;
    int status;
    (void)old_value;
    if (flag != CLIENT_FLAG_TALKING || !new_value) return;
    if (strcmp(new_value, "1") == 0) status = STATUS_TALKING;
    else if (strcmp(new_value, "0") == 0 || strcmp(new_value, "2") == 0) status = STATUS_NOT_TALKING;
    else return;
    if (api.getClientID(server, &self) == ERROR_ok)
        ts3plugin_onTalkStatusChangeEvent(server, status, 0, self);
}

EXPORT void ts3plugin_onConnectStatusChangeEvent(uint64 server, int status, unsigned int error) {
    char packet[128];
    (void)error;
    if (status == STATUS_CONNECTION_ESTABLISHED) { send_snapshot(); return; }
    if (status != STATUS_DISCONNECTED) return;
    snprintf(packet, sizeof(packet), "{\"v\":1,\"event\":\"reset\",\"server\":\"%llu\"}", (unsigned long long)server);
    send_packet(packet);
    send_snapshot();
}

EXPORT void ts3plugin_currentServerConnectionChanged(uint64 server) {
    (void)server; send_snapshot();
}
EXPORT void ts3plugin_onClientMoveEvent(uint64 server, anyID client, uint64 old_channel, uint64 new_channel, int visibility, const char* message) {
    (void)server; (void)client; (void)old_channel; (void)new_channel; (void)visibility; (void)message;
    send_snapshot();
}
