"""GridTalk 0.3.0: UDP voice roster and speaking indicators."""
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
_members = {}
_sources = {}
_connected = False
_last_packet = None
_last_health = 0.0
_dirty = True
_bridge_message = ""
_shapes = []
_textures = {}
_font_weights = {}
_pills = []
_pill_states = {}
_layout_key = None
_visual_status = None
_layout_width = 130


def acMain(ac_version):
    global _socket, _app, _status, _rows, _pills, _layout_key, _visual_status
    _app = ac.newApp("GridTalk")
    ac.initFont(0, "Arial", 0, 0)
    ac.initFont(0, "Arial", 0, 1)
    ac.setSize(_app, 220, 58)
    ac.drawBorder(_app, 0)
    ac.setBackgroundColor(_app, 0.22, 0.22, 0.22)
    ac.setBackgroundOpacity(_app, 0.40)
    for name in ("red", "green"):
        _textures[name] = ac.newTexture("apps/python/GridTalk/assets/" + name + ".png")
    ac.addRenderCallback(_app, _draw_backgrounds)
    _status = ac.addLabel(_app, "Waiting for voice sender")
    ac.setPosition(_status, 10, 32)
    ac.setFontSize(_status, 12)
    _rows = []
    _pills = []
    _layout_key = None
    _visual_status = None
    _font_weights.clear()
    _pill_states.clear()
    candidate = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        candidate.bind(("127.0.0.1", PORT))
        candidate.setblocking(False)
        _socket = candidate
        ac.log("GridTalk 0.3.0 listening on 127.0.0.1:" + str(PORT))
    except OSError as exc:
        candidate.close()
        ac.setText(_status, "UDP port unavailable: " + str(PORT))
        ac.log("GridTalk bind failed: " + str(exc))
    return "GridTalk"


class _SourceState:
    def __init__(self):
        self.members = {}
        self.server = None
        self.channel = None
        self.channel_name = ""
        self.connected = False
        self.pending = None
        self.committed = -1
        self.last_packet = None
        self.packets = 0
        self.dirty = False
        self.bridge_message = ""
        self.stale = False

    def accept(self, event, now):
        if not isinstance(event, dict) or event.get("v") not in (1, 2):
            return
        kind = event.get("event")
        if kind not in ("state", "talk", "reset", "bridge_status"):
            return
        self.last_packet = now
        self.packets += 1
        if kind == "bridge_status":
            message = event.get("message")
            self.bridge_message = message if isinstance(message, str) else "Plugin query error"
            self.dirty = True
        elif kind == "reset":
            if event.get("server") is None or event.get("server") == self.server:
                self.members = {}
                self.connected = False
                self.pending = None
                self.committed = -1
                self.dirty = True
        elif kind == "talk":
            if event.get("v") == 1:
                self.bridge_message = "Legacy sender - update to protocol v2"
                self.dirty = True
                return
            client = event.get("client_id")
            talking = event.get("talking")
            if not isinstance(client, int) or not isinstance(talking, bool):
                return
            if self.pending is not None and event.get("server") == self.pending["server"] and event.get("channel") == self.pending["channel"]:
                self.pending["talks"][client] = talking
            if event.get("server") != self.server or event.get("channel") != self.channel:
                return
            if client in self.members and self.members[client]["talking"] != talking:
                self.members[client]["talking"] = talking
                self.dirty = True
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
            if sequence <= self.committed:
                return
            if self.pending is None or sequence > self.pending["id"]:
                self.pending = {"id": sequence, "pages": pages, "parts": {}, "talks": {},
                            "server": server, "channel": channel, "name": channel_name,
                            "connected": connected, "time": now}
            if (sequence != self.pending["id"] or pages != self.pending["pages"] or
                    server != self.pending["server"] or channel != self.pending["channel"] or
                    connected != self.pending["connected"]):
                return
            self.pending["parts"][page] = normalized
            if len(self.pending["parts"]) == pages:
                members = {}
                for index in range(pages):
                    members.update(self.pending["parts"][index])
                # A failed SDK query is unknown, not a stop event.
                for client, member in members.items():
                    if member["talking"] is None:
                        previous = self.members.get(client, {}) if server == self.server and channel == self.channel else {}
                        member["talking"] = previous.get("talking", False)
                for client, talking in self.pending["talks"].items():
                    if client in members:
                        members[client]["talking"] = talking
                self.dirty = (self.dirty or members != self.members or server != self.server or channel != self.channel or
                          channel_name != self.channel_name or connected != self.connected)
                self.members = members
                self.server, self.channel, self.channel_name = server, channel, channel_name
                self.connected = connected
                self.committed = sequence
                self.pending = None
                self.bridge_message = ""


def _accept(event, now, refresh=True):
    if not isinstance(event, dict) or event.get("v") not in (1, 2):
        return
    source = event.get("source", "default")
    if not isinstance(source, str) or not source or len(source) > 64:
        return
    if event.get("event") not in ("state", "talk", "reset", "bridge_status"):
        return
    if source not in _sources:
        if len(_sources) >= 16:
            return
        _sources[source] = _SourceState()
    state = _sources[source]
    state.accept(event, now)
    if refresh:
        _refresh_sources(now)


def _refresh_sources(now):
    global _members, _connected, _last_packet, _bridge_message, _dirty
    changed = False
    for state in _sources.values():
        stale = state.last_packet is not None and now - state.last_packet > STALE_SECONDS
        if state.stale != stale:
            state.stale = stale
            state.dirty = True
        if state.pending is not None and now - state.pending["time"] > STALE_SECONDS:
            state.pending = None
        changed = changed or state.dirty
    packets = [s.last_packet for s in _sources.values() if s.last_packet is not None]
    _last_packet = max(packets) if packets else None
    if not changed:
        return
    members = {}
    for source, state in _sources.items():
        for client, member in state.members.items():
            entry = dict(member)
            entry["source"] = source
            if state.stale:
                entry["talking"] = False
            members[(source, client)] = entry
        state.dirty = False
    _members = members
    _connected = any(s.connected and not s.stale for s in _sources.values())
    _bridge_message = "; ".join(s.bridge_message for s in _sources.values() if s.bridge_message)
    _dirty = True


def _render(now):
    global _shapes, _layout_key, _visual_status, _layout_width
    stale = _last_packet is not None and now - _last_packet > STALE_SECONDS
    if _last_packet is None:
        status = "Waiting for voice sender"
    elif stale:
        status = "Voice connection lost"
    elif _bridge_message:
        status = _bridge_message
    elif not _connected:
        status = "Voice sender disconnected"
    else:
        status = "" if _members else "Channel is empty"
    if not _dirty and status == _visual_status:
        return
    names = sorted(_members.values(), key=lambda member: (member["name"].lower(), member["source"], member["client_id"]))
    layout_key = (status, tuple((m["source"], m["client_id"], m["name"], m["self"]) for m in names))
    layout_changed = layout_key != _layout_key
    if status != _visual_status:
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
        label = ac.addLabel(_app, "")
        ac.setFontSize(label, 14)
        ac.setFontColor(label, 1, 1, 1, 1)
        _rows.append(label)
    top = 54 if status else 32
    if layout_changed:
        texts = [" ".join((m["name"] or "Client " + str(m["client_id"])).split()) +
                 (" (you)" if m["self"] else "") for m in names]
        _layout_width = max([130, _text_width(status, 12) + 20] +
                            [int(_text_width(text, 14) * 1.12) + 52 for text in texts])
    width = _layout_width
    row_states = []
    for index, label in enumerate(_rows):
        visible = index < len(names)
        if layout_changed:
            ac.setVisible(label, int(visible))
            for background in _pills[index]:
                ac.setVisible(background, int(visible))
        if visible:
            member = names[index]
            speaking = member["talking"] and not stale
            if layout_changed:
                ac.setText(label, texts[index])
                ac.setPosition(label, 36, top + index * 28 + 3)
            row_states.append(speaking)
            weight = 1 if speaking else 0
            if _font_weights.get(label) != weight:
                ac.setCustomFont(label, "Arial", 0, weight)
                ac.setFontSize(label, 14)
                _font_weights[label] = weight
    if layout_changed:
        ac.setSize(_app, width, top + len(names) * 28 + 4)
    shapes = []
    for index, speaking in enumerate(row_states):
        y = top + index * 28
        state = "active" if speaking else "idle"
        for control, part, x, w in zip(_pills[index], ("left", "middle", "right"),
                                       (6, 18, width - 18), (12, width - 36, 12)):
            if layout_changed:
                ac.setPosition(control, x, y)
                ac.setSize(control, w, 24)
            if _pill_states.get(control) != state:
                ac.setBackgroundTexture(control, "apps/python/GridTalk/assets/" + state + "_" + part + ".png")
                _pill_states[control] = state
        shapes.append((11, y + 2, 20, 20, _textures["green" if speaking else "red"]))
    _shapes = shapes
    _layout_key = layout_key
    _visual_status = status


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
    global _dirty, _last_health
    if _socket is None:
        return
    now = time.monotonic()
    for _ in range(MAX_PACKETS_PER_FRAME):
        try:
            data, address = _socket.recvfrom(65535)
        except OSError as exc:
            if exc.errno not in (errno.EAGAIN, errno.EWOULDBLOCK, 10035):
                ac.log("GridTalk UDP error: " + str(exc))
            break
        try:
            # Coalesce source rosters once after the bounded receive batch.
            _accept(json.loads(data.decode("utf-8")), now, refresh=False)
        except (ValueError, UnicodeError):
            continue
    _refresh_sources(now)
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
    _sources.clear()
