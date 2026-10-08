# GridTalk 0.4.1 hardening

Implemented the code improvements from the [v0.4.0 review](CODE_REVIEW.md).
TS5/TS6 integration remains deferred. The original review/evidence is preserved
as a baseline; this report describes the corrected implementation.

| Finding | Implementation | Evidence |
| --- | --- | --- |
| R1 resources | 32 KiB packets, 32 pages, 256 total committed users, 128 KiB normalized pending state per source, 256 pending talk overrides; 24 visible rows and 420-pixel width; long text clipped; only known fields retained | Large-roster/text/budget tests; at most 97 native labels |
| R2 JSON recursion | Bounded payload, iterative depth/node validation, decoder recursion exception handling compatible with older Python, bounded integer-literal conversion | Deep JSON and malformed real UDP tests |
| R3 stale recovery | Applicable voice data tracked separately from diagnostics/activity; timeout clears stored talk flags; invalid/obsolete/wrong-context packets cannot refresh voice state | Invalid and diagnostic packets cannot revive stale green; valid stop can recover connection |
| R4 source exhaustion | Validate before admission; unknown-source talk/reset reserves no slot; retire silent streams after 60 seconds; reclaim disconnected slots; bounded sequence-floor cache | Malformed-source flood, 16-source cap, retirement and obsolete-sequence tests |
| R5 contract mismatch | Exact integer checks/ranges, unique IDs within/across pages, consistent context/name/connection, coherent empty disconnected state, explicit legacy policy, Unicode/NUL validation | Positive and negative validation/schema tests; Unicode names preserved |
| R6 unknown queries | Last valid per-member state retained for at most five seconds; repeated null queries do not extend grace | Transient failure preserves held PTT; sustained failure clears green |
| R7 installer | Preflight required files, stage verified manifest, full app/asset backup, unique paths, rollback of app/DLL/legacy migration, verify before reporting success | Failure injected after staging/app/DLL/migration, missing asset, clean-install failure, repeated successful upgrades |
| R8 lifecycle | Owned message-only window; window timer carries no DLL callback pointer in queued messages; shutdown marshalled to owner, window/class destroyed, admitted callbacks drained; socket/lifecycle synchronization and atomic sequences; cross-thread warning | Concurrent talk/shutdown on both owner and foreign threads, 100 initialization/shutdown cycles, actual DLL unload followed by old-message dispatch in three separate loads |
| R9 move bursts | Ignore unrelated server/channel moves; relevant burst coalesced through one queued refresh; talk callbacks stay immediate | 200 unrelated moves cause no snapshot; 100 relevant moves cause one refresh per test cycle |
| R10 native bounds | Capacity-aware JSON escaping/append, checked formatter results, bounded SDK roster, partial UTF-8 tail handling, destination-port validation | Direct helper failure boundaries, worst-case 511-byte control-character names, incomplete UTF-8 SDK tail, 257-user native roster rejection |

## Resource and compatibility policy

The wire protocol remains v2, with the receiver limits now published in the JSON
Schema and sender guide. Valid ordinary TS3 packets are compatible. Over-limit
packets are rejected before changing roster/freshness state. There is no JSON
Schema dependency inside the AC app.

At most 256 users are committed across sources. The UI displays the first 24
users in stable alphabetical order with an overflow count; names are clipped to
fit 420 pixels. Normal small channels retain the existing white/bold-white names,
shaded lights/pills, automatic sizing, and transparency. Hidden controls are
reused and their count cannot grow past the display limit.

Voice data and individual known speaking states expire after five seconds.
Diagnostic packets can keep a bridge resident but cannot keep stale talk values
green. Silent sources and their names are removed after 60 seconds. A bounded
cache of 32 retired sequence floors retains old-packet protection for up to five
minutes after retirement; explicit reset clears a floor. Stable source IDs and
monotonic snapshot counters remain required.

The receive loop has a 1 ms budget checked every four packets in addition to the
32-packet cap. This is an approximate batch limit, not a guarantee that total
frame work stays below 1 ms. Up to one group, snapshot completion, and bounded UI
layout can exceed that budget. The static UI/resource limits constrain that
work. Loopback remains unauthenticated; source identity is not authorization.

## Performance

The final offline capture is [performance-hardened.json](performance-hardened.json).
The [updated audit probes](audit-after.json) show the original malformed-input,
stale revival, source exhaustion, and unbounded-window reproductions corrected.
It uses 3,000 samples per case, real UDP, the desktop Python runtime, and mocked
AC/native rendering. Two-user mean idle cost remains around 0.003 ms. Strict
validation adds packet-processing cost compared with v0.4.0; it is deliberate
work to bound malformed-input and retained-resource behavior. Unchanged roster
updates still make no native setters, and a single visible speaker transition
still makes five. Row order is cached; large-roster drawing is capped at 24 lamps.

The benchmark uses a simulated monotonic clock, so it measures full bounded
packet batches rather than exercising elapsed-wall-time early termination.
A separate regression checks budget termination. These are Python-side timings,
  not FPS results. Native AC rendering and actual TeamSpeak SDK CPU cost remain
unmeasured here. See [PERFORMANCE.md](PERFORMANCE.md) for A/B methodology.

## Reproduction

```powershell
cmake -S . -B build-tests -A x64 -DGRIDTALK_BUILD_TESTS=ON -DGRIDTALK_UDP_PORT=19999
cmake --build build-tests --config Release
python -m pip install --target build/schema-deps jsonschema==4.26.0
python tests/check_schema.py
python tests/check.py --port 19999 --build-dir build-tests --schema
python tests/receiver_hardening.py
python tests/native_hardening.py
./tests/installer.ps1
python tools/benchmark.py --output build/performance-hardened.json
```

Native/receiver checks use an isolated test port or ephemeral sockets. Installer
tests mutate only temporary fake roots under `build/`. The production DLL must
be built separately with port 9999 before installation.

## Remaining live validation

All reviewed code changes and offline regression work are implemented. Actual
TS3 enable/disable, server/channel changes, and prolonged sessions still need
verification in TeamSpeak, particularly its SDK thread/host interaction. The
synthetic tests establish our own lifecycle behavior; they cannot establish all
of the host's behavior. Next, run repeated FPS/CPU/memory A/B tests with both
components disabled, sender only, and sender plus visible GridTalk. Record the
installed revision and raw runs. No zero-FPS-impact claim is made.
