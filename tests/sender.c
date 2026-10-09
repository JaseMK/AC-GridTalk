#include <stdio.h>
#include <string.h>
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdlib.h>
#include "teamspeak/public_definitions.h"
#include "ts3_functions.h"
#ifdef GRIDTALK_DYNAMIC_LIBRARY
#define PLUGIN_FUNCTION(name, result, args) typedef result (*name##_type) args; static name##_type name
PLUGIN_FUNCTION(ts3plugin_setFunctionPointers, void, (struct TS3Functions));
PLUGIN_FUNCTION(ts3plugin_init, int, (void));
PLUGIN_FUNCTION(ts3plugin_shutdown, void, (void));
PLUGIN_FUNCTION(ts3plugin_onTalkStatusChangeEvent, void, (uint64, int, int, anyID));
PLUGIN_FUNCTION(ts3plugin_currentServerConnectionChanged, void, (uint64));
PLUGIN_FUNCTION(ts3plugin_onClientSelfVariableUpdateEvent, void, (uint64, int, const char*, const char*));
PLUGIN_FUNCTION(ts3plugin_onClientMoveEvent, void, (uint64, anyID, uint64, uint64, int, const char*));
PLUGIN_FUNCTION(ts3plugin_onClientMoveMovedEvent, void, (uint64, anyID, uint64, uint64, int, anyID, const char*, const char*, const char*));
PLUGIN_FUNCTION(ts3plugin_onClientMoveTimeoutEvent, void, (uint64, anyID, uint64, uint64, int, const char*));
PLUGIN_FUNCTION(ts3plugin_onUpdateClientEvent, void, (uint64, anyID, anyID, const char*, const char*));
#else
void ts3plugin_setFunctionPointers(struct TS3Functions funcs);
int ts3plugin_init(void);
void ts3plugin_shutdown(void);
void ts3plugin_onTalkStatusChangeEvent(uint64 server, int status, int whisper, anyID client);
void ts3plugin_currentServerConnectionChanged(uint64 server);
void ts3plugin_onClientSelfVariableUpdateEvent(uint64 server, int flag, const char* old_value, const char* new_value);
void ts3plugin_onClientMoveEvent(uint64 server, anyID client, uint64 old_channel, uint64 new_channel, int visibility, const char* message);
void ts3plugin_onClientMoveMovedEvent(uint64 server, anyID client, uint64 old_channel, uint64 new_channel, int visibility, anyID mover, const char* mover_name, const char* mover_uid, const char* message);
void ts3plugin_onClientMoveTimeoutEvent(uint64 server, anyID client, uint64 old_channel, uint64 new_channel, int visibility, const char* message);
void ts3plugin_onUpdateClientEvent(uint64 server, anyID client, anyID invoker, const char* invoker_name, const char* invoker_uid);
#endif
static uint64 current = 123;
static int roster_size = 17;
static int self_talking;
static int remote_query_fails;
static int boundary_mode;
static int roster_queries;
static volatile LONG worker_running;
static unsigned int display(uint64 server, anyID client, char* name, size_t size) {
    (void)server; (void)client;
    if (boundary_mode) {
        memset(name, boundary_mode == 1 ? 1 : 'W', size - 1);
        if (boundary_mode == 2) name[size - 2] = (char)0xe2; /* deliberately incomplete UTF-8 tail */
        name[size - 1] = 0;
        return 0;
    }
    snprintf(name, size, "Driver %u \"A\" \\ test\n", (unsigned)client);
    return 0;
}
static uint64 current_server(void) { return current; }
static unsigned int channel_of(uint64 server, anyID client, uint64* channel) {
    (void)server; *channel = client == 59 ? 8 : 7; return 0;
}
static unsigned int channel_clients(uint64 server, uint64 channel, anyID** clients) {
    int i; (void)server; (void)channel;
    ++roster_queries;
    *clients = calloc((size_t)roster_size + 1, sizeof(anyID));
    for (i = 0; i < roster_size; ++i) (*clients)[i] = (anyID)(42 + i);
    return 0;
}
static unsigned int channel_name(uint64 server, uint64 channel, size_t flag, char** name) {
    (void)server; (void)channel; (void)flag;
    *name = _strdup("Race \"chat\""); return 0;
}
static unsigned int talking(uint64 server, anyID client, size_t flag, int* value) {
    (void)server; (void)flag;
    /* The generic query is deliberately wrong for self: catches regression. */
    *value = client == 43;
    return remote_query_fails || client == 42 ? 1 : 0;
}
static unsigned int own_talking(uint64 server, size_t flag, int* value) {
    (void)server; (void)flag; *value = self_talking; return 0;
}
static unsigned int release(void* value) { free(value); return 0; }
static unsigned int own_id(uint64 server, anyID* client) {
    (void)server; *client = 42; return 0;
}
static DWORD WINAPI talk_worker(LPVOID unused) {
    (void)unused;
    while (InterlockedCompareExchange(&worker_running, 0, 0)) {
        ts3plugin_onTalkStatusChangeEvent(123, STATUS_TALKING, 0, 43);
        ts3plugin_onTalkStatusChangeEvent(123, STATUS_NOT_TALKING, 0, 43);
        Sleep(1);
    }
    return 0;
}
static void dispatch_pending(void) {
    MSG message;
    while (PeekMessage(&message, NULL, 0, 0, PM_REMOVE)) DispatchMessage(&message);
}
static DWORD WINAPI shutdown_worker(LPVOID unused) {
    (void)unused;
    ts3plugin_shutdown();
    return 0;
}

int main(int argc, char** argv) {
    struct TS3Functions funcs = {0};
#ifdef GRIDTALK_DYNAMIC_LIBRARY
    HMODULE library;
    MSG held_message = {0};
    int stress = 1;
    if (argc < 2 || !(library = LoadLibraryA(argv[1]))) return 7;
#define LOAD_FUNCTION(name) name = (name##_type)GetProcAddress(library, #name); if (!name) return 8
    LOAD_FUNCTION(ts3plugin_setFunctionPointers);
    LOAD_FUNCTION(ts3plugin_init);
    LOAD_FUNCTION(ts3plugin_shutdown);
    LOAD_FUNCTION(ts3plugin_onTalkStatusChangeEvent);
    LOAD_FUNCTION(ts3plugin_currentServerConnectionChanged);
    LOAD_FUNCTION(ts3plugin_onClientSelfVariableUpdateEvent);
    LOAD_FUNCTION(ts3plugin_onClientMoveEvent);
    LOAD_FUNCTION(ts3plugin_onClientMoveMovedEvent);
    LOAD_FUNCTION(ts3plugin_onClientMoveTimeoutEvent);
    LOAD_FUNCTION(ts3plugin_onUpdateClientEvent);
#else
    int stress = argc > 1 && strcmp(argv[1], "--stress") == 0;
    if (argc > 1 && strcmp(argv[1], "--boundary") == 0) boundary_mode = 1;
    if (argc > 1 && strcmp(argv[1], "--utf8") == 0) boundary_mode = 2;
    if (argc > 1 && strcmp(argv[1], "--oversize") == 0) roster_size = 257;
#endif
    funcs.getClientDisplayName = display;
    funcs.getClientID = own_id;
    funcs.getCurrentServerConnectionHandlerID = current_server;
    funcs.getChannelOfClient = channel_of;
    funcs.getChannelClientList = channel_clients;
    funcs.getChannelVariableAsString = channel_name;
    funcs.getClientVariableAsInt = talking;
    funcs.getClientSelfVariableAsInt = own_talking;
    funcs.freeMemory = release;
    ts3plugin_setFunctionPointers(funcs);
    if (stress) {
        int cycle, index;
        HANDLE worker;
        funcs.getClientID = NULL;
        ts3plugin_setFunctionPointers(funcs);
        if (ts3plugin_init() == 0) return 2;
        funcs.getClientID = own_id;
        ts3plugin_setFunctionPointers(funcs);
        for (cycle = 0; cycle < 25; ++cycle) {
            roster_queries = 0;
            if (ts3plugin_init()) return 3;
            for (index = 0; index < 100; ++index) {
                ts3plugin_onClientMoveEvent(456, 43, 7, 8, 0, "");
                ts3plugin_onClientMoveEvent(123, 43, 8, 9, 0, "");
            }
            dispatch_pending();
            if (roster_queries != 1) return 4; /* no unrelated full-roster queries */
            for (index = 0; index < 100; ++index)
                ts3plugin_onClientMoveEvent(123, 43, 7, 8, 0, "");
            dispatch_pending();
            if (roster_queries != 2) return 5; /* relevant burst coalesced */
            for (index = 0; index < 100; ++index) {
                ts3plugin_onClientMoveMovedEvent(456, 43, 7, 8, 0, 44, "Admin", "uid", "");
                ts3plugin_onClientMoveMovedEvent(123, 43, 8, 9, 0, 44, "Admin", "uid", "");
                ts3plugin_onUpdateClientEvent(123, 59, 44, "Admin", "uid"); /* client 59 is in channel 8 */
                ts3plugin_onUpdateClientEvent(456, 43, 44, "Admin", "uid");
            }
            dispatch_pending();
            if (roster_queries != 2) return 12; /* unrelated moved-by-other/updates ignored */
            for (index = 0; index < 100; ++index)
                ts3plugin_onClientMoveMovedEvent(123, 43, 7, 8, 0, 44, "Admin", "uid", "");
            dispatch_pending();
            if (roster_queries != 3) return 13; /* moved-by-other in our channel coalesced */
            for (index = 0; index < 100; ++index) {
                ts3plugin_onClientMoveTimeoutEvent(123, 43, 7, 0, 0, "");
                ts3plugin_onUpdateClientEvent(123, 43, 44, "Admin", "uid");
            }
            dispatch_pending();
            if (roster_queries != 4) return 14; /* timeout/rename in our channel coalesced */
            InterlockedExchange(&worker_running, 1);
            worker = CreateThread(NULL, 0, talk_worker, NULL, 0, NULL);
            if (!worker) return 6;
            Sleep(5);
#ifdef GRIDTALK_DYNAMIC_LIBRARY
            if (cycle == 24) {
                HWND window = FindWindowExW(HWND_MESSAGE, NULL, L"GridTalkNotifierSnapshotWindow", NULL);
                if (!window || !PostMessageW(window, WM_TIMER, 1, 0) ||
                        !PeekMessageW(&held_message, window, WM_TIMER, WM_TIMER, PM_REMOVE)) return 9;
            }
#endif
            if (cycle % 2) {
                HANDLE shutdown = CreateThread(NULL, 0, shutdown_worker, NULL, 0, NULL);
                if (!shutdown) return 11;
                while (WaitForSingleObject(shutdown, 0) == WAIT_TIMEOUT) { dispatch_pending(); Sleep(1); }
                CloseHandle(shutdown);
            } else ts3plugin_shutdown(); /* races worker callbacks; waits for admitted SDK calls */
            dispatch_pending(); /* no callback into the destroyed timer window */
            InterlockedExchange(&worker_running, 0);
            WaitForSingleObject(worker, INFINITE);
            CloseHandle(worker);
            ts3plugin_onTalkStatusChangeEvent(123, STATUS_TALKING, 0, 43); /* safely ignored */
        }
#ifdef GRIDTALK_DYNAMIC_LIBRARY
        if (!FreeLibrary(library)) return 10;
        DispatchMessageW(&held_message); /* old timer message after the DLL is actually unloaded */
        dispatch_pending();
#endif
        puts("PASS: 25 native lifecycle cycles, threaded talk/shutdown, move/kick/rename filtering and coalescing");
        return 0;
    }
    if (ts3plugin_init()) return 1;
    ts3plugin_onTalkStatusChangeEvent(123, STATUS_TALKING, 1, 42);
    ts3plugin_onTalkStatusChangeEvent(123, STATUS_NOT_TALKING, 1, 42);
    ts3plugin_onTalkStatusChangeEvent(123, STATUS_TALKING_WHILE_DISABLED, 0, 42);
    ts3plugin_onTalkStatusChangeEvent(123, STATUS_TALKING, 0, 59);
    self_talking = 1;
    ts3plugin_onClientSelfVariableUpdateEvent(123, CLIENT_FLAG_TALKING, "0", "1");
    /* Exercise the real timer/message loop, not a manually invoked snapshot. */
    roster_size = 2;
    {
        ULONGLONG until = GetTickCount64() + 1150;
        MSG message;
        while (GetTickCount64() < until) {
            while (PeekMessage(&message, NULL, 0, 0, PM_REMOVE)) DispatchMessage(&message);
            Sleep(10);
        }
    }
    self_talking = 0;
    ts3plugin_onClientSelfVariableUpdateEvent(123, CLIENT_FLAG_TALKING, "1", "0");
    self_talking = 1;
    ts3plugin_onClientSelfVariableUpdateEvent(123, CLIENT_FLAG_TALKING, "0", "1");
    self_talking = 0;
    ts3plugin_onClientSelfVariableUpdateEvent(123, CLIENT_FLAG_TALKING, "1", "0");
    remote_query_fails = 1;
    ts3plugin_currentServerConnectionChanged(123);
    current = 0;
    ts3plugin_currentServerConnectionChanged(0);
    ts3plugin_shutdown();
    return 0;
}
