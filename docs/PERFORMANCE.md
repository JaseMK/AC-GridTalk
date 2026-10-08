# Performance assessment — 8 October 2026

## Conclusion

The v0.4.1 safety work is described in [HARDENING.md](HARDENING.md), with raw
results in `performance-hardened.json`. Earlier captures below are retained as
baselines. Validation adds packet-processing work; normal idle cost remains
around 0.003 ms, and native display resources are now capped.

Offline measurements show small Python costs for normal channel sizes. The main
avoidable cost was excessive calls into AC whenever speaking state changed;
this has been reduced. **Zero FPS impact has not been established.** AC and
TeamSpeak were not running during this assessment. Actual native UI, GPU, and
TeamSpeak SDK costs need an in-game comparison.

## Measurements

Measured on this PC with the bundled desktop Python runtime, using real Windows
loopback UDP and an ephemeral port. AC API calls were replaced by counting mocks.
Each case contains 3,000 samples with a simulated 120 Hz update clock. Timings
include receiver work and Python UI preparation; sending test packets is outside
the measured interval. These are microbenchmarks, not a real-time workload or
FPS test. AC's older embedded Python may perform differently.

Mean time, milliseconds per invocation:

| Users | Work | v0.3.0 | Optimized | Optimized p99 |
|---:|---|---:|---:|---:|
| 2 | Idle update | 0.00210 | 0.00198 | 0.00230 |
| 2 | Unchanged roster snapshot | 0.00743 | 0.00745 | 0.01400 |
| 2 | Speaking transition | 0.01623 | 0.00881 | 0.02010 |
| 2 | Burst of 32 talk packets | 0.09012 | 0.07952 | 0.15680 |
| 16 | Idle update | 0.00269 | 0.00196 | 0.00220 |
| 16 | Unchanged roster snapshot | 0.01971 | 0.01897 | 0.04680 |
| 16 | Speaking transition | 0.07596 | 0.01716 | 0.04080 |
| 16 | Burst of 32 talk packets | 0.14554 | 0.08728 | 0.16010 |
| 64 | Idle update | 0.00422 | 0.00208 | 0.00290 |
| 64 | Unchanged roster snapshot | 0.07139 | 0.06806 | 0.12510 |
| 64 | Speaking transition | 0.27246 | 0.04316 | 0.07460 |
| 64 | Burst of 32 talk packets | 0.34324 | 0.11459 | 0.18510 |

A snapshot normally arrives once per second, not every frame. The speaking case
toggles the first user on each invocation. The burst alternates start/stop and
finishes in the same state after the first sample; it primarily stresses packet
parsing and coalescing. System scheduling affects the tail timings.

The custom draw callback submits one lamp quad per visible user plus one color
call. With mocked rendering it takes roughly 0.0005 / 0.0025 / 0.0090 ms for
2 / 16 / 64 users. **Those values exclude the actual drawing cost.** Native AC
also renders three pill controls and a text label per visible user.

## Changes made

- Idle health checks and unchanged snapshots no longer repeat native UI setters.
- A single speaker transition makes five native setters (font, font size, three
  pill textures), down from about 41 / 265 / 1,033 for 2 / 16 / 64 users.
- Names, positions, sizes, and visibility update only when the roster/layout changes.
- Removed unused hidden dot labels and six redundant custom texture loads.
- Retained nonblocking I/O, the 32-packet frame limit, full-opacity white names,
  bold speaking text, existing draw order, transparency, and instant PTT events.

The 32-packet limit bounds packet count, not wall-clock time. Snapshot completion
and layout work grow with roster size. Very large channels also create a tall
overlay with more native controls and draw submissions. Hidden spare row controls
are reused, so their allocation follows the largest roster seen during a session.

## TeamSpeak-side review

The DLL has no busy loop or audio processing. A Windows timer reads cached channel
state once per second; each member requires a name and speaking-state query.
SDK-allocated strings and client lists are released. Talk events send one small
nonblocking UDP datagram. Channel-move callbacks can trigger extra snapshots.
This work is linear in roster size and runs on TeamSpeak's message thread, not
the AC frame thread. Its actual SDK/CPU cost was not measured in this offline run.

## GridTalk multi-source update

The renamed receiver now isolates each sender's roster and timeout, then merges
changed source state once per receive batch. A follow-up 3,000-sample run uses the
same mock/UDP methodology. For two users, mean idle update is 0.00236 ms,
speaking transition 0.01113 ms, and a 32-packet burst 0.09575 ms. For 64 users,
mean speaking transition is 0.05422 ms and a burst 0.13460 ms. Idle/unchanged
rosters still make no native UI setter calls, and a speaker transition still
makes five. Raw data is in `performance-gridtalk.json`.

These cases exercise one source; separate functional tests exercise multiple
sources. Simultaneous-source performance and actual native rendering still
need live assessment. Small differences between offline runs include scheduling
noise and the added source bookkeeping; they are not measured FPS changes.

## Validation and reproduction

The native/receiver tests pass, including held and rapid PTT, missing stop-event
recovery, reordering, roster resize, stale state, layer order, and packet limits.
Additional assertions confirm exactly five UI calls for a speaker transition and
no UI calls for an unchanged periodic refresh.

```powershell
python tools/benchmark.py --ref v0.3.0 --output build/performance-before.json
python tools/benchmark.py --output build/performance-after.json
python tests/check.py --port 19999 --build-dir build-tests
```

Raw outputs from this run are in `performance-before.json` and
`performance-after.json` beside this report. The separate test DLL uses port 19999;
the benchmark uses an ephemeral port, so neither consumes game port 9999.

## Remaining in-game check

1. Use the same replay or repeatable benchmark, camera, resolution, weather,
   opponents, and graphics settings. Warm up the scene before recording.
2. Measure at least three 60-second runs for each condition: both components
   disabled, TS sender only, and sender plus GridTalk enabled and visible.
   Restart the driving session after changing the enabled-app setting.
   Keep TeamSpeak connected in both cases. Avoid VSync/FPS limits masking changes
   when evaluating headroom; keep the chosen settings consistent across runs.
3. Compare frame-time distributions, average FPS, and 1% lows. Watch AC and
   TeamSpeak CPU usage too. Include quiet periods and repeated speaking changes.
4. Alternate disabled/enabled runs to reduce temperature and background-task bias.
   A difference smaller than ordinary run-to-run variation is not a demonstrated
   regression. A repeatable worsening merits profiling the native rendering path.

Simply hiding the window is not a complete disabled baseline: the Python update
callback may still run. This report does not claim an in-game A/B test was done.
