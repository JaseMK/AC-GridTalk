"""Content Manager options: title bar, background opacity, and the speaking hold."""
import importlib.util
import socket  # noqa: F401  (must precede GridTalk: its bundled 3.3 _socket cannot load)
import sys
import re
import tempfile
import types
from pathlib import Path

root = Path(__file__).resolve().parents[1]
calls = []
ac = types.ModuleType("ac")
controls = [0]


def control(*args):
    controls[0] += 1
    return controls[0]


for name in ("newApp", "addLabel", "newTexture"):
    setattr(ac, name, control)
for name in ("setPosition", "setFontSize", "initFont", "setCustomFont", "setBackgroundTexture", "drawBorder",
             "setIconPosition", "setBackgroundColor", "addRenderCallback", "glQuadTextured", "glColor4f",
             "setFontColor", "setVisible", "setText", "log"):
    setattr(ac, name, lambda *args: None)
ac.setTitle = lambda app, title: calls.append(("title", title))
ac.setBackgroundOpacity = lambda target, value: calls.append(("opacity", value)) if target == app._app else None
sizes = {}
ac.setSize = lambda target, *size: sizes.update({target: size})
sys.modules["ac"] = ac
spec = importlib.util.spec_from_file_location("GridTalk", root / "ac_app/GridTalk/GridTalk.py")
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)
app.PORT = 0
config = Path(tempfile.mkdtemp())
app._CONFIG_DIR = str(config)


def start(defaults="", user=None):
    (config / "GridTalk_defaults.ini").write_text(defaults, encoding="utf-8")
    user_file = config / "GridTalk.ini"
    if user is None:
        user_file.unlink() if user_file.exists() else None
    else:
        user_file.write_text(user, encoding="utf-8")
    del calls[:]
    app.acMain("test")
    return dict(calls)


# Shipped defaults file is valid and keeps today's behaviour.
shipped = (root / "ac_app/GridTalk/config/GridTalk_defaults.ini").read_text(encoding="utf-8")
assert app._read_ini(str(root / "ac_app/GridTalk/config/GridTalk_defaults.ini")).keys() == \
    {"SHOW_TITLE_BAR", "BACKGROUND_OPACITY", "SPEAKING_HOLD_SECONDS", "UI_SCALE"}
started = start(shipped)
assert "title" not in started and started["opacity"] == 0.40
assert app._settings["speaking_hold_seconds"] == 0

# User values override defaults; bad and out-of-range values fall back or clamp.
started = start(shipped, "[OPTIONS]\nSHOW_TITLE_BAR=false\nBACKGROUND_OPACITY=0\nSPEAKING_HOLD_SECONDS=2.5 ; x\n")
assert started == {"title": "", "opacity": 0.0}
assert app._settings["speaking_hold_seconds"] == 2.5 and app._top_margin() < 32
start(shipped, "BACKGROUND_OPACITY=7\nSPEAKING_HOLD_SECONDS=999\nSHOW_TITLE_BAR=maybe\n")
assert app._settings == dict(show_title_bar=True, background_opacity=1.0, speaking_hold_seconds=10.0, ui_scale=1.0)
start(shipped, "BACKGROUND_OPACITY=nan\nSPEAKING_HOLD_SECONDS=-3\n")
assert app._settings["background_opacity"] == 0.40 and app._settings["speaking_hold_seconds"] == 0.0
start("", None)  # no files at all
assert app._settings == app._DEFAULT_SETTINGS

# Speaking hold: green for `hold` seconds after the speaker stops.
start(shipped, "SPEAKING_HOLD_SECONDS=2\n")
app._sources.clear()
app._members = {("t", 1): dict(client_id=1, name="A", talking=True, self=False, source="t"),
                ("t", 2): dict(client_id=2, name="B", talking=False, self=False, source="t")}
app._connected, app._last_packet = True, 100.0
app._dirty = True


def lamps(now):
    app._render(now)
    app._dirty = False
    return [shape[-1] == app._textures["green"] for shape in app._shapes]


assert lamps(100.0) == [True, False]
app._members[("t", 1)]["talking"] = False
app._dirty = True
app._last_packet = 101.0
assert lamps(101.0) == [True, False]   # still lit just after releasing
assert app._hold_deadline == 103.0
app._last_packet = 102.5
assert lamps(102.5) == [True, False]
app._last_packet = 103.1
app._dirty = True  # acUpdate does this once the deadline passes
assert lamps(103.1) == [False, False]  # hold elapsed
assert app._hold_deadline is None
# Talking again cancels the pending hold; stale connection never holds.
app._members[("t", 1)]["talking"] = True
app._dirty = True
assert lamps(103.2) == [True, False]
app._members[("t", 1)]["talking"] = False
app._dirty = True
app._last_packet = 103.3
assert lamps(103.3) == [True, False]
app._dirty = True
assert lamps(110.0) == [False, False]  # stale ("voice connection lost") clears holds
# acUpdate re-renders by itself when a hold expires.
start(shipped, "SPEAKING_HOLD_SECONDS=1\n")
app._socket = types.SimpleNamespace(recvfrom=lambda size: (_ for _ in ()).throw(OSError(10035, "none")))
app._time_now = [300.0]
app.time = types.SimpleNamespace(monotonic=lambda: app._time_now[0])
app._members = {("t", 1): dict(client_id=1, name="A", talking=True, self=False, source="t")}
app._connected, app._last_packet, app._dirty = True, 300.0, True
app._render(300.0)
app._members[("t", 1)]["talking"] = False
app._dirty = True
app._render(300.2)
assert app._shapes[0][-1] == app._textures["green"] and app._hold_deadline == 301.2
app._dirty = False
app._time_now[0] = 301.3
app._last_packet = 301.3
app.acUpdate(0.016)
assert app._shapes[0][-1] == app._textures["red"]
# With the option off nothing is held.
start(shipped)
app._members = {("t", 1): dict(client_id=1, name="A", talking=True, self=False, source="t")}
app._connected, app._last_packet, app._dirty = True, 200.0, True
assert lamps(200.0) == [True]
app._members[("t", 1)]["talking"] = False
app._dirty = True
assert lamps(200.1) == [False]
# AC resets the window opacity while it is dragged, so every frame re-applies it.
start(shipped, "BACKGROUND_OPACITY=0.1\n")
app._socket = types.SimpleNamespace(recvfrom=lambda size: (_ for _ in ()).throw(OSError(10035, "none")))
del calls[:]
app.acUpdate(0.016)
app.acUpdate(0.016)
assert calls == [("opacity", 0.1)] * 2, calls
# UI scale: every layout size follows it, and the setting is clamped.
def one_row(scale):
    start(shipped, "UI_SCALE=" + scale + "\n")
    app._members = {("t", 1): dict(client_id=1, name="A", talking=True, self=False, source="t")}
    app._connected, app._last_packet, app._dirty = True, 500.0, True
    app._render(500.0)
    return sizes[app._app][1], app._shapes[0][:4]


assert one_row("1") == (32 + 28 + 4, (11, 34, 20, 20))
assert one_row("2") == (32 + 56 + 8, (22, 32 + 4, 40, 40))
assert one_row("0.5") == (32 + 14 + 2, (6, 33, 10, 10))
assert app._settings["ui_scale"] == 0.5
start(shipped, "UI_SCALE=9\n")
assert app._settings["ui_scale"] == 2.0
start(shipped, "UI_SCALE=0\n")
assert app._settings["ui_scale"] == 0.5

# Aliases: names are logged for Content Manager and replaced by the alias the user types.
alias_file = config / "Aliases.ini"
if alias_file.exists():
    alias_file.unlink()
texts = {}
ac.setText = lambda label, text: texts.__setitem__(label, text)
start(shipped)
odd = "Zed (x); odd 日本"
app._members = {("t", 1): dict(client_id=1, name="BigCrow88", talking=False, self=False, source="t"),
                ("t", 2): dict(client_id=2, name=odd, talking=False, self=False, source="t"),
                ("t", 3): dict(client_id=3, name="", talking=False, self=False, source="t")}
app._connected, app._last_packet, app._dirty = True, 600.0, True
app._render(600.0)
text = alias_file.read_text(encoding="utf-8")
crow = app._alias_key("BigCrow88")
assert text.splitlines()[0] == "[ALIASES]" and len(text.splitlines()) == 3  # the unnamed client is not logged
assert crow + "= ; BigCrow88" in text
assert "; Zed [x], odd 日本" in text
assert all(re.match(r"^[\w -]+=", line) for line in text.splitlines()[1:])  # legal keys for Content Manager
assert [texts[row] for row in app._rows[:3]] == ["Client 3", "BigCrow88", odd]
# Names already logged don't touch the file again.
stamp = alias_file.stat().st_mtime_ns
app._dirty = True
app._render(600.5)
assert alias_file.stat().st_mtime_ns == stamp
# Editing the file while GridTalk runs: the alias is picked up at the next health check.
alias_file.write_text(text.replace(crow + "=", crow + "=Simon"), encoding="utf-8")
import os
os.utime(str(alias_file), (stamp / 1e9 + 5, stamp / 1e9 + 5))
clock = [601.0]
app.time = types.SimpleNamespace(monotonic=lambda: clock[0])
app._socket = types.SimpleNamespace(recvfrom=lambda size: (_ for _ in ()).throw(OSError(10035, "none")))
app._last_packet, app._last_health = 601.0, 0
app.acUpdate(0.016)
assert [texts[row] for row in app._rows[:3]] == ["Client 3", odd, "Simon"] or \
    [texts[row] for row in app._rows[:3]] == ["Client 3", "Simon", odd], [texts[row] for row in app._rows[:3]]
assert "Simon" in [texts[row] for row in app._rows[:3]] and "BigCrow88" not in [texts[row] for row in app._rows[:3]]
# A new name is logged without losing the user's alias; a blank alias reverts to the real name.
app._members[("t", 4)] = dict(client_id=4, name="Newcomer", talking=False, self=False, source="t")
app._dirty = True
app._render(602.0)
text = alias_file.read_text(encoding="utf-8")
assert crow + "=Simon ;" in text and app._alias_key("Newcomer") + "= ;" in text
# The log is capped; entries with an alias are never dropped.
app.MAX_ALIASES = 3
app._members[("t", 5)] = dict(client_id=5, name="Fifth", talking=False, self=False, source="t")
app._dirty = True
app._render(603.0)
text = alias_file.read_text(encoding="utf-8")
assert crow + "=Simon ;" in text and len(text.splitlines()) == 4 and app._alias_key("Fifth") in text
# An unwritable folder is survivable.
app._CONFIG_DIR = str(config / "missing")
app._members[("t", 6)] = dict(client_id=6, name="Sixth", talking=False, self=False, source="t")
app._dirty = True
app._render(604.0)
app._CONFIG_DIR = str(config)
print("PASS: options file defaults/overrides/clamping, title bar, opacity, speaking hold, UI scale, aliases")
