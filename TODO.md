# Prioritised TODO

Updated 8 October 2026. P1 = validate performance and reliability; P2 = polish
and investigate compatibility; P3 = deliver supported expansion and releases.
The plan below records future work, not permission to start every item now.

## Current position

- Current application version is v0.4.0; v0.3.0 remains the existing Git release tag.
- Offline Python/UDP benchmarks and initial UI optimisations are complete;
  see [the performance assessment](docs/PERFORMANCE.md).
- Offline performance changes are complete. GridTalk branding, independent UDP
  sources, and the public sender schema/guide are implemented and installed.
- The offline code review is complete; see [findings and evidence](docs/CODE_REVIEW.md).
  Its fixes and host-dependent validation are pending. No in-game FPS A/B
  comparison has been completed.

## P1 — Performance and reliability

- [ ] **Run controlled in-game FPS A/B testing.** Use the same warmed-up replay
  or benchmark and graphics settings. Compare: both components disabled;
  TS plugin enabled with AC app disabled; both enabled with the overlay visible.
  Alternate conditions and record at least three 60-second runs per condition.
  Measure average FPS, 1% lows, frame-time distribution, and AC/TS CPU usage.
  Include quiet operation and frequent PTT transitions. Document run-to-run noise
  before deciding whether there is a repeatable regression. Hiding the overlay
  alone does not establish a disabled baseline.
- [x] **Assess code quality, efficiency, security, and bugs.** Cover the DLL,
  Python app, protocol, tests, and installer. Review error handling, timer and
  socket lifecycle, SDK memory ownership, UTF-8 handling, thread assumptions,
  snapshot/event ordering, repeated allocations, and native UI/rendering work.
  Findings, severity, evidence, and implementation order are recorded in
  [the review](docs/CODE_REVIEW.md).
- [ ] **Fix receiver admission and resource limits (R1–R5).** Bound roster size,
  strings, retained bytes, native controls, and JSON nesting. Validate before
  allocating source state or refreshing timestamps. Enforce coherent pages,
  exact integer IDs, and unique members; reclaim expired source slots safely.
- [ ] **Correct voice-state freshness (R3/R6).** Separate bridge liveness from
  roster/speaking freshness. Require trustworthy data after timeout and bound
  retention of unknown speaking values; test transient and sustained failures.
- [ ] **Harden native lifecycle and roster refreshes (R8–R10).** Establish actual
  TS callback thread/unload behavior, filter/coalesce unrelated move events,
  and use checked capacity-aware formatting. Preserve immediate PTT behavior.
- [ ] **Validate real-session reliability and resource use.** Exercise held and
  rapid PTT, simultaneous speakers, joins/leaves, channel/tab changes, reconnects,
  client/app restart order, and port conflicts. Run a long session and observe
  CPU, memory, handles, and retained row controls. Verify recovery from delayed,
  lost, reordered, incomplete, or malformed packets. Use synthetic traffic for
  larger-roster stress cases and label those results separately from live tests.
- [ ] **Review limits for large rosters and packet bursts.** The packet-count cap
  is not a wall-clock budget. Check snapshot assembly and layout spikes, window
  height, and rendering cost as channel size grows. Add limits or deferred work
  only where measurements justify them.

Completion evidence: repeatable in-game results, documented review findings,
passing regression checks, and no unexplained FPS/CPU/memory regressions.

## P2 — Presentation and compatibility research

- [x] **Choose and apply the names.** Project and AC app: **GridTalk**;
  TeamSpeak addon: **Assetto Corsa Notifier**. Validate the renamed installation
  and addon entry in a live session before release.
- [ ] **Improve the TeamSpeak plugins/addons page.** Provide a clear purpose,
  author and version, supported client/API/architecture, setup instructions,
  local UDP behavior, and troubleshooting. Verify the actual addon page rather
  than relying solely on exported metadata or startup logs.
- [ ] **Remove the Assetto Corsa logo from the Python app title bar.** Preserve
  the compact layout and practical window positioning/dragging behavior; check
  the result in-game.
- [ ] **Investigate TeamSpeak 5 and TeamSpeak 6 compatibility.** Check current
  official extension/plugin capabilities and available local integration APIs.
  Record supported versions, event/roster access, architecture, and distribution
  constraints. Decide whether the current DLL can be adapted or a separate
  bridge is required. Do not promise support before feasibility is established.

Completion evidence: selected name, readable addon metadata, verified title-bar
change, and a sourced compatibility assessment with a concrete recommendation.

## P3 — Packaging and expansion

- [x] **Publish the provider-neutral UDP contract.** JSON Schema, examples,
  full sender guide, and separate source state are in the repo.
- [ ] **Investigate a Discord sender.** Establish supported roster/transmission
  APIs and permissions, then implement a separate bridge if feasible. Test it
  alongside TeamSpeak, including overlapping IDs, restarts, and independent
  timeouts. The existing packet examples do not constitute Discord support.

- [ ] **Create and validate TS5/TS6 integrations if feasible.** Follow the
  compatibility assessment, preserve the AC receiver protocol where practical,
  and test actual speaking events, roster changes, reconnects, and performance
  in each supported client. Clearly document unsupported cases.
- [ ] **Prepare a reproducible installation/release package.** Bundle the DLL,
  complete Python app and textures, installation instructions, and dependency
  notices. Validate installation, upgrade, backup, and removal on a clean setup
  without requiring users to compile. Add complete asset backups, staging,
  rollback, unique backup paths, and failure-injection checks (R7).
  Test missing assets and plugin-disabled
  diagnostics. Keep the pinned SDK build reproducible.
- [ ] **Prepare the next release after hardening.** Review and
  commit the subsequent fixes, tests, and validation evidence.
  Update the changelog and version strings for a new release, rerun relevant
  checks, and create a new tag. Keep the existing v0.3.0 tag intact.

## Execution plan

1. **Establish the baseline.** Record the exact installed app/DLL revision and
   test settings, then run the P1 in-game comparison. Save raw captures and an
   interpreted results table. This provides a baseline for later changes.
2. **Audit and stabilise.** Complete the full review and session/stress checks.
   Prioritise measured regressions and correctness faults. Fix in small steps,
   rerun targeted tests, and repeat the in-game comparison when frame-sensitive
   code changes.
3. **Polish the current TS3 release.** Verify the chosen names, improve addon information,
   remove the AC logo, and check readability, transparency, resizing, and window
   usability in-game.
4. **Assess newer clients.** Research TS5/TS6 independently of the TS3 DLL and
   write a feasibility decision before implementation. Estimate scope from the
   supported APIs rather than assumed compatibility.
5. **Package and release the validated TS3 work.** Test the clean-install package,
   update documentation and changelog, commit the reviewed changes, and tag the
   next release. Newer-client support need not delay this release.
6. **Build supported newer-client bridges.** Implement feasible TS5/TS6 work as
   separate milestones, reuse the protocol where possible, and repeat functional,
   performance, and installation validation before claiming support.
