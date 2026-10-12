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

1. **The GridTalk app** draws the overlay in Assetto Corsa. Everyone needs it.
2. **A TeamSpeak connector** reports your channel to the overlay. Which one you
   need depends on your TeamSpeak version:

| Your TeamSpeak | Connector | Download |
| --- | --- | --- |
| **TeamSpeak 3** | *Assetto Corsa Notifier*, a TeamSpeak plugin | `AssettoCorsaNotifier-<version>.ts3_plugin` |
| **TeamSpeak 5 or 6** | *GridTalk TS6 Relay*, a small tray app | `GridTalk-TS6-Relay-<version>.exe` |

You only need one connector. **TeamSpeak 3 users don't need the TS6 Relay**,
and TeamSpeak 5 and 6 can't load TeamSpeak 3 plugins. Not sure which you have?
Look at the version number in TeamSpeak's *About* window or in Windows'
*Settings → Apps → Installed apps*: it starts with 3, 5 or 6.

## Requirements

- Windows.
- One of:
  - 64-bit **TeamSpeak 3** client (3.6.x; plugin API 26), or
  - **TeamSpeak 5 or 6**, with *Remote Apps* enabled in TeamSpeak's
    *Settings → Remote Apps* (it is on by default).
- **Assetto Corsa** (works with or without Content Manager).

## Install

From the [latest release](../../releases/latest), download `GridTalk-<version>.zip`
and the connector for your TeamSpeak (see the table above).

### 1. The Assetto Corsa app (everyone)

1. Extract `GridTalk-<version>.zip` into your Assetto Corsa folder (for Steam,
   usually `C:\Program Files (x86)\Steam\steamapps\common\assettocorsa`). This
   creates `apps\python\GridTalk`. Content Manager users can drag the zip onto
   Content Manager instead.
2. Tick **GridTalk** in the Python apps list (Content Manager:
   *Settings → Assetto Corsa → Python apps*; original launcher:
   *Options → General → UI Modules*). In a session, open GridTalk from the apps
   bar on the right of the screen.

### 2a. TeamSpeak 3: the plugin

Close TeamSpeak, then double-click `AssettoCorsaNotifier-<version>.ts3_plugin`.
TeamSpeak's installer opens and warns that the package is unsigned; continue to
install. Start TeamSpeak and check that **Assetto Corsa Notifier** is enabled
under *Tools → Options → Addons*. That's all: the plugin runs whenever
TeamSpeak does.

<details>
<summary>Installing the TeamSpeak 3 plugin by hand</summary>

A `.ts3_plugin` file is a zip. Rename it to `.zip`, then copy
`plugins\assetto_corsa_notifier.dll` into `%APPDATA%\TS3Client\plugins` and
restart TeamSpeak.
</details>

### 2b. TeamSpeak 5 or 6: the TS6 Relay

TeamSpeak 5 and 6 don't support plugins like TeamSpeak 3 did. Instead, the
GridTalk TS6 Relay is a small program (about 250 KB, a few MB of memory, no
installer) that reads your channel through TeamSpeak's *Remote Apps* feature
and passes it to the overlay.

1. Save `GridTalk-TS6-Relay-<version>.exe` somewhere permanent, for example
   `Documents\GridTalk`, and double-click it. Windows may warn that the file is
   unsigned; choose *More info → Run anyway*.
2. TeamSpeak asks whether to allow **GridTalk** as a Remote App. Allow it.
   You're only asked once.
3. The relay has no window. It sits in the notification area as a GridTalk
   icon (Windows may tuck it under the **^** arrow; drag it onto the taskbar to
   keep it visible). The icon's lamp turns green while your channel is being
   sent to the overlay and is grey otherwise; hover over it to see why.
4. Click the icon and tick **Start with Windows** so the relay runs
   automatically from now on. Otherwise, start it yourself before racing.

The relay and TeamSpeak can be started in either order: the relay waits for
TeamSpeak and reconnects if TeamSpeak restarts. To stop it, click the icon and
choose *Exit*.

### Upgrading

Download the new files and repeat step 1, then 2a or 2b; the new files replace
the old ones. For the TS6 Relay, choose *Exit* from its icon before replacing
the `.exe`. If the new file has a different name or location, tick
**Start with Windows** again on the new copy.

### Uninstall

- **Assetto Corsa app:** delete `apps\python\GridTalk` from the Assetto Corsa folder.
- **TeamSpeak 3 plugin:** remove it in TeamSpeak's *Addons* page.
- **TS6 Relay:** click its icon, untick *Start with Windows* and choose *Exit*.
  Delete the `.exe` and the `%APPDATA%\GridTalk` folder, and remove GridTalk
  under TeamSpeak's *Settings → Remote Apps*.

## Options

In Content Manager, open *Settings → Assetto Corsa → Python app settings →
GridTalk*. Changes apply the next time you start a session.

| Option | What it does |
| --- | --- |
| Show title | Turn off to hide the "GridTalk" title text. The bar still appears when you hover the window, so you can drag it. |
| Background opacity | 0% (fully transparent) to 100%. Only the grey window background changes; the name pills do not. Default 40%. |
| Speaking hold | Keeps someone green for up to 10 extra seconds after they stop talking, so a short word is not missed. Default 0 (off). |
| App scale | Resizes the overlay (names, lamps and spacing) from 50% to 200%. Default 100%. |

### Aliases

The *Aliases* page, shown after the main options, lists the people GridTalk
has seen, so it grows as you meet people (up to 200). Type a name like "Simon"
in someone's *Alias* box and the overlay shows that instead; leave it empty to
keep their real name. Changes are picked up within a second or two while a
session is running.

Aliases belong to a TeamSpeak display name, so if someone renames themselves
they need a new alias. Once the list is full the oldest names without an alias
are dropped.

Without Content Manager, edit `apps\python\GridTalk\config\GridTalk.ini`
(copy the lines from `GridTalk_defaults.ini`) or `GridTalk_Aliases.ini` in the same folder.

## Troubleshooting

The overlay shows a status line when it isn't displaying a normal roster:

| Status | Meaning |
| --- | --- |
| Waiting for voice sender | Nothing received yet. Is TeamSpeak running, with the plugin enabled (TeamSpeak 3) or the TS6 Relay running (TeamSpeak 5/6)? |
| Voice sender disconnected | TeamSpeak is running but not connected to a server. |
| Voice connection lost | No updates for five seconds (TeamSpeak closed, the plugin disabled, or the TS6 Relay exited). Speaking lights are cleared. |
| Channel is empty | Connected, but the voice app reported an empty channel. |
| UDP port unavailable: 9999 (retrying) | Another program is using port 9999. GridTalk keeps retrying every three seconds. |
| Showing 24 of N users | Large channel; only the first 24 names are shown. |

GridTalk TS6 Relay (TeamSpeak 5/6):

- **Allow GridTalk in TeamSpeak (Remote Apps)** in the overlay means the relay
  is waiting for you to approve it in TeamSpeak.
- Nothing at all in the overlay: check the relay's icon is in the notification
  area, and that *Remote Apps* is on in TeamSpeak's settings.
- The tray icon's lamp is green while the relay is sending your channel and
  grey otherwise; its tooltip says why. Its log is
  `%APPDATA%\GridTalk\relay.log`; it is rewritten each time the relay starts.
- If you declined the TeamSpeak prompt, or removed GridTalk from
  *Settings → Remote Apps*, the relay asks again after a while, waiting longer
  each time (up to five minutes). Restart it to be asked straight away.
- With several server tabs open, the relay follows the tab whose microphone is
  active.

Logs:

- TeamSpeak 3: *Tools → Client Log* shows plugin startup and any send errors.
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

This produces the TeamSpeak 3 plugin, `build\Release\assetto_corsa_notifier.dll`,
and the GridTalk TS6 Relay, `build\Release\GridTalk-TS6-Relay.exe`. The relay
needs no SDK; it uses TeamSpeak 5/6's Remote Apps WebSocket API
(`ws://localhost:5899`). Run it with `--console --dump` to log to the console,
including the structure (never the contents) of each kind of TeamSpeak
message. The AC app in
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

`run_all.ps1` builds `build-tests`, then runs the schema, native plugin,
relay and overlay tests. The relay test drives it with a fake TeamSpeak Remote
Apps server. If Assetto Corsa is installed, it also runs the overlay inside
the game's own embedded Python 3.3 (`-SkipAcRuntime` skips this step). GitHub
Actions runs the same suite on every push.

### Releasing

1. Update the version in `CMakeLists.txt` and `ac_app/GridTalk/GridTalk.py`,
   and add a `CHANGELOG.md` entry.
2. Commit, then tag and push: `git tag vX.Y.Z` and `git push origin main vX.Y.Z`.
3. The release workflow builds the DLL and relay, runs `tools/package.py`, and
   creates a **draft** GitHub release with the downloads attached. Review it on GitHub
   and click *Publish*.

To build the downloads locally instead, run `python tools/package.py` after
the production build. It writes them to `dist\` and refuses a DLL or relay
built for the test port or a different version.

## Repository layout

| Path | Contents |
| --- | --- |
| `src/plugin.c` | TeamSpeak 3 plugin (Assetto Corsa Notifier) |
| `relay/` | GridTalk TS6 Relay for TeamSpeak 5/6 (tray app) |
| `ac_app/GridTalk/` | Assetto Corsa app (copied as-is into `apps/python`) |
| `protocol/`, `docs/SENDER_PROTOCOL.md` | Wire protocol for other senders |
| `tests/` | Native, overlay, schema and AC-runtime tests |
| `tools/` | Packaging, packet listener, benchmark, UI texture and relay icon generators |

## Licence

GridTalk is released under the [MIT licence](LICENSE). The bundled `_socket`
modules in `ac_app/GridTalk/lib` are from CPython 3.3 and are covered by the
[Python licence](ac_app/GridTalk/lib/PYTHON-LICENSE.txt).
The TeamSpeak SDK is fetched separately and is subject to its own terms.
