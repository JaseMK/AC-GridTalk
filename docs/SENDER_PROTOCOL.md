# GridTalk sender protocol v2

GridTalk receives channel rosters and speaking activity from local voice-app
bridges. Assetto Corsa Notifier is the TeamSpeak 3 sender included in this repo.
Other senders can implement this protocol without using TeamSpeak APIs.
Discord examples below illustrate the wire format; a working Discord bridge
is not included.

The machine-readable contract is [gridtalk-v2.schema.json](../protocol/gridtalk-v2.schema.json).
It uses JSON Schema Draft 2020-12. [Example packets](../protocol/examples)
cover every event. Application version 0.4.3 and wire version 2 are independent.

## Transport and limits

Send **one UTF-8 JSON object per UDP datagram** to `127.0.0.1:9999` (IPv4).
Do not prefix a length, concatenate objects, append binary data, or split one
JSON object between datagrams. The destination can be changed at build/config
time, but both sender and receiver must agree. GridTalk sends no acknowledgments
or requests. Only one receiver can bind this port; multiple senders may send to it.

- Receiver payload limit: **32,768 bytes**, including JSON and UTF-8 bytes.
  IPv4 permits larger datagrams, but this receiver rejects them.
- At most **32 pages**, eight users per page, and **256 committed users total**
  across all senders. The native TS sender also caps its channel at 256 users.
- Maximum normalized pending member storage: **128 KiB per source**; at most
  256 pending talk overrides. Only known fields are retained.
- At most **16 resident sources**. Valid data is required before admission;
  unknown-source talk/reset packets cannot reserve a slot. Disconnected sources
  may be reclaimed to admit another sender. Silent sources are retired after
  60 seconds; a bounded cache of 32 sequence floors lasts up to five minutes.
  Use stable source identities. Reset clears a retained sequence floor.
- JSON depth is limited to eight levels and 256 value nodes per datagram.
  Extra fields are ignored only within these limits. Strings must contain no NUL
  or unpaired Unicode surrogates. Integer literals longer than 17 characters
  are rejected before integer conversion; use safe integers for protocol fields.
- At most **32 packets per AC update**, with a **1 ms receive budget** checked
  every four datagrams. This bounds batches approximately, not total frame time:
  one group and subsequent bounded layout work can exceed the budget.
- Voice data becomes stale after five seconds without an applicable member
  event or complete fresh snapshot. Diagnostics, old snapshots, invalid packets,
  and wrong-channel talk events cannot refresh it or restore green indicators.
  Incomplete snapshots expire after five seconds from their first page.
- A `talking:null` query retains its last valid member value for at most five
  seconds. Repeated unknown queries do not extend that grace period.
- UI resources are capped at **24 rows** and **420 pixels wide**. Longer names
  are ellipsized; larger accepted rosters show a visible-user count. Hidden row
  controls are reused, with no more than 97 native labels including status.

UDP can lose, duplicate, or reorder packets. Periodic complete snapshots are
essential for recovery and for an overlay started after the voice app.
This is a local, unauthenticated interface; the receiver binds loopback only.
Do not send audio, credentials, or private server tokens.

## Shared envelope and identities

| Field | Type | Meaning |
| --- | --- | --- |
| `v` | integer | `2` for new senders. |
| `source` | string, 1–64 characters | Stable identity of one independent roster stream, e.g. `teamspeak`, `discord`, or `discord:account-a`. |
| `event` | string | `state`, `talk`, `reset`, or `bridge_status`. |

New senders should always supply `source`. For backward compatibility it is
optional in the schema; omission selects `default`. Two senders using the same
source overwrite each other's state. Distinct sources are isolated, including
reset, sequence numbers, timeout, and user IDs. The overlay combines their lists;
it does not merge the same human across providers or label rows by provider.

`server` and `channel` are opaque strings. They may represent a guild, workspace,
room, call, or synthetic local context. For an app with no servers, choose a
stable string such as `local`. Never encode a provider's 64-bit identifiers as
JSON numbers. `client_id` is a positive integer at most 9,007,199,254,740,991
(the interoperable JSON safe-integer limit). Map larger provider IDs to stable
local integers. Do not reuse a member ID within a channel while old events can
still arrive. Identity is scoped by source and current server/channel.

Unknown fields are allowed for extensions and ignored by this receiver. New
event types or versions require a receiver update. The schema describes valid
sender output; it does not imply the runtime applies a JSON Schema validator.

## `state`: authoritative roster snapshot

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `snapshot` | integer, 0–9,007,199,254,740,991 | yes | Strictly increasing sequence for the source's snapshots, including disconnects. |
| `page` | integer, 0–31 | yes | Zero-based page index; less than `pages`. |
| `pages` | integer, 1–32 | yes | Total number of pages. |
| `connected` | boolean | yes | Whether this source has a current voice channel. |
| `server` | string, 1–128 characters | when connected | Current server/workspace identity. |
| `channel` | string, 1–128 characters | when connected | Current channel/call identity. |
| `channel_name` | string, up to 512 characters | no | Human-readable channel name; defaults to empty. Currently not displayed. |
| `clients` | array, at most 8 items | yes | Members on this page. |

Each client has all four fields:

| Field | Type | Meaning |
| --- | --- | --- |
| `client_id` | positive safe integer | Stable identity in this channel. |
| `name` | string, up to 512 characters | Display name; may contain Unicode. Empty names display as `Client <id>`. |
| `talking` | boolean or null | `true` while transmitting; `false` otherwise. `null` means a query failed: retain the previous value in the same channel for up to five seconds from its last valid update, or assume false for a new member. |
| `self` | boolean | Whether this is the sender's local user. GridTalk adds `(you)`. |

Capture one consistent roster and split it into pages. All pages share source,
snapshot, page count, connection flag, server, channel, and channel name. Each
member ID must appear once across the complete snapshot. Page count does not
have to be a particular mathematical minimum, but pack pages efficiently.

GridTalk keeps the displayed roster until all pages arrive, then replaces it
atomically. Missing members are removed. Empty connected channels use one page
with `clients:[]`. Page order is unimportant. Repeated pages replace that page
while assembly is pending. Sequences at or below the last committed sequence
are ignored. Starting a newer snapshot abandons an older incomplete snapshot;
older incoming pages cannot replace the newer one.

Speaking events received while a matching snapshot is pending override that
snapshot's talk values. Avoid overlapping snapshots unnecessarily. Packets have
no per-event sequence or timestamp: delayed talk events can briefly regress a
flag until the next snapshot. There is no exact ordering guarantee over UDP.

Disconnected state must use `connected:false`, `page:0`, `pages:1`, and
`clients:[]`. Omit channel fields. Continue sending disconnected snapshots if
the bridge is still running. A disconnected snapshot clears only this source.

## `talk`: immediate transmission transition

Required fields beyond the envelope are `server`, `channel`, `client_id`, and
`talking` (boolean). Send `true` on transmission start and `false` on stop.
Track the voice app's actual transmitting state, including PTT hold/release and
mute behavior. Do not infer a stop merely because a held PTT emits no new event.

Optional `name` (string), `self` (boolean), and `whisper` (boolean) are
informational in talk packets. They do not update the roster or change its
filtering. Only a `state` snapshot adds users, renames them, or changes `self`.
Events for another server/channel or a user absent from the roster are ignored
for the current display. Matching events can also be retained for a pending
snapshot's members. `talking:null` is allowed only in snapshots, never here.

## `reset`: clear a source

`{"v":2,"source":"discord","event":"reset"}` clears that source's roster,
connection, pending pages, and sequence floor. Optional `server` scopes the
reset to that server: it is applied only when it matches the current server.
No other source is affected. A subsequent full snapshot re-establishes the list.

The bundled TS3 sender currently emits `v:1` resets for historical compatibility.
That exact legacy event is included in the schema. New senders should use v2.
Any other v1 packet, including legacy v1 talk events, is rejected as invalid.

## `bridge_status`: report a query/integration error

Required `message` is a string of at most 256 characters, e.g. `Voice roster query failed`. It displays a
diagnostic while preserving the source's last roster. A complete valid snapshot
clears the diagnostic. Diagnostic packets count as bridge activity but do not refresh voice data or
speaking flags. They must not be used as the sole voice-state heartbeat. Use unknown
talk values in a fresh roster when a speaking query fails.

## Startup, restart, and shutdown

1. Choose a stable source. Send a reset at startup followed by a full snapshot.
2. Send immediate talk transitions and a fresh complete snapshot about once a
   second, including current transmitting states.
3. Use one increasing snapshot counter across all channels for this source.
   Prefer a system monotonic-clock millisecond value with a local increment
   when multiple snapshots occur within the same millisecond. Persist a counter
   if the chosen clock starts again at zero for each process.
4. Keep sequences increasing across sender restarts where possible. Reset is
   itself UDP and may be lost; a restart that starts again at sequence 1 can be
   ignored by an existing receiver if the reset was lost. A five-second voice timeout does not reset the sequence floor. Silent sources
   are retired at 60 seconds; their floor is retained in a bounded cache for up
   to five more minutes. Do not rely on eviction to restart sequence numbering. After a full machine reboot the AC receiver
   also restarts, so a system-uptime counter is suitable for the bundled sender.
5. On channel change send a snapshot for the new context. On disconnect send
   the disconnected snapshot. On clean shutdown send reset; if it is lost, the
   five-second timeout clears speaking indicators. Silent sources and their names
   are removed after 60 seconds.

The local sender determines which channel and users to expose. The TS3 sender
uses the current channel of the active server tab. Other providers can choose
their current call, but should publish one coherent roster per source.

## Minimal Python transport example

```python
import json
import socket

udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

def send(packet):
    data = json.dumps(packet, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(data) > 32768:
        raise ValueError("UDP packet too large; paginate the roster")
    udp.sendto(data, ("127.0.0.1", 9999))

send({"v": 2, "source": "my-voice-app", "event": "reset"})
send({"v": 2, "source": "my-voice-app", "event": "state",
      "snapshot": 1000, "page": 0, "pages": 1, "connected": True,
      "server": "local", "channel": "race", "channel_name": "Race chat",
      "clients": [{"client_id": 1, "name": "Driver", "talking": False, "self": True}]})
send({"v": 2, "source": "my-voice-app", "event": "talk",
      "server": "local", "channel": "race", "client_id": 1, "talking": True})
# Wire up actual voice events and periodic snapshots in your bridge.
# Send talking:false on stop, then reset on clean shutdown.
udp.close()
```

## Validation and integration checklist

Install the development validator and check the schema/examples:

```powershell
python -m pip install -r requirements-dev.txt  # inside the venv described in the README
python tests/check_schema.py
python tests/check.py --port 19999 --build-dir build-tests --schema
```

The last command requires the native test build documented in the README.
It validates real DLL packets against this schema, alongside receiver tests.
The schema cannot express cross-packet rules, `page < pages`, or unique member
IDs across pages; sender tests must also check these semantic constraints.

Before releasing a new bridge, test held/rapid PTT, simultaneous speakers,
lost start/stop packets, incomplete/reordered pages, a receiver started later,
renaming/joining/leaving users, channel changes, reconnects, restart sequences,
Unicode names, and another sender using overlapping client IDs. Verify that a
reset or timeout affects only your source. Measure CPU and in-game FPS with the
bridge and overlay enabled and disabled; schema conformance does not establish
provider integration correctness or performance.
