#define WIN32_LEAN_AND_MEAN
#include <winsock2.h>
#include <ws2tcpip.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include "teamspeak/public_definitions.h"
#include "teamspeak/public_errors.h"
#include "ts3_functions.h"

#define EXPORT __declspec(dllexport)
#define STRINGIFY_INNER(value) #value
#define STRINGIFY(value) STRINGIFY_INNER(value)
#define STRINGIFY_PORT STRINGIFY(GRIDTALK_UDP_PORT)
#ifndef GRIDTALK_VERSION
#error "GRIDTALK_VERSION must be defined by the build (CMake project version)"
#endif
static struct TS3Functions api;
static SOCKET udp = INVALID_SOCKET;
static struct sockaddr_in destination;
static HWND timer_window;
static HINSTANCE plugin_instance;
static SRWLOCK lifecycle_lock = SRWLOCK_INIT;
static SRWLOCK socket_lock = SRWLOCK_INIT;
static CONDITION_VARIABLE callbacks_finished = CONDITION_VARIABLE_INIT;
static LONG active_callbacks;
static int running;
static DWORD owner_thread;
static LONG thread_warning;
static LONG snapshot_requested;
#define SNAPSHOT_TIMER 1
#define STOP_WINDOW (WM_APP + 1)
#define REFRESH_ROSTER (WM_APP + 2)
#define MAX_CLIENTS 256
static const wchar_t window_class[] = L"GridTalkNotifierSnapshotWindow";
static unsigned long long snapshot_id;
static int reported_send_error;
static volatile LONG reported_snapshot;
static void send_snapshot(void);
static int escape_name(const char* name, char* output, size_t capacity);
static void log_message(const char* text, enum LogLevel level);
BOOL WINAPI DllMain(HINSTANCE instance, DWORD reason, LPVOID reserved) {
    (void)reserved;
    if (reason == DLL_PROCESS_ATTACH) plugin_instance = instance;
    return TRUE;
}

static int begin_callback(void) {
    int accepted;
    AcquireSRWLockExclusive(&lifecycle_lock);
    accepted = running;
    if (accepted) ++active_callbacks;
    ReleaseSRWLockExclusive(&lifecycle_lock);
    if (accepted && GetCurrentThreadId() != owner_thread &&
            InterlockedCompareExchange(&thread_warning, 1, 0) == 0)
        log_message("Callback observed on another thread; lifecycle/socket access is serialized", LogLevel_WARNING);
    return accepted;
}

static void end_callback(void) {
    AcquireSRWLockExclusive(&lifecycle_lock);
    if (--active_callbacks == 0) WakeAllConditionVariable(&callbacks_finished);
    ReleaseSRWLockExclusive(&lifecycle_lock);
}

static LRESULT CALLBACK snapshot_window_proc(HWND window, UINT message, WPARAM wparam, LPARAM lparam) {
    if (message == WM_TIMER && wparam == SNAPSHOT_TIMER) {
        send_snapshot();
        return 0;
    }
    if (message == REFRESH_ROSTER) {
        InterlockedExchange(&snapshot_requested, 0);
        send_snapshot();
        return 0;
    }
    if (message == STOP_WINDOW) {
        if (!KillTimer(window, SNAPSHOT_TIMER))
            log_message("Snapshot timer cancellation failed", LogLevel_WARNING);
        DestroyWindow(window);
        return 0;
    }
    return DefWindowProcW(window, message, wparam, lparam);
}

static int append_json(char* output, size_t capacity, size_t* used, const char* format, ...) {
    int written;
    va_list arguments;
    if (*used >= capacity) return 0;
    va_start(arguments, format);
    written = vsnprintf(output + *used, capacity - *used, format, arguments);
    va_end(arguments);
    if (written < 0 || (size_t)written >= capacity - *used) {
        output[*used] = 0;
        log_message("Packet formatting exceeded its buffer; packet discarded", LogLevel_ERROR);
        return 0;
    }
    *used += (size_t)written;
    return 1;
}

static void log_message(const char* text, enum LogLevel level) {
    if (api.logMessage) api.logMessage(text, level, "Assetto Corsa Notifier", 0);
}

EXPORT const char* ts3plugin_name(void) { return "Assetto Corsa Notifier"; }
EXPORT const char* ts3plugin_version(void) { return GRIDTALK_VERSION; }
EXPORT int ts3plugin_apiVersion(void) { return 26; }
EXPORT const char* ts3plugin_author(void) { return "GridTalk"; }
EXPORT const char* ts3plugin_description(void) { return "Sends your TeamSpeak channel roster and live speaking status to GridTalk over local UDP (127.0.0.1:" STRINGIFY_PORT "). Enable this addon, then enable GridTalk in Assetto Corsa."; }
EXPORT void ts3plugin_setFunctionPointers(const struct TS3Functions funcs) { api = funcs; }

static void send_packet(const char* packet) {
    int send_error = 0;
    AcquireSRWLockExclusive(&socket_lock);
    if (udp != INVALID_SOCKET && sendto(udp, packet, (int)strlen(packet), 0,
               (const struct sockaddr*)&destination, sizeof(destination)) == SOCKET_ERROR && !reported_send_error) {
        send_error = WSAGetLastError();
        reported_send_error = 1;
    }
    ReleaseSRWLockExclusive(&socket_lock);
    if (send_error) {
        char error[128];
        snprintf(error, sizeof(error), "UDP send failed: Winsock error %d", send_error);
        log_message(error, LogLevel_ERROR);
    }
}

EXPORT int ts3plugin_init(void) {
    WSADATA data;
    u_long nonblocking = 1;
    WNDCLASSW window = {0};
    if (!api.getCurrentServerConnectionHandlerID || !api.getClientID || !api.getChannelOfClient ||
            !api.getChannelClientList || !api.getClientDisplayName || !api.getChannelVariableAsString ||
            !api.getClientVariableAsInt || !api.getClientSelfVariableAsInt || !api.freeMemory) return 1;
    if (WSAStartup(MAKEWORD(2, 2), &data)) return 1;
    udp = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (udp == INVALID_SOCKET || ioctlsocket(udp, FIONBIO, &nonblocking)) {
        if (udp != INVALID_SOCKET) closesocket(udp);
        udp = INVALID_SOCKET;
        WSACleanup();
        return 1;
    }
    destination.sin_family = AF_INET;
    destination.sin_port = htons(GRIDTALK_UDP_PORT);
    destination.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    reported_send_error = 0;
    reported_snapshot = 0;
    snapshot_id = GetTickCount64();
    owner_thread = GetCurrentThreadId();
    thread_warning = 0;
    snapshot_requested = 0;
    window.lpfnWndProc = snapshot_window_proc;
    window.hInstance = plugin_instance;
    window.lpszClassName = window_class;
    if (!RegisterClassW(&window)) goto init_failed;
    timer_window = CreateWindowExW(0, window_class, L"", 0, 0, 0, 0, 0,
                                   HWND_MESSAGE, NULL, plugin_instance, NULL);
    if (!timer_window || !SetTimer(timer_window, SNAPSHOT_TIMER, 1000, NULL)) {
        if (timer_window) DestroyWindow(timer_window);
        timer_window = NULL;
        UnregisterClassW(window_class, plugin_instance);
        goto init_failed;
    }
    AcquireSRWLockExclusive(&lifecycle_lock);
    running = 1;
    ReleaseSRWLockExclusive(&lifecycle_lock);
    log_message("v" GRIDTALK_VERSION " started: speaking events + 1-second channel snapshots to 127.0.0.1:" STRINGIFY_PORT, LogLevel_INFO);
    send_packet("{\"v\":1,\"source\":\"teamspeak\",\"event\":\"reset\"}");
    send_snapshot();
    return 0;
init_failed:
    log_message("Could not create channel snapshot window/timer", LogLevel_ERROR);
    closesocket(udp); udp = INVALID_SOCKET; WSACleanup(); return 1;
}

EXPORT void ts3plugin_shutdown(void) {
    HWND window;
    AcquireSRWLockExclusive(&lifecycle_lock);
    running = 0;
    ReleaseSRWLockExclusive(&lifecycle_lock);
    window = timer_window;
    /* No TIMERPROC address is stored in queued messages. Destroy the owning
       window on its thread before unregistering the DLL's window procedure. */
    if (window) SendMessageW(window, STOP_WINDOW, 0, 0);
    AcquireSRWLockExclusive(&lifecycle_lock);
    while (active_callbacks) SleepConditionVariableSRW(&callbacks_finished, &lifecycle_lock, INFINITE, 0);
    ReleaseSRWLockExclusive(&lifecycle_lock);
    timer_window = NULL; /* admitted callbacks have finished reading the handle */
    if (window && !UnregisterClassW(window_class, plugin_instance))
        log_message("Snapshot window class cleanup failed", LogLevel_WARNING);
    send_packet("{\"v\":1,\"source\":\"teamspeak\",\"event\":\"reset\"}");
    AcquireSRWLockExclusive(&socket_lock);
    if (udp != INVALID_SOCKET) {
        closesocket(udp);
        udp = INVALID_SOCKET;
        WSACleanup();
    }
    ReleaseSRWLockExclusive(&socket_lock);
}

static void get_name(uint64 server, anyID client, char* escaped) {
    char name[512] = {0};
    if (api.getClientDisplayName(server, client, name, sizeof(name)) != ERROR_ok) name[0] = 0;
    name[sizeof(name) - 1] = 0;
    /* Drop a partial UTF-8 tail if an SDK implementation truncates the buffer. */
    {
        size_t length = strlen(name), lead = length;
        while (lead && (((unsigned char)name[lead - 1] & 0xc0) == 0x80)) --lead;
        if (lead) {
            unsigned char c = (unsigned char)name[lead - 1];
            size_t expected = c >= 0xf0 ? 4 : c >= 0xe0 ? 3 : c >= 0xc0 ? 2 : 1;
            if (length - (lead - 1) < expected) name[lead - 1] = 0;
        }
    }
    escape_name(name, escaped, 3073);
}

static void send_snapshot_impl(void) {
    uint64 server, channel = 0;
    anyID self = 0, *clients = NULL;
    char channel_name[512] = {0}, escaped_channel[3073], *sdk_name = NULL;
    char packet[32768];
    size_t count = 0, pages, page;
    unsigned long long id = (unsigned long long)InterlockedIncrement64((volatile LONG64*)&snapshot_id);
    if (!api.getCurrentServerConnectionHandlerID) return;
    server = api.getCurrentServerConnectionHandlerID();
    if (!server || api.getClientID(server, &self) != ERROR_ok ||
        api.getChannelOfClient(server, self, &channel) != ERROR_ok) {
        size_t used = 0;
        if (append_json(packet, sizeof(packet), &used, "{\"v\":2,\"source\":\"teamspeak\",\"event\":\"state\",\"snapshot\":%llu,\"page\":0,\"pages\":1,\"connected\":false,\"clients\":[]}", id))
            send_packet(packet);
        return;
    }
    if (api.getChannelClientList(server, channel, &clients) != ERROR_ok) {
        send_packet("{\"v\":2,\"source\":\"teamspeak\",\"event\":\"bridge_status\",\"message\":\"Channel roster query failed\"}");
        if (clients) api.freeMemory(clients);
        return;
    }
    if (!clients) return;
    if (api.getChannelVariableAsString(server, channel, CHANNEL_NAME, &sdk_name) == ERROR_ok && sdk_name) {
        size_t length = strlen(sdk_name);
        if (length >= sizeof(channel_name)) {
            length = sizeof(channel_name) - 1;
            while (length && (((unsigned char)sdk_name[length] & 0xc0) == 0x80)) --length;
        }
        memcpy(channel_name, sdk_name, length);
        channel_name[length] = 0;
    }
    if (sdk_name) api.freeMemory(sdk_name);
    if (!escape_name(channel_name, escaped_channel, sizeof(escaped_channel))) {
        api.freeMemory(clients); return;
    }
    while (count <= MAX_CLIENTS && clients[count]) ++count;
    if (count > MAX_CLIENTS) {
        send_packet("{\"v\":2,\"source\":\"teamspeak\",\"event\":\"bridge_status\",\"message\":\"Channel exceeds 256 users\"}");
        api.freeMemory(clients); return;
    }
    pages = count ? (count + 7) / 8 : 1;
    for (page = 0; page < pages; ++page) {
        size_t index, used;
        used = 0;
        if (!append_json(packet, sizeof(packet), &used,
            "{\"v\":2,\"source\":\"teamspeak\",\"event\":\"state\",\"snapshot\":%llu,\"page\":%u,\"pages\":%u,\"connected\":true,\"server\":\"%llu\",\"channel\":\"%llu\",\"channel_name\":\"%s\",\"clients\":[",
            id, (unsigned)page, (unsigned)pages, (unsigned long long)server, (unsigned long long)channel, escaped_channel)) goto snapshot_failed;
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
            if (!append_json(packet, sizeof(packet), &used,
                "%s{\"client_id\":%u,\"name\":\"%s\",\"talking\":%s,\"self\":%s}",
                index == page * 8 ? "" : ",", (unsigned)clients[index], escaped,
                talk_error != ERROR_ok ? "null" : (talking == STATUS_TALKING ? "true" : "false"),
                clients[index] == self ? "true" : "false")) goto snapshot_failed;
        }
        if (!append_json(packet, sizeof(packet), &used, "]}")) goto snapshot_failed;
        send_packet(packet);
    }
    api.freeMemory(clients);
    if (InterlockedCompareExchange(&reported_snapshot, 1, 0) == 0) {
        log_message("Channel snapshot sent successfully (roster available)", LogLevel_INFO);
    }
    return;
snapshot_failed:
    api.freeMemory(clients);
}

static void send_snapshot(void) {
    if (!begin_callback()) return;
    send_snapshot_impl();
    end_callback();
}

/* Capacity-aware escaping; input is bounded independently by SDK buffers. */
static int escape_name(const char* name, char* output, size_t capacity) {
    static const char hex[] = "0123456789abcdef";
    const unsigned char* p = (const unsigned char*)name;
    size_t used = 0;
    if (!capacity) return 0;
    while (*p) {
        unsigned char c = *p++;
        size_t needed = c < 32 ? 6 : (c == '"' || c == '\\') ? 2 : 1;
        if (used + needed >= capacity) { output[0] = 0; return 0; }
        if (c == '"' || c == '\\') { output[used++] = '\\'; output[used++] = (char)c; }
        else if (c < 32) {
            output[used++] = '\\'; output[used++] = 'u'; output[used++] = '0'; output[used++] = '0';
            output[used++] = hex[c >> 4]; output[used++] = hex[c & 15];
        } else output[used++] = (char)c;
    }
    output[used] = 0;
    return 1;
}

EXPORT void ts3plugin_onTalkStatusChangeEvent(uint64 server, int status, int whisper, anyID client) {
    char escaped[3073], packet[3584];
    uint64 channel = 0, client_channel = 0;
    anyID self = 0;
    int is_self;
    size_t used = 0;
    if (!begin_callback()) return;
    if (status != STATUS_TALKING && status != STATUS_NOT_TALKING) goto talk_done;
    is_self = api.getClientID(server, &self) == ERROR_ok && self == client;
    if (api.getCurrentServerConnectionHandlerID && server != api.getCurrentServerConnectionHandlerID()) goto talk_done;
    if (api.getChannelOfClient && (api.getChannelOfClient(server, self, &channel) != ERROR_ok ||
        api.getChannelOfClient(server, client, &client_channel) != ERROR_ok || channel != client_channel)) goto talk_done;
    get_name(server, client, escaped);
    if (!append_json(packet, sizeof(packet), &used,
        "{\"v\":2,\"source\":\"teamspeak\",\"event\":\"talk\",\"server\":\"%llu\",\"channel\":\"%llu\",\"client_id\":%u,\"name\":\"%s\",\"talking\":%s,\"whisper\":%s,\"self\":%s}",
        (unsigned long long)server, (unsigned long long)channel, (unsigned int)client, escaped,
        status == STATUS_TALKING ? "true" : "false", whisper ? "true" : "false", is_self ? "true" : "false")) goto talk_done;
    send_packet(packet);
talk_done:
    end_callback();
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
    if (!begin_callback()) return;
    if (api.getClientID(server, &self) == ERROR_ok)
        ts3plugin_onTalkStatusChangeEvent(server, status, 0, self);
    end_callback();
}

EXPORT void ts3plugin_onConnectStatusChangeEvent(uint64 server, int status, unsigned int error) {
    char packet[128];
    size_t used = 0;
    (void)error;
    if (status == STATUS_CONNECTION_ESTABLISHED) { send_snapshot(); return; }
    if (status != STATUS_DISCONNECTED || !begin_callback()) return;
    if (append_json(packet, sizeof(packet), &used, "{\"v\":1,\"source\":\"teamspeak\",\"event\":\"reset\",\"server\":\"%llu\"}", (unsigned long long)server)) send_packet(packet);
    send_snapshot();
    end_callback();
}

EXPORT void ts3plugin_currentServerConnectionChanged(uint64 server) {
    (void)server; send_snapshot();
}
/* Queue one coalesced roster snapshot on the owner thread when a client enters,
   leaves, or changes within the current server tab's channel. Ignores unrelated
   activity so busy servers cause no extra roster queries. */
static void request_roster_refresh(uint64 server, anyID client, uint64 old_channel, uint64 new_channel) {
    anyID self = 0;
    uint64 current_channel = 0;
    if (!begin_callback()) return;
    if (server == api.getCurrentServerConnectionHandlerID() && api.getClientID(server, &self) == ERROR_ok &&
            (client == self || (api.getChannelOfClient(server, self, &current_channel) == ERROR_ok &&
             (old_channel == current_channel || new_channel == current_channel)))) {
        if (InterlockedCompareExchange(&snapshot_requested, 1, 0) == 0 &&
                !PostMessageW(timer_window, REFRESH_ROSTER, 0, 0))
            InterlockedExchange(&snapshot_requested, 0);
    }
    end_callback();
}

EXPORT void ts3plugin_onClientMoveEvent(uint64 server, anyID client, uint64 old_channel, uint64 new_channel, int visibility, const char* message) {
    (void)visibility; (void)message;
    request_roster_refresh(server, client, old_channel, new_channel);
}

EXPORT void ts3plugin_onClientMoveMovedEvent(uint64 server, anyID client, uint64 old_channel, uint64 new_channel, int visibility,
                                             anyID mover, const char* mover_name, const char* mover_uid, const char* message) {
    (void)visibility; (void)mover; (void)mover_name; (void)mover_uid; (void)message;
    request_roster_refresh(server, client, old_channel, new_channel);
}

EXPORT void ts3plugin_onClientMoveTimeoutEvent(uint64 server, anyID client, uint64 old_channel, uint64 new_channel, int visibility, const char* message) {
    (void)visibility; (void)message;
    request_roster_refresh(server, client, old_channel, new_channel);
}

EXPORT void ts3plugin_onClientKickFromChannelEvent(uint64 server, anyID client, uint64 old_channel, uint64 new_channel, int visibility,
                                                   anyID kicker, const char* kicker_name, const char* kicker_uid, const char* message) {
    (void)visibility; (void)kicker; (void)kicker_name; (void)kicker_uid; (void)message;
    request_roster_refresh(server, client, old_channel, new_channel);
}

EXPORT void ts3plugin_onClientKickFromServerEvent(uint64 server, anyID client, uint64 old_channel, uint64 new_channel, int visibility,
                                                  anyID kicker, const char* kicker_name, const char* kicker_uid, const char* message) {
    (void)visibility; (void)kicker; (void)kicker_name; (void)kicker_uid; (void)message;
    request_roster_refresh(server, client, old_channel, new_channel);
}

EXPORT void ts3plugin_onClientBanFromServerEvent(uint64 server, anyID client, uint64 old_channel, uint64 new_channel, int visibility,
                                                 anyID kicker, const char* kicker_name, const char* kicker_uid, uint64 time, const char* message) {
    (void)visibility; (void)kicker; (void)kicker_name; (void)kicker_uid; (void)time; (void)message;
    request_roster_refresh(server, client, old_channel, new_channel);
}

/* Display-name and other client property changes; refresh only for our channel. */
EXPORT void ts3plugin_onUpdateClientEvent(uint64 server, anyID client, anyID invoker, const char* invoker_name, const char* invoker_uid) {
    uint64 client_channel = 0;
    (void)invoker; (void)invoker_name; (void)invoker_uid;
    if (!begin_callback()) return;
    if (api.getChannelOfClient(server, client, &client_channel) == ERROR_ok)
        request_roster_refresh(server, client, client_channel, client_channel);
    end_callback();
}
