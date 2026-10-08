# GridTalk code assessment — 8 October 2026

## Scope and result

Reviewed the current DLL, GridTalk receiver, installer, CMake build, public
protocol, tests, diagnostic tools, and UI asset generation. This includes the
rename and multiple-source implementation, plus the previous performance work.
This is an assessment: the findings below have not been fixed in this pass.

The small-channel design is efficient: nonblocking I/O, bounded packet count,
periodic snapshots, cached UI layout, and five native setters per changed
speaker. The biggest remaining risks are incomplete validation, stale-state
recovery, and excessive resource use from unexpected sender output. No remote
code-execution vulnerability or currently reachable C buffer overflow was
identified in this review. That is a review result, not a security guarantee.

P1 means fix before broadly supporting third-party senders; P2 means address
before a polished release; P3 means useful follow-up. Confirmed findings use
isolated probes or directly demonstrated control flow. Risks requiring host or
SDK validation are explicitly marked. Local denial of service is distinguished
from internet exposure: the socket binds only `127.0.0.1`.

## Findings, in priority order

### R1 — P1: accepted rosters and names can overwhelm native UI resources

**Confirmed.** Receiver validation permits 8,192 pages × eight clients per
source, with 16 sources; there is no smaller total-member, retained-byte, display
row, name-length, or window-size limit. Every displayed member allocates four
native labels. Hidden controls are retained for the rest of the app session.
The 32-packet limit does not bound work when the final snapshot page triggers a
large merge, sort, and layout.

An isolated 1,024-user snapshot created **4,097 native labels** (including the
status label) and a **28,708-pixel-high** window. A 10,000-character name produced
a **156,916-pixel-wide** window. The former took approximately 6.7 ms even with
native calls mocked; actual AC rendering/resource cost was not measured.
Large retained extension fields can also inflate pending and committed state.

References: `GridTalk.py:119`, `GridTalk.py:139`, `GridTalk.py:242`,
`GridTalk.py:260`. Recommended: set practical caps on total users, pages,
strings, snapshot bytes, and pending events; retain only known fields; clip
display text and viewport size; bound native controls. Add stress checks and
measure peak completion/layout latency. Do not rely on pagination alone.

### R2 — P1: deeply nested JSON escapes the update callback

**Confirmed in the desktop Python runtime.** A 20,001-byte packet containing
10,000 nested arrays makes `json.loads` raise `RecursionError`. `acUpdate` catches
only `ValueError` and `UnicodeError`, so this exception leaves the callback.
The exact game-host reaction and nesting threshold require AC embedded-Python
validation; the escape from the callback is reproduced. Similar deep structures
in extension fields can make state comparison expensive or recursive.

Reference: `GridTalk.py:334`. Recommended: impose a conservative protocol
payload/depth boundary and handle decoder recursion failures. Use exception
handling compatible with AC's embedded Python version. Filter unknown fields
before retaining packets. Add actual socket-level malformed-input checks.

### R3 — P2: invalid packets and diagnostics can revive stale speaking flags

**Confirmed.** `self.last_packet` updates before event-specific validation and
before checking channel/sequence applicability. A held speaker correctly turns
red after timeout, but a packet containing just
`{"v":2,"source":"teamspeak","event":"talk"}` refreshes that source and
restores the previous green flag without a valid talk or roster update. A valid
`bridge_status` has the same effect. Repeated obsolete snapshots or wrong-channel
events can also keep an out-of-date roster appearing fresh.

References: `GridTalk.py:87`, `GridTalk.py:107`, `GridTalk.py:194`.
Recommended: separate bridge/process liveness from trustworthy roster/speaking
freshness. Invalid packets must not update either. After stale recovery, require
a complete fresh snapshot or valid member-specific update before restoring a
green flag. Diagnostic messages may prove the process is alive, not that the
voice state is current. Keep per-member freshness when necessary.

### R4 — P2: malformed sources permanently exhaust the sender limit

**Confirmed.** Source allocation happens before complete event validation.
Sixteen packets with distinct sources and an incomplete `state` occupy every
slot; a subsequent correct TeamSpeak snapshot is ignored. No idle-source
eviction or explicit reclamation occurs until `acShutdown`.

Reference: `GridTalk.py:181`. Recommended: validate before admitting a source,
reclaim genuinely disconnected/expired streams with bounded retention, and
report the limit once rather than silently discarding a legitimate sender.
Preserve sequence protection deliberately when evicting/restarting sources.

### R5 — P2: runtime validation is looser than the published contract

**Confirmed.** Python booleans satisfy `isinstance(value, int)`, so `client_id:true`
is accepted and aliases numeric ID 1. IDs and snapshot sequences are not checked
against the schema's positive/safe-integer ranges. Duplicate member IDs silently
overwrite names, including duplicates across pages. A `connected:false` snapshot
can include clients, even though the schema requires an empty single page.
`channel_name` consistency is not checked between pages; final state uses the
last arriving page's name. `v:1` state/status events are admitted despite the
published legacy support being limited to resets and upgrade diagnostics.

References: `GridTalk.py:82`, `GridTalk.py:107`, `GridTalk.py:119`,
`GridTalk.py:139`, `GridTalk.py:146`, `GridTalk.py:165`.
Recommended: implement a lightweight explicit validator before mutation, with
exact integer checks, bounds, coherent disconnected state, unique member IDs,
matching page metadata, and a defined legacy policy. Keep the full JSON Schema
validator development-only to avoid a new game runtime dependency. Add rejection
tests, not just valid-packet tests.

### R6 — P2: unknown speaking queries can preserve green indefinitely

**Confirmed behavior; currently intentional policy.** A user whose last known
state was speaking remains green through arbitrarily many complete snapshots
with `talking:null`. Healthy packet traffic prevents the global timeout. This
protects held PTT from transient query failures but can misleadingly retain green
when the provider's query fails persistently. The same risk applies to a bridge
that repeatedly sends only diagnostics.

Reference: `GridTalk.py:156`. Recommended: keep the last valid state for a short
grace period, then show an unknown state or inactive indicator with a diagnostic.
Use per-member timestamps so a different active sender cannot mask this failure.
Document the resulting policy and test both transient and sustained failure.

### R7 — P2: installer can leave a mixed installation and incomplete backup

**Confirmed from control flow; failure injection not performed on the live install.**
The installer backs up the DLL and Python file but not existing GridTalk assets.
It copies new files directly, then migrates old names. Missing assets, a locked
file, permission failure, or migration failure can leave a partial upgrade.
There is no rollback. Success-looking messages precede the migration. Backup
folders use second-resolution timestamps, so rapid repeated runs can collide.

References: `Install.ps1:18`, `Install.ps1:22`, `Install.ps1:29`,
`Install.ps1:32`, `Install.ps1:38`.
Recommended: preflight every required asset and destination, stage the complete
new installation, back up the whole previous app directory, and roll back on
failure. Use unique backup paths and report completion only after verifying
installed hashes and migration. Test failure scenarios in temporary fake roots.

### R8 — P2: native timer/thread/unload safety is not established

**Host-dependent risk, not a reproduced crash.** A windowless `SetTimer` is
created on the init thread and invokes DLL code through that thread's message
dispatch. SDK callbacks also mutate/read global socket, snapshot counter, and
API state without synchronization. The tests dispatch one thread's message loop;
they do not establish that every real TS callback, shutdown, and timer runs on
that same thread or that unload has no queued timer callback.

References: `plugin.c:23`, `plugin.c:67`, `plugin.c:78`, `plugin.c:101`.
Microsoft documents that [SetTimer callbacks require dispatch on the calling
thread](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-settimer),
and that [KillTimer does not remove already posted timer messages](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-killtimer).
These facts justify testing the lifecycle; they do not establish that TeamSpeak
actually exhibits an unload race.

Recommended: instrument actual callback thread IDs and repeated enable/disable,
connect/disconnect, and shutdown under activity. Establish the SDK/host thread
contract, check timer cancellation results, and ensure queued callbacks cannot
reach an unloaded DLL. Add a shutdown guard and serialize state where the real
contract requires it. A guard alone cannot protect code after DLL unload.

### R9 — P2: move callbacks can produce avoidable full-roster bursts

**Confirmed from code; real cost not yet measured.** Every client move triggers
`send_snapshot`, even on another server or in unrelated channels. For a channel
of N members and M unrelated moves, this can do roughly O(N × M) SDK queries,
JSON construction, and UDP sends. Large bursts can compete with useful talk
packets in the receiver queue.

Reference: `plugin.c:217`. Recommended: filter moves to the current server and
current/previous local channel, coalesce relevant roster changes, and retain the
one-second recovery timer. Keep immediate talk transitions separate. Measure
the live SDK cost before introducing complex caching.

### R10 — P3: native buffer safety depends on implicit size assumptions

**Hardening opportunity, not a confirmed overflow.** The current 511-byte input
name bound, worst-case six-byte JSON escaping, eight-client pages, and fixed
metadata fit the buffers: an escaped name needs at most 3,067 bytes including
NUL; snapshot packets fit comfortably below 32,768 bytes. However, `escape_name`
has no output capacity parameter, and `snprintf` returns are added to `used`
without checking truncation/negative values. Future changes to field sizes or
pagination could make `packet + used` invalid.

References: `plugin.c:88`, `plugin.c:129`, `plugin.c:142`, `plugin.c:159`.
Recommended: capacity-aware escaping/append helpers, checked formatter returns,
and maximum-length/control-character tests. The SDK nickname API should also be
tested for valid UTF-8 at its buffer boundary; channel truncation already avoids
cutting UTF-8 continuation bytes. Do not claim the SDK returns broken names
without reproducing that behavior.

## Security boundary

Loopback binding and destination prevent direct LAN/internet delivery in the
normal configuration. Incoming JSON never reaches `eval`, shell commands, or
file paths; assets use fixed repository paths. SDK strings are JSON-escaped.
The protocol contains display names and channel metadata, not audio. SDK-owned
lists/strings are released on the successful paths reviewed.

Any local process can nevertheless forge a source, talk event, roster, or reset.
`source` is an identity label, not authentication. Another process can occupy the
port before GridTalk starts; the app reports the bind failure. Binding loopback
does not solve local spoofing or resource exhaustion. For the intended personal
local setup, strict validation and resource bounds are the first improvements.
If trusted-sender authentication becomes a requirement, define it as a separate
protocol/design change rather than presenting source strings as protection.

## Efficiency assessment

Normal two-user operation measures roughly 0.0024 ms per idle Python update and
0.0111 ms per speaking transition in the desktop mocked-UI benchmark. No
background busy loop, audio processing, or server network polling is present.
The one-second TS snapshot reads SDK state; its real host cost remains unmeasured.
See [PERFORMANCE.md](PERFORMANCE.md) and its raw captures.

Sorting and scanning the complete roster still occur on a speaking change,
despite only five native setter calls. Cache row order/client-to-row mappings if
larger supported rosters show measurable cost. Avoid repeated unchanged
diagnostic-triggered work. Multiple-source merging is already coalesced once
per AC receive batch. Do not introduce threads or a runtime schema dependency
merely to optimize the tiny normal-channel idle cost.

FPS, native rendering, the old embedded Python runtime, simultaneous-source
stress, and TS SDK CPU cost need controlled in-game testing. The existing raw
microbenchmarks exclude those costs and cannot prove zero FPS impact.

## Evidence and coverage

- Release builds succeeded. The compiler warning observed is C4201 from the
  upstream SDK's anonymous union.
- Existing native/receiver regressions pass: held/rapid PTT, real Windows timer,
  snapshots, missing-stop recovery, reordering, channel filtering, layer order,
  packet count limit, and independent source/reset/timeout handling.
- The public schema and example packets validate, and captured native output
  passes the schema. These checks do not validate every malformed-input path.
- [audit-evidence.json](audit-evidence.json) records the isolated reproductions.
  Run `python tools/audit_receiver.py` to regenerate a capture in ignored
  `build/audit-evidence.json`. It uses mocked AC calls and ephemeral sockets,
  never the installed game port. This probe records observed behavior; it is
  not a test that requires these bugs to remain.

Not completed: real TS thread/unload testing, native memory instrumentation,
installer failure injection, live AC display/resource stress, or FPS A/B runs.

## Recommended implementation order

1. Implement strict admission/validation and resource limits; add regressions
   for deep JSON, source exhaustion, duplicates, oversized strings/rosters, and
   coherent disconnected pages. Keep validation lightweight in AC.
2. Separate data freshness from bridge liveness; bound unknown-state retention
   and verify stale recovery independently for each sender/member.
3. Filter/coalesce TS roster refreshes and validate timer/thread/unload behavior
   in the actual host. Add checked native formatting and boundary tests.
4. Make install/upgrade rollback complete and verify renamed live addon/app
   behavior. The assessed changes are now versioned 0.4.0; give subsequent
   hardening builds a distinct version or build revision. The old release tag
   remains immutable.
5. Run repeatable FPS/CPU/memory A/B tests on the hardened revision, then profile
   only demonstrated hotspots. Save raw data, exact revision, and settings.
