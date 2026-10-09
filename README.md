# GridTalk

An in-game overlay for Assetto Corsa that shows who is in your TeamSpeak
channel and who is talking right now.

- Everyone in your current TeamSpeak channel, with a red/green light that turns
  green while they transmit. Active speakers' names go bold; your own name is
  marked "(you)".
- Updates instantly when someone starts or stops talking, joins, leaves, or is
  renamed. If a network update is missed, it corrects itself within a second.
- The window sizes itself to the channel (up to 24 names) and stays small and
  translucent.

GridTalk has two parts that talk to each other over your own PC only
(`127.0.0.1`, UDP port 9999). Nothing is sent over the internet.

| Part | What it is |
| --- | --- |
| **Assetto Corsa Notifier** | TeamSpeak 3 plugin that reports your channel |
| **GridTalk** | Assetto Corsa Python app that draws the overlay |

## Requirements

- Windows, 64-bit **TeamSpeak 3** client (3.6.x; plugin API 26). TeamSpeak 5/6
  are not supported.
- **Assetto Corsa** (works with or without Content Manager).

## Install

Download both files from the [latest release](../../releases/latest):

1. **TeamSpeak plugin**: close TeamSpeak, then double-click
   `AssettoCorsaNotifier-<version>.ts3_plugin`. TeamSpeak's installer opens and
   warns that the package is unsigned; continue to install. Start TeamSpeak and
   check that **Assetto Corsa Notifier** is enabled under
   *Tools → Options → Addons*.
2. **Assetto Corsa app**: extract `GridTalk-<version>.zip` into your Assetto
   Corsa folder (for Steam, usually
   `C:\Program Files (x86)\Steam\steamapps\common\assettocorsa`). This creates
   `apps\python\GridTalk`. Content Manager users can drag the zip onto Content
   Manager instead.
3. **Enable the app**: tick **GridTalk** in the Python apps list (Content
   Manager: *Settings → Assetto Corsa → Python apps*; original launcher:
   *Options → General → UI Modules*). In a session, open GridTalk from the apps
   bar on the right of the screen.

To upgrade, repeat steps 1 and 2; the new files replace the old ones.

<details>
<summary>Installing the TeamSpeak plugin by hand</summary>

A `.ts3_plugin` file is a zip. Rename it to `.zip`, then copy
`plugins\assetto_corsa_notifier.dll` into `%APPDATA%\TS3Client\plugins` and
restart TeamSpeak.
</details>

**Uninstall:** remove the plugin in TeamSpeak's *Addons* page, and delete
`apps\python\GridTalk` from the Assetto Corsa folder.

## Troubleshooting

The overlay shows a status line when it isn't displaying a normal roster:

| Status | Meaning |
| --- | --- |
| Waiting for voice sender | Nothing received yet. Is TeamSpeak running with the plugin enabled? |
| Voice sender disconnected | TeamSpeak is running but not connected to a server. |
| Voice connection lost | No updates for five seconds (TeamSpeak closed or the plugin disabled). Speaking lights are cleared. |
| Channel is empty | Connected, but the voice app reported an empty channel. |
| UDP port unavailable: 9999 (retrying) | Another program is using port 9999. GridTalk keeps retrying every three seconds. |
| Showing 24 of N users | Large channel; only the first 24 names are shown. |

Logs:

- TeamSpeak: *Tools → Client Log* shows plugin startup and any send errors.
- Assetto Corsa: `Documents\Assetto Corsa\logs\py_log.txt` shows GridTalk
  startup and receive errors.
- With Assetto Corsa closed, `python tools/listen.py` prints the packets
  TeamSpeak is sending.

**Limitations:** only people in your current channel of the active TeamSpeak
tab are shown. Names are TeamSpeak names and aren't matched to cars. Speaking
while your microphone is muted is ignored.

## Other voice apps

The overlay doesn't depend on TeamSpeak. Any program can feed it by sending
small JSON messages to `127.0.0.1:9999`. The [sender protocol](docs/SENDER_PROTOCOL.md),
[JSON Schema](protocol/gridtalk-v2.schema.json) and
[example packets](protocol/examples) describe the format. A Discord bridge is
not included yet.

## Building from source

Requires Windows, CMake 3.20+, Visual Studio C++ build tools, Python 3, and the
TeamSpeak plugin SDK at the pinned revision:

```powershell
git clone https://github.com/teamspeak/ts3client-pluginsdk.git sdk
git -C sdk checkout 4aa90a53aa150cbf81e13bc97e68c0431b26499f
cmake -S . -B build -A x64
cmake --build build --config Release
```

This produces `build\Release\assetto_corsa_notifier.dll`. The AC app in
`ac_app\GridTalk` is plain Python and needs no build. The version number is set
once in `CMakeLists.txt` (`project(... VERSION ...)`) and must match `VERSION`
in `ac_app/GridTalk/GridTalk.py`.

### Tests

Tests use a separate build that sends to port 19999, so they can run while
Assetto Corsa is open:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
powershell -ExecutionPolicy Bypass -File tests\run_all.ps1
```

`run_all.ps1` builds `build-tests`, then runs the schema, native plugin and
overlay tests. If Assetto Corsa is installed, it also runs the overlay inside
the game's own embedded Python 3.3 (`-SkipAcRuntime` skips this step). GitHub
Actions runs the same suite on every push.

### Releasing

1. Update the version in `CMakeLists.txt` and `ac_app/GridTalk/GridTalk.py`,
   and add a `CHANGELOG.md` entry.
2. Commit, then tag and push: `git tag v0.4.3` and `git push origin main v0.4.3`.
3. The release workflow builds the DLL, runs `tools/package.py`, and creates a
   **draft** GitHub release with both downloads attached. Review it on GitHub
   and click *Publish*.

To build the downloads locally instead, run `python tools/package.py` after
the production build. It writes them to `dist\` and refuses a DLL built for
the test port or a different version.

## Repository layout

| Path | Contents |
| --- | --- |
| `src/plugin.c` | TeamSpeak plugin |
| `ac_app/GridTalk/` | Assetto Corsa app (copied as-is into `apps/python`) |
| `protocol/`, `docs/SENDER_PROTOCOL.md` | Wire protocol for other senders |
| `tests/` | Native, overlay, schema and AC-runtime tests |
| `tools/` | Packaging, packet listener, benchmark, UI texture generator |

## Licence

GridTalk is released under the [MIT licence](LICENSE). The bundled `_socket`
modules in `ac_app/GridTalk/lib` are from CPython 3.3 and are covered by the
[Python licence](ac_app/GridTalk/lib/PYTHON-LICENSE.txt).
The TeamSpeak SDK is fetched separately and is subject to its own terms.
