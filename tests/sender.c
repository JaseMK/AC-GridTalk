#include <stdio.h>
#include <string.h>
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdlib.h>
#include "teamspeak/public_definitions.h"
#include "ts3_functions.h"
void ts3plugin_setFunctionPointers(struct TS3Functions funcs);
int ts3plugin_init(void);
void ts3plugin_shutdown(void);
void ts3plugin_onTalkStatusChangeEvent(uint64 server, int status, int whisper, anyID client);
void ts3plugin_currentServerConnectionChanged(uint64 server);
void ts3plugin_onClientSelfVariableUpdateEvent(uint64 server, int flag, const char* old_value, const char* new_value);
static uint64 current = 123;
static int roster_size = 17;
static int self_talking;
static int remote_query_fails;
static unsigned int display(uint64 server, anyID client, char* name, size_t size) {
    (void)server; (void)client;
    snprintf(name, size, "Driver %u \"A\" \\ test\n", (unsigned)client);
    return 0;
}
static uint64 current_server(void) { return current; }
static unsigned int channel_of(uint64 server, anyID client, uint64* channel) {
    (void)server; *channel = client == 59 ? 8 : 7; return 0;
}
static unsigned int channel_clients(uint64 server, uint64 channel, anyID** clients) {
    int i; (void)server; (void)channel;
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
int main(void) {
    struct TS3Functions funcs = {0};
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
