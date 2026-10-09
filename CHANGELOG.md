# Changelog

## 0.4.3 — unreleased

- Report the real version: the DLL takes it from the CMake project version and
  the AC app from one `VERSION` constant. v0.4.2 still identified itself as 0.4.1.
- Refresh the roster immediately when someone in your channel is moved by
  another user, times out, is kicked or banned, or is renamed. Uses the same
  filtering and coalescing as ordinary moves; previously these waited up to 1 s.
- Retry the UDP bind every three seconds when port 9999 is busy, instead of
  staying unavailable until AC restarts.
- Remove GridTalk's bundled `_socket` folder from AC's shared `sys.path` after
  import, and rate-limit receive-error logging to once per 10 seconds.
- Remove the unreachable legacy v1 talk diagnostic; v1 talk packets are rejected.
  The v1 reset emitted by the TS3 sender is still accepted.
- Document running the installer with a process-scoped execution-policy bypass.

## 0.4.2 — 2026-10-08

- Fix standalone AC startup by bundling Python 3.3 socket extensions for both
  architectures; avoid the IDNA dependency for numeric loopback binding. Add an
  import/startup/update check using AC's actual embedded Python 3.3.5 runtime.
- Include the socket modules and Python licence in staged, hash-verified installs.

## 0.4.1 — 2026-10-08

- Validate packets before source admission or freshness mutation; enforce IDs,
  page consistency, unique members, text/nesting/storage limits, and safe integers.
- Separate bridge activity from voice freshness; bound unknown query retention
  and reclaim silent sources while retaining sequence floors temporarily.
- Bound display rows/window width, clip long text, cache row order, and add an
  approximate receive time budget alongside the packet-count cap.
- Replace callback timers with an owned message window, serialize lifecycle and
  socket access, drain admitted callbacks on shutdown, and test actual DLL unload.
- Filter/coalesce relevant channel moves; add checked JSON formatting and UTF-8
  boundary handling and validate destination ports.
- Stage and hash-check installations; back up complete app assets, roll back failed
  upgrades/migration, and use unique backup paths.
- Add security/resource/freshness, installer failure, native boundary/lifecycle
  regressions and an updated performance capture. Live FPS/host checks remain pending.

## 0.4.0 — 2026-10-08

- Rename the project/AC overlay to GridTalk and the TS addon to Assetto Corsa Notifier.
- Isolate UDP roster and speaking state per sender source, allowing multiple providers.
- Publish a JSON Schema and integration guide for additional voice-app senders.
- Record an efficiency/security/bug assessment with reproducible receiver probes
  and a prioritized hardening plan; assessment findings remain open.

- Skip unchanged UI refreshes and update only the affected speaker's styling.
- Remove unused hidden dot controls and redundant texture loads.
- Add reproducible UDP/Python benchmarks and a performance assessment.

## 0.3.0 — 2026-10-07

- Windows x64 TeamSpeak 3.6.2 plugin using API 26 and local UDP.
- Current-channel roster with periodic snapshots and immediate speaking events.
- Correct local PTT events and self-state queries; missed-event recovery.
- Nonblocking Assetto Corsa Python receiver with bounded work per frame.
- Compact automatically sized roster, shaded red/green lights, translucent
  rounded pills, and white names that become bold while speaking.
- Connection diagnostics, backup-based installer, and native/receiver regression tests.

First Git release of the working implementation developed in this folder.
