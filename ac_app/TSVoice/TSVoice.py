"""TSVoice 0.3.0: current TeamSpeak channel roster and speaking indicators."""
import errno
import json
import socket
import time
import ac

PORT = 9999
MAX_PACKETS_PER_FRAME = 32
STALE_SECONDS = 5.0
_socket = None
_app = None
_status = None
_rows = []
_dots = []
_members = {}
_server = None
_channel = None
_channel_name = ""
_connected = False
_last_packet = None
_packets = 0
_last_health = 0.0
_pending = None
_committed = -1
_dirty = True
_bridge_message = ""
_shapes = []
_textures = {}
_font_weights = {}
_pills = []
_pill_states = {}


def acMain(ac_version):
    global _socket, _app, _status, _rows, _dots, _pills
    _app = ac.newApp("TSVoice")
    ac.initFont(0, "Arial", 0, 0)
    ac.initFont(0, "Arial", 0, 1)
    ac.setSize(_app, 220, 58)
    ac.drawBorder(_app, 0)
    ac.setBackgroundColor(_app, 0.22, 0.22, 0.22)
    ac.setBackgroundOpacity(_app, 0.40)
    for name in ("idle_left", "idle_middle", "idle_right", "active_left", "active_middle", "active_right", "red", "green"):
        _textures[name] = ac.newTexture("apps/python/TSVoice/assets/" + name + ".png")
    ac.addRenderCallback(_app, _draw_backgrounds)
    _status = ac.addLabel(_app, "Waiting for TeamSpeak")
    ac.setPosition(_status, 10, 32)
    ac.setFontSize(_status, 12)
    _rows = []
    _dots = []
    _pills = []
    candidate = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        candidate.bind(("127.0.0.1", PORT))
        candidate.setblocking(False)
        _socket = candidate
        ac.log("TSVoice 0.3.0 listening on 127.0.0.1:" + str(PORT))
    except OSError as exc:
        candidate.close()
        ac.setText(_status, "UDP port unavailable: " + str(PORT))
        ac.log("TSVoice bind failed: " + str(exc))
    return "TSVoice"


def _accept(event, now):
    global _members, _server, _channel, _channel_name, _connected
    global _pending, _committed, _last_packet, _packets, _dirty, _bridge_message
    if not isinstance(event, dict) or event.get("v") not in (1, 2):
        return
    kind = event.get("event")
    if kind not in ("state", "talk", "reset", "bridge_status"):
        return
    _last_packet = now
    _packets += 1
    if kind == "bridge_status":
        message = event.get("message")
        _bridge_message = message if isinstance(message, str) else "Plugin query error"
        _dirty = True
    elif kind == "reset":
        if event.get("server") is None or event.get("server") == _server:
            _members = {}
            _connected = False
            _pending = None
            _committed = -1
            _dirty = True
    elif kind == "talk":
        if event.get("v") == 1:
            _bridge_message = "Old DLL detected - install plugin v0.3.0"
            _dirty = True
            return
        client = event.get("client_id")
        talking = event.get("talking")
        if not isinstance(client, int) or not isinstance(talking, bool):
            return
        if _pending is not None and event.get("server") == _pending["server"] and event.get("channel") == _pending["channel"]:
            _pending["talks"][client] = talking
        if event.get("server") != _server or event.get("channel") != _channel:
            return
        if client in _members and _members[client]["talking"] != talking:
            _members[client]["talking"] = talking
            _dirty = True
    elif kind == "state":
        sequence, page, pages = event.get("snapshot"), event.get("page"), event.get("pages")
        clients = event.get("clients")
        if (not isinstance(sequence, int) or not isinstance(page, int) or
                not isinstance(pages, int) or not 1 <= pages <= 8192 or
                not 0 <= page < pages or not isinstance(clients, list) or len(clients) > 8):
            return
        connected = event.get("connected")
        if not isinstance(connected, bool):
            return
        server, channel = event.get("server"), event.get("channel")
        channel_name = event.get("channel_name", "")
        if connected and (not isinstance(server, str) or not isinstance(channel, str) or
                          not isinstance(channel_name, str)):
            return
        normalized = {}
        for member in clients:
            if (not isinstance(member, dict) or not isinstance(member.get("client_id"), int) or
                    not isinstance(member.get("name"), str) or
                    "talking" not in member or
                    (member.get("talking") is not None and not isinstance(member.get("talking"), bool)) or
                    not isinstance(member.get("self"), bool)):
                return
            normalized[member["client_id"]] = dict(member)
        if sequence <= _committed:
            return
        if _pending is None or sequence > _pending["id"]:
            _pending = {"id": sequence, "pages": pages, "parts": {}, "talks": {},
                        "server": server, "channel": channel, "name": channel_name,
                        "connected": connected, "time": now}
        if (sequence != _pending["id"] or pages != _pending["pages"] or
                server != _pending["server"] or channel != _pending["channel"] or
                connected != _pending["connected"]):
            return
        _pending["parts"][page] = normalized
        if len(_pending["parts"]) == pages:
            members = {}
            for index in range(pages):
                members.update(_pending["parts"][index])
            # A failed SDK query is unknown, not a stop event.
            for client, member in members.items():
                if member["talking"] is None:
                    previous = _members.get(client, {}) if server == _server and channel == _channel else {}
                    member["talking"] = previous.get("talking", False)
            for client, talking in _pending["talks"].items():
                if client in members:
                    members[client]["talking"] = talking
            _dirty = (_dirty or members != _members or server != _server or channel != _channel or
                      channel_name != _channel_name or connected != _connected)
            _members = members
            _server, _channel, _channel_name = server, channel, channel_name
            _connected = connected
            _committed = sequence
            _pending = None
            _bridge_message = ""


def _render(now):
    global _shapes
    stale = _last_packet is not None and now - _last_packet > STALE_SECONDS
    names = sorted(_members.values(), key=lambda member: (member["name"].lower(), member["client_id"]))
    if _last_packet is None:
        status = "Waiting for TeamSpeak"
    elif stale:
        status = "TeamSpeak connection lost"
    elif _bridge_message:
        status = _bridge_message
    elif not _connected:
        status = "TeamSpeak disconnected"
    else:
        status = "" if names else "Channel is empty"
    ac.setText(_status, status)
    ac.setVisible(_status, 1 if status else 0)
    while len(_rows) < len(names):
        # Native backgrounds are created BEFORE the text control. The custom
        # render callback runs over native labels, so it must not draw pills.
        backgrounds = [ac.addLabel(_app, "") for _ in range(3)]
        for background in backgrounds:
            ac.drawBorder(background, 0)
            # The PNG supplies the fill and alpha. An opaque control backing
            # fills its transparent corners with black.
            ac.setBackgroundOpacity(background, 0)
        _pills.append(backgrounds)
        dot = ac.addLabel(_app, "")
        label = ac.addLabel(_app, "")
        ac.setFontSize(dot, 14)
        ac.setFontSize(label, 14)
        _dots.append(dot)
        _rows.append(label)
    top = 54 if status else 32
    width = max(130, _text_width(status, 12) + 20)
    row_states = []
    for index, label in enumerate(_rows):
        visible = index < len(names)
        ac.setVisible(label, int(visible))
        for background in _pills[index]:
            ac.setVisible(background, int(visible))
        ac.setVisible(_dots[index], 0)
        if visible:
            member = names[index]
            speaking = member["talking"] and not stale
            name = member["name"] or "Client " + str(member["client_id"])
            name = " ".join(name.split())
            text = name + (" (you)" if member["self"] else "")
            # Reserve bold width even while quiet, so the window stays steady.
            width = max(width, int(_text_width(text, 14) * 1.12) + 52)
            ac.setPosition(label, 36, top + index * 28 + 3)
            row_states.append(speaking)
            dot_text = "\u25cf"
            dot_color = (0.3, 1.0, 0.4, 1.0) if speaking else (1.0, 0.25, 0.25, 1.0)
            color = (1.0, 1.0, 1.0, 1.0)
            weight = 1 if speaking else 0
            if _font_weights.get(label) != weight:
                ac.setCustomFont(label, "Arial", 0, weight)
                ac.setFontSize(label, 14)
                _font_weights[label] = weight
        else:
            text, color = "", (0.85, 0.85, 0.85, 1.0)
            dot_text, dot_color = "", (1.0, 0.25, 0.25, 1.0)
        ac.setText(_dots[index], dot_text)
        ac.setFontColor(_dots[index], *dot_color)
        ac.setText(label, text)
        ac.setFontColor(label, *color)
    ac.setSize(_app, width, top + len(names) * 28 + 4)
    ac.setBackgroundOpacity(_app, 0.40)
    shapes = []
    for index, speaking in enumerate(row_states):
        y = top + index * 28
        state = "active" if speaking else "idle"
        for control, part, x, w in zip(_pills[index], ("left", "middle", "right"),
                                       (6, 18, width - 18), (12, width - 36, 12)):
            ac.setPosition(control, x, y)
            ac.setSize(control, w, 24)
            if _pill_states.get(control) != state:
                ac.setBackgroundTexture(control, "apps/python/TSVoice/assets/" + state + "_" + part + ".png")
                _pill_states[control] = state
        shapes.append((11, y + 2, 20, 20, _textures["green" if speaking else "red"]))
    _shapes = shapes


def _draw_backgrounds(delta_t):
    ac.glColor4f(1, 1, 1, 1)
    for shape in _shapes:
        ac.glQuadTextured(*shape)


def _text_width(text, font_size):
    # AC exposes no text measurement here. Estimate proportional glyph widths
    # with padding; reserve a full em for wide Unicode names.
    units = 0.0
    for char in text:
        if char in " ilI.,'!:;|":
            units += 0.35
        elif char in "MW@#%" or ord(char) > 255:
            units += 1.0
        else:
            units += 0.65
    return int(units * font_size + 8)


def acUpdate(delta_t):
    global _dirty, _last_health, _pending
    if _socket is None:
        return
    now = time.monotonic()
    for _ in range(MAX_PACKETS_PER_FRAME):
        try:
            data, address = _socket.recvfrom(65535)
        except OSError as exc:
            if exc.errno not in (errno.EAGAIN, errno.EWOULDBLOCK, 10035):
                ac.log("TSVoice UDP error: " + str(exc))
            break
        try:
            _accept(json.loads(data.decode("utf-8")), now)
        except (ValueError, UnicodeError):
            continue
    if _pending is not None and now - _pending["time"] > STALE_SECONDS:
        _pending = None
    if _dirty or now - _last_health >= 1.0:
        _render(now)
        _dirty = False
        _last_health = now


def acShutdown():
    global _socket
    if _socket is not None:
        _socket.close()
        _socket = None
    _members.clear()
