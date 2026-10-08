import importlib.util
import json
import socket
import subprocess
import sys
import time
import types
import argparse
from pathlib import Path

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int, default=9999)
parser.add_argument("--build-dir", default="build")
parser.add_argument("--schema", action="store_true", help="Validate native packets against the public JSON Schema")
options = parser.parse_args()
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(("127.0.0.1", options.port))
sock.settimeout(2)
subprocess.run([str(root / options.build_dir / "Release/test_sender.exe")], check=True)
events = []
while True:
    event = json.loads(sock.recv(65535).decode("utf-8"))
    events.append(event)
    if len(events) > 1 and event["event"] == "reset":
        break
sock.close()
if options.schema:
    from check_schema import packet_validator
    validator = packet_validator()
    for event in events:
        validator.validate(event)
    print("Native DLL packets match the published JSON Schema.")
states = [e for e in events if e["event"] == "state"]
talks = [e for e in events if e["event"] == "talk"]
assert len(talks) == 6  # Includes local PTT self-variable events
assert [t["talking"] for t in talks[2:]] == [True, False, True, False]
assert talks[0]["name"] == 'Driver 42 "A" \\ test\n'
assert talks[0]["talking"] is True and talks[1]["talking"] is False
assert talks[0]["self"] is True and talks[0]["whisper"] is True
assert talks[0]["server"] == "123" and talks[0]["channel"] == "7"
assert [len(e["clients"]) for e in states[:3]] == [8, 8, 1]
assert all(e["channel_name"] == 'Race "chat"' for e in states[:3])
assert len(states[3]["clients"]) == 2  # real 1-second timer refresh
assert states[3]["snapshot"] > states[0]["snapshot"]
assert states[3]["clients"][0]["talking"] is True  # held across timer refresh
assert states[4]["clients"][0]["talking"] is False
assert states[4]["clients"][1]["talking"] is None  # failed query is unknown
assert states[5]["connected"] is False
assert all(e.get("source") == "teamspeak" for e in events)

labels, colors, logs, sizes, visibility = {}, {}, [], {}, {}
ac = types.ModuleType("ac")
def control(*args):
    control.count += 1
    return control.count
control.count = 0
for name in ("newApp", "addLabel", "addButton", "newTexture"):
    setattr(ac, name, control)
for name in ("setPosition", "setFontSize", "initFont", "setCustomFont", "setBackgroundTexture", "addOnClickedListener", "drawBorder", "setBackgroundOpacity", "setBackgroundColor", "addRenderCallback", "glQuadTextured", "glColor4f"):
    setattr(ac, name, lambda *args: None)
ac.log = logs.append
ac.setText = lambda label, text: labels.update({label: text})
ac.setFontColor = lambda label, *color: colors.update({label: color})
ac.setSize = lambda label, *size: sizes.update({label: size})
ac.setVisible = lambda label, visible: visibility.update({label: visible})
sys.modules["ac"] = ac
sys.modules["acsys"] = types.SimpleNamespace(GL=types.SimpleNamespace(Triangles=4))
spec = importlib.util.spec_from_file_location("GridTalk", root / "ac_app/GridTalk/GridTalk.py")
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)
app.PORT = options.port
app.acMain("test")
ui_calls = []
def track_ui(name, original):
    def call(*args):
        ui_calls.append(name)
        return original(*args)
    return call
for name in ("setText", "setVisible", "setPosition", "setSize", "setFontColor",
             "setCustomFont", "setFontSize", "setBackgroundTexture", "setBackgroundOpacity"):
    setattr(ac, name, track_ui(name, getattr(ac, name)))
sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
def send(event):
    sender.sendto(json.dumps(event).encode("utf-8"), ("127.0.0.1", options.port))
def tick():
    app.acUpdate(0.016)

sender.sendto(b"invalid JSON", ("127.0.0.1", options.port))
# Out-of-order pages are committed only once complete.
send(states[2]); send(states[0]); tick()
assert not app._members
send(states[1]); tick()
assert len(app._members) == 17
assert len(app._rows) == 17
assert all(visibility[row] for row in app._rows)
large_height = sizes[app._app][1]
assert labels[app._status] == ""
assert app._members[("teamspeak", 43)]["talking"] is True
ui_calls.clear()
send(talks[0]); tick()
assert len(ui_calls) == 5  # only the changed speaker's font and three pill slices
assert app._members[("teamspeak", 42)]["talking"] is True
held_name = labels[app._rows[0]]
assert app._shapes[0][-1] == app._textures["green"]
assert app._shapes
assert len(app._shapes) == len(app._members)  # callback draws lamps only
assert all(background < app._rows[0] for background in app._pills[0])
assert all(shape[0] + shape[2] <= 36 for shape in app._shapes)  # never overlays names
app._draw_backgrounds(0.016)
assert colors[app._rows[0]] == (1.0, 1.0, 1.0, 1.0)
# Holding PTT stays green across a periodic roster refresh.
send(talks[2]); tick()
send(states[3]); tick()
assert app._members[("teamspeak", 42)]["talking"] is True
assert labels[app._rows[0]] == held_name
# Every rapid release/press is reflected on the next AC frame.
for event in talks[3:]:
    send(event); tick()
    assert app._members[("teamspeak", 42)]["talking"] is event["talking"]
    assert app._font_weights[app._rows[0]] == int(event["talking"])
    assert labels[app._rows[0]] == held_name  # speaking never changes name text
    assert app._shapes[0][-1] == app._textures["green" if event["talking"] else "red"]
# Deliberately omit a stop event: next snapshot must recover the state.
send(talks[2]); tick()
send(states[4]); tick()
assert len(app._members) == 2 and app._members[("teamspeak", 42)]["talking"] is False
assert app._members[("teamspeak", 43)]["talking"] is True  # failed query preserves prior state
assert sizes[app._app][1] < large_height
assert sum(visibility[row] for row in app._rows) == 2
send(states[0]); tick()  # obsolete snapshot cannot replace current roster
assert len(app._members) == 2
before = dict(labels)
ui_calls.clear()
app._last_health = 0  # force the periodic health check, not just an idle fast path
tick()
assert labels == before  # idle frame does not alter UI
assert not ui_calls  # unchanged health refresh never calls the native UI
for _ in range(40):
    send(talks[0])
tick()
assert app._socket.recvfrom(65535)[0]  # processing cap leaves queued packets
tick()
app._refresh_sources(app._last_packet + 6)
app._render(app._last_packet + 6)
assert "connection lost" in labels[app._status]
assert app._shapes[0][-1] == app._textures["red"]
send(states[5]); tick()
assert not app._members and not app._connected
assert "disconnected" in labels[app._status]

# Independent senders can use the same numeric IDs and snapshot numbers.
discord = dict(states[3], source="discord", snapshot=1)
send(discord); tick()
assert ("discord", 42) in app._members
assert ("teamspeak", 42) not in app._members
ts = dict(states[3], snapshot=states[5]["snapshot"] + 1)
send(ts); tick()
assert len(app._members) == 4
discord_stop = dict(talks[1], source="discord")
send(discord_stop); tick()
assert not app._members[("discord", 42)]["talking"]
assert app._members[("teamspeak", 42)]["talking"]
send({"v": 2, "source": "discord", "event": "reset"}); tick()
assert len(app._members) == 2 and ("teamspeak", 42) in app._members
send(discord); tick()
now = time.monotonic()
app._sources["teamspeak"].last_packet = now - 6
app._sources["discord"].last_packet = now
tick()
assert not app._members[("teamspeak", 42)]["talking"]
assert app._members[("discord", 42)]["talking"]
assert app._connected
app.acShutdown()
sender.close()
print("PASS: native speaking callbacks, real timer snapshots, channel filter, JSON escaping")
print("PASS: AC roster, reordered network pages, auto-sizing, missed-stop recovery, stale status, 32-packet cap")
print("PASS: held local PTT across snapshot, rapid PTT transitions, failed-query state preservation")
print("PASS: separate red/green speaking dots and stable name text")
print("PASS: independent sender IDs, snapshots, reset, speaking state, and timeout")
