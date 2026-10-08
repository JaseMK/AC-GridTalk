# Changelog

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
