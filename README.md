# GridTalk

GridTalk is a local UDP voice-activity overlay for Assetto Corsa, version 0.3.0.
The provider-neutral Python app receives channel rosters and speaking events
from voice-app senders on `127.0.0.1:9999`. Multiple sources can share the port;
their rosters, user IDs, resets, and timeouts are handled independently.

**Assetto Corsa Notifier** is the bundled Windows x64 TeamSpeak 3.6.2 sender
(plugin API 26), built as `assetto_corsa_notifier.dll`. It publishes everyone in
your active TeamSpeak tab's current channel. Other voice apps can integrate via
the [public sender protocol and JSON Schema](docs/SENDER_PROTOCOL.md); Discord
integration is not included yet.

The overlay shows fixed-position red/green speaking dots, displays names in white
with bold white for active speakers, and marks your own name. The shaded grey
app shows the whole roster with no paging buttons or packet counter. Each user
has a darker rounded pill and a larger shaded traffic light; active pills take
on a subtle green tint. Small cached textures provide the rounded ends and shaded
lights using AC's textured-quad renderer. Its height
fits the number of users; width is estimated from the displayed names with padding.
Names are TeamSpeak display names, not automatically mapped to AC cars.

## Install or upgrade

Close TeamSpeak and exit the AC driving session, then run `Install.ps1` from
PowerShell. It backs up the old files into this project's `backups` directory
and installs both the DLL and Python app. It moves the old `ts_ac_udp.dll` and
`TSVoice` app into the backup folder to prevent duplicate installations.
After upgrading, enable the newly named addon and app. Its default AC path matches the Steam
installation found on this machine. Override with `-AssettoCorsaPath` if needed.

For manual installation, replace BOTH files:

- `build/Release/assetto_corsa_notifier.dll` -> `%APPDATA%/TS3Client/plugins/assetto_corsa_notifier.dll`
- Copy the entire `ac_app/GridTalk` folder, including `assets`, into `<AC>/apps/python`.

Restart TeamSpeak and enable **Assetto Corsa Notifier** under Tools > Options > Addons.
Check that its version is **0.3.0**. Enable GridTalk in AC's Python app settings
and open it from the in-game apps bar. The DLL is x64; use the 64-bit TS client.

## Connection diagnosis

The app distinguishes these states:

- **Waiting for voice sender**: no recognized packet has arrived.
- The user list appears when connected, with no status line during normal use.
- **Voice sender disconnected**: no fresh connected source has a usable channel.
- **Voice connection lost**: no recognized packet for five seconds. Speaker highlights are cleared.
- **Legacy sender - update to protocol v2**: an old event-only sender is installed.
- **UDP port unavailable**: another receiver is using 9999.

With AC closed, `python tools/listen.py` prints live packets. Do not run it while
AC is listening, because only one receiver may bind port 9999. TeamSpeak logs
contain plugin startup, first roster snapshot, and the first UDP send failure.
AC's `Documents/Assetto Corsa/logs/py_log.txt` contains receiver startup/errors.

## Behavior and performance

See [the measured performance assessment](docs/PERFORMANCE.md) for benchmark
results, optimizations, and the remaining in-game FPS comparison.
The [code assessment](docs/CODE_REVIEW.md) records confirmed robustness bugs,
security boundaries, host-dependent risks, and prioritized improvements.

Speaking transitions arrive immediately through the TS callback. A one-second
roster refresh uses `getClientSelfVariableAsInt` for your own transmission state,
and local PTT transitions also use `onClientSelfVariableUpdateEvent`. Failed
state queries preserve the previous indicator instead of falsely clearing it.
A one-second
timer sends complete channel state, so an app started later receives the roster
and speaking state without needing someone to start talking. Snapshots also
repair missed UDP events and refresh joins, departures, moves, and name changes.
The timer runs on TeamSpeak's plugin-init thread message loop, not a worker thread.
There is no audio transmission or server polling.

The AC receiver is nonblocking. An idle frame makes one receive attempt, and a
busy frame handles at most 32 datagrams. State changes update the display promptly;
the connection status refreshes at most once per second while idle. UDP does not
guarantee delivery; lost snapshot pages are discarded and the next complete
snapshot replaces them. Update latency for roster changes is normally up to one
second; missed speaking events recover on the next snapshot. Only people in your
current channel are displayed, including whispers from those people. Activity
while the microphone is muted is ignored.

## Develop another sender

The [full protocol guide](docs/SENDER_PROTOCOL.md) documents every field,
transport limits, lifecycle, source isolation, page assembly, and recovery.
Use [the JSON Schema](protocol/gridtalk-v2.schema.json) and
[example packets](protocol/examples) to implement and validate a bridge for
another voice app. New senders should provide a stable `source` identity,
immediate `talk` events, and complete `state` snapshots about once a second.
Only users established by a snapshot appear in the overlay.

Development-only schema validation instructions are in the guide. The in-game
app requires no JSON Schema library or third-party Python packages.

## Build and test

Requires CMake, Visual Studio C++ tools, Python, and the official TeamSpeak SDK:

```powershell
git clone https://github.com/teamspeak/ts3client-pluginsdk.git sdk
git -C sdk checkout 4aa90a53aa150cbf81e13bc97e68c0431b26499f
cmake -S . -B build -A x64 -DGRIDTALK_BUILD_TESTS=ON
cmake --build build --config Release
python tests/check.py
```

Tests require port 9999 free. They exercise native speaking callbacks and a real
Windows timer/message loop with stub SDK functions, real loopback UDP, roster
pagination, reordered snapshot pages, JSON escaping, channel filtering, independent
simultaneous senders (including overlapping IDs and separate resets/timeouts), recovery
from missed stop events, stale status, and the per-frame packet limit. They mock
the AC UI; actual in-game display and TS runtime still require live validation.

PTT regression tests cover holding the local microphone active across a timer
snapshot and rapid local release/press events. To test while AC uses port 9999:

```powershell
cmake -S . -B build-tests -A x64 -DGRIDTALK_BUILD_TESTS=ON -DGRIDTALK_UDP_PORT=19999
cmake --build build-tests --config Release
python tests/check.py --port 19999 --build-dir build-tests
```

Install only the production DLL from `build/Release`, which targets port 9999.

The build instructions pin the SDK revision used for v0.3.0. Build outputs,
the downloaded SDK, and local installation backups are excluded from Git.
UI textures are tracked; regenerating them with `tools/make_ui_assets.py`
requires Pillow, which is not needed by the in-game app.

Change the destination using `-DGRIDTALK_UDP_PORT=9999` and the Python `PORT` constant
(also update the diagnostic listener). The fetched SDK is ignored by git; its
upstream licensing terms apply to its headers.
