"""GridTalk: UDP voice roster and speaking indicators."""
import errno
import json
import math
import re
import os
import sys
# AC ships Python 3.3 but omits its socket extension from the default path.
# Resolve our own copy before importing socket, independent of other apps.
# AC apps share one interpreter, so remove the path again once it has loaded.
_library = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'lib',
                        'x64' if sys.maxsize > 2 ** 32 else 'x86')
_library_added = _library not in sys.path
if _library_added:
    sys.path.insert(0, _library)
try:
    import socket
finally:
    if _library_added:
        sys.path.remove(_library)
import time
import ac

VERSION = '0.5.0'
PORT = 9999
BIND_RETRY_SECONDS = 3.0
ERROR_LOG_SECONDS = 10.0
MAX_PACKETS_PER_FRAME = 32
MAX_RECEIVE_SECONDS = 0.001
STALE_SECONDS = 5.0
MAX_SOURCES = 16
MAX_MEMBERS = 256
MAX_PAGES = 32
MAX_PACKET_BYTES = 32768
MAX_PENDING_BYTES = 131072
MAX_JSON_DEPTH = 8
MAX_JSON_NODES = 256
MAX_VISIBLE_ROWS = 24
MAX_WINDOW_WIDTH = 420
MAX_HOLD_SECONDS = 10.0
MIN_UI_SCALE = 0.5
MAX_UI_SCALE = 2.0
MAX_ALIASES = 200
MAX_ALIAS_CHARS = 64
ALIAS_KEY_CHARS = 64
SOURCE_RETENTION = 60.0
SEQUENCE_RETENTION = 300.0
MAX_INTEGER = 9007199254740991
_retired = {}
_source_limit_reported = False
_ordered_members = []
_order_key = None
_invalid_text = re.compile('[\x00\ud800-\udfff]')
_socket = None
_next_bind = 0.0
_bind_failed = False
_next_error_log = 0.0
_suppressed_errors = 0
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
_CONFIG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config')
_DEFAULT_SETTINGS = {'show_title_bar': True, 'background_opacity': 0.40, 'speaking_hold_seconds': 0.0,
                     'ui_scale': 1.0}
_settings = dict(_DEFAULT_SETTINGS)
_was_talking = set()
_hold_until = {}
_hold_deadline = None
_aliases = {}
_aliases_mtime = None
_alias_write_failed = False
_unrecorded = set()
_recorded = set()


def _read_ini(path):
    """Return {KEY: value} for the lines of a flat ini file; a missing file reads as empty."""
    values = {}
    try:
        with open(path, encoding='utf-8-sig', errors='replace') as handle:
            for line in handle:
                line = line.split(';', 1)[0].strip()
                if '=' in line and not line.startswith('['):
                    key, value = line.split('=', 1)
                    values[key.strip().upper()] = value.strip()
    except (OSError, ValueError):
        pass
    return values


def _load_settings():
    """Read Content Manager's options: shipped defaults, then the user's overrides."""
    values = _read_ini(os.path.join(_CONFIG_DIR, 'GridTalk_defaults.ini'))
    values.update(_read_ini(os.path.join(_CONFIG_DIR, 'GridTalk.ini')))
    settings = dict(_DEFAULT_SETTINGS)
    shown = values.get('SHOW_TITLE_BAR', '').lower()
    if shown in ('true', '1', 'on', 'yes'):
        settings['show_title_bar'] = True
    elif shown in ('false', '0', 'off', 'no'):
        settings['show_title_bar'] = False
    for key, name, minimum, maximum in (('BACKGROUND_OPACITY', 'background_opacity', 0.0, 1.0),
                                        ('SPEAKING_HOLD_SECONDS', 'speaking_hold_seconds', 0.0, MAX_HOLD_SECONDS),
                                        ('UI_SCALE', 'ui_scale', MIN_UI_SCALE, MAX_UI_SCALE)):
        try:
            number = float(values[key])
        except (KeyError, ValueError):
            continue
        if math.isfinite(number):
            settings[name] = min(max(number, minimum), maximum)
    return settings


def _px(size):
    """A layout size in pixels at the user's UI scale."""
    return max(1, int(round(size * _settings['ui_scale'])))


def _top_margin():
    """Space above the roster: AC's title bar, which does not scale, or a small gap without it."""
    return 32 if _settings['show_title_bar'] else 6


def _alias_key(name):
    """An ini key for a name. Hex keeps any name legal for Content Manager and reversible."""
    data = ' '.join(name.split())[:ALIAS_KEY_CHARS].encode('utf-8')
    return 'n_' + ''.join('{:02x}'.format(byte) for byte in data)


def _alias_label(key):
    """The name behind a key, as Content Manager's label (which ends at '(' or ';')."""
    try:
        name = bytes(int(key[2 + i:4 + i], 16) for i in range(0, len(key) - 2, 2)).decode('utf-8')
    except ValueError:
        return key
    return name.translate({ord('('): '[', ord(')'): ']', ord(';'): ',', 10: ' ', 13: ' '})


def _read_aliases():
    """Return ({key: alias}, [keys in file order]) from Aliases.ini."""
    aliases, keys = {}, []
    try:
        with open(os.path.join(_CONFIG_DIR, 'Aliases.ini'), encoding='utf-8-sig', errors='replace') as handle:
            for line in handle:
                line = line.split(';', 1)[0].strip()
                if line.lower().startswith('n_') and '=' in line:
                    key, value = line.split('=', 1)
                    key = key.strip().lower()
                    if key not in aliases:
                        keys.append(key)
                    aliases[key] = ' '.join(value.split())[:MAX_ALIAS_CHARS]
    except (OSError, ValueError):
        pass
    return aliases, keys


def _write_aliases(aliases, keys):
    lines = ['[ALIASES]']
    lines.extend('{}={} ; {}'.format(key, aliases[key], _alias_label(key)) for key in keys)
    path = os.path.join(_CONFIG_DIR, 'Aliases.ini')
    temporary = path + '.tmp'
    with open(temporary, 'w', encoding='utf-8') as handle:
        handle.write('\n'.join(lines) + '\n')
    os.replace(temporary, path)


def _load_aliases():
    global _aliases, _aliases_mtime, _dirty
    aliases = dict((key, value) for key, value in _read_aliases()[0].items() if value)
    try:
        _aliases_mtime = os.stat(os.path.join(_CONFIG_DIR, 'Aliases.ini')).st_mtime
    except OSError:
        _aliases_mtime = None
    if aliases != _aliases:
        _aliases = aliases
        _dirty = True


def _record_names(names):
    """Add newly seen names to Aliases.ini so they can be given an alias in Content Manager."""
    global _alias_write_failed
    fresh = [name for name in names if name.strip() and _alias_key(name) not in _recorded]
    if not fresh:
        return  # Nothing new, so no file access on ordinary frames.
    _recorded.update(_alias_key(name) for name in fresh)
    aliases, keys = _read_aliases()  # Re-read first so edits made in Content Manager are kept.
    added = False
    for name in fresh:
        key = _alias_key(name)
        if key in aliases or key in _unrecorded:
            continue
        if len(keys) >= MAX_ALIASES:
            droppable = [k for k in keys if not aliases[k]]
            if not droppable:
                _unrecorded.add(key)
                continue
            del aliases[droppable[0]]
            keys.remove(droppable[0])
        aliases[key] = ''
        keys.append(key)
        added = True
    if not added:
        return
    try:
        _write_aliases(aliases, keys)
        _alias_write_failed = False
    except OSError as exc:
        if not _alias_write_failed:
            ac.log('GridTalk could not save Aliases.ini: ' + str(exc))
            _alias_write_failed = True
    _load_aliases()


def _shown_name(member):
    name = ' '.join(member['name'].split())
    return _aliases.get(_alias_key(name)) or name


def acMain(ac_version):
    global _app, _status, _rows, _pills, _layout_key, _visual_status, _bind_failed, _settings
    global _hold_deadline, _order_key
    _settings = _load_settings()
    _order_key = None
    _was_talking.clear()
    _hold_until.clear()
    _hold_deadline = None
    _aliases.clear()
    _unrecorded.clear()
    _recorded.clear()
    _load_aliases()
    _app = ac.newApp("GridTalk")
    if not _settings['show_title_bar']:
        ac.setTitle(_app, "")
    ac.initFont(0, "Arial", 0, 0)
    ac.initFont(0, "Arial", 0, 1)
    ac.setSize(_app, 220, 58)
    ac.drawBorder(_app, 0)
    # Move AC's logo off-window; the title bar still appears on hover for dragging.
    ac.setIconPosition(_app, 0, -10000)
    ac.setBackgroundColor(_app, 0.22, 0.22, 0.22)
    ac.setBackgroundOpacity(_app, _settings['background_opacity'])
    for name in ("red", "green"):
        _textures[name] = ac.newTexture("apps/python/GridTalk/assets/" + name + ".png")
    ac.addRenderCallback(_app, _draw_backgrounds)
    _status = ac.addLabel(_app, "Waiting for voice sender")
    ac.setPosition(_status, _px(10), _top_margin())
    ac.setFontSize(_status, _px(12))
    _rows = []
    _pills = []
    _layout_key = None
    _visual_status = None
    _font_weights.clear()
    _pill_states.clear()
    _bind_failed = False  # the new status label needs the failure text
    _bind(time.monotonic())
    return "GridTalk"


def _bind(now):
    """Bind the receiver, or schedule a retry while another process holds the port."""
    global _socket, _next_bind, _bind_failed, _dirty
    candidate = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Numeric bytes avoid loading IDNA/unicodedata for a loopback-only address.
        candidate.bind((b"127.0.0.1", PORT))
        candidate.setblocking(False)
    except OSError as exc:
        candidate.close()
        _next_bind = now + BIND_RETRY_SECONDS
        if not _bind_failed:
            ac.setText(_status, "UDP port unavailable: " + str(PORT) + " (retrying)")
            ac.log("GridTalk bind failed, retrying every {:g} s: {}".format(BIND_RETRY_SECONDS, exc))
            _bind_failed = True
        return
    _socket = candidate
    _bind_failed = False
    _dirty = True
    ac.log("GridTalk " + VERSION + " listening on 127.0.0.1:" + str(PORT))


def _integer(value, minimum=0, maximum=MAX_INTEGER):
    return type(value) is int and minimum <= value <= maximum


def _string(value, maximum, empty=True):
    # Reject unpaired surrogates before they reach AC's native text boundary.
    return (isinstance(value, str) and len(value) <= maximum and
            (empty or bool(value)) and _invalid_text.search(value) is None)


def _bounded_json(event):
    stack = [(event, 1)]
    nodes = 0
    while stack:
        value, depth = stack.pop()
        nodes += 1
        if nodes > MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
            return False
        if type(value) is float and not math.isfinite(value):
            return False
        if isinstance(value, dict):
            stack.extend((v, depth + 1) for v in value.values())
        elif isinstance(value, list):
            stack.extend((v, depth + 1) for v in value)
    return True


def _validate(event):
    if not isinstance(event, dict) or type(event.get('v')) is not int or not _bounded_json(event):
        return None
    version, kind = event.get('v'), event.get('event')
    if version not in (1, 2) or (version == 1 and kind != 'reset'):
        return None
    source = event.get('source', 'default')
    if not _string(source, 64, False):
        return None
    packet = {'v': version, 'event': kind, 'source': source}
    if kind == 'reset':
        if 'server' in event:
            if not _string(event['server'], 128, False):
                return None
            packet['server'] = event['server']
    elif kind == 'bridge_status':
        if not _string(event.get('message'), 256):
            return None
        packet['message'] = event['message']
    elif kind == 'talk':
        if (not _string(event.get('server'), 128, False) or
                not _string(event.get('channel'), 128, False) or
                not _integer(event.get('client_id'), 1) or type(event.get('talking')) is not bool):
            return None
        for field in ('name', 'self', 'whisper'):
            if field in event and not (_string(event[field], 512) if field == 'name' else type(event[field]) is bool):
                return None
        packet.update((key, event[key]) for key in ('server', 'channel', 'client_id', 'talking'))
    elif kind == 'state':
        sequence, page, pages = event.get('snapshot'), event.get('page'), event.get('pages')
        clients, connected = event.get('clients'), event.get('connected')
        if (not _integer(sequence) or not _integer(pages, 1, MAX_PAGES) or
                not _integer(page, 0, pages - 1) or not isinstance(clients, list) or
                len(clients) > 8 or type(connected) is not bool):
            return None
        server, channel, name = event.get('server'), event.get('channel'), event.get('channel_name', '')
        if connected:
            if not _string(server, 128, False) or not _string(channel, 128, False) or not _string(name, 512):
                return None
        elif clients or page != 0 or pages != 1 or any(key in event for key in ('server', 'channel', 'channel_name')):
            return None
        members = {}
        for member in clients:
            if (not isinstance(member, dict) or not _integer(member.get('client_id'), 1) or
                    not _string(member.get('name'), 512) or 'talking' not in member or
                    (member['talking'] is not None and type(member['talking']) is not bool) or
                    type(member.get('self')) is not bool or member['client_id'] in members):
                return None
            members[member['client_id']] = {key: member[key] for key in ('client_id', 'name', 'talking', 'self')}
        packet.update(snapshot=sequence, page=page, pages=pages, connected=connected,
                      server=server, channel=channel, channel_name=name, clients=members)
    else:
        return None
    return packet


class _SourceState:
    def __init__(self):
        self.members = {}
        self.known_at = {}
        self.server = None
        self.channel = None
        self.channel_name = ''
        self.connected = False
        self.pending = None
        self.committed = -1
        self.last_packet = None  # Applicable voice data, never mere diagnostics.
        self.last_seen = None
        self.dirty = False
        self.bridge_message = ''
        self.stale = False
        self.check_at = 0.0

    def expire(self, now):
        if now <= self.check_at:
            return
        deadlines = []
        stale = self.last_packet is not None and now - self.last_packet > STALE_SECONDS
        if stale != self.stale:
            self.stale = stale
            self.dirty = True
        if self.last_packet is not None and not stale:
            deadlines.append(self.last_packet + STALE_SECONDS)
        for client, member in self.members.items():
            if member['talking']:
                deadline = self.known_at.get(client, -STALE_SECONDS) + STALE_SECONDS
                if stale or now > deadline:
                    member['talking'] = False
                    self.dirty = True
                else:
                    deadlines.append(deadline)
        if self.pending is not None:
            deadline = self.pending['time'] + STALE_SECONDS
            if now > deadline:
                self.pending = None
            else:
                deadlines.append(deadline)
        self.check_at = min(deadlines) if deadlines else now + STALE_SECONDS

    def accept(self, event, now, budget):
        kind = event['event']
        self.expire(now)
        if kind == 'bridge_status':
            message = event['message']
            if message != self.bridge_message:
                self.bridge_message = message
                self.dirty = True
            self.last_seen = now
            return True
        if kind == 'reset':
            if 'server' in event and event['server'] != self.server:
                return False
            self.members.clear()
            self.known_at.clear()
            self.connected = False
            self.pending = None
            self.committed = -1
            self.bridge_message = ''
            self.last_packet = self.last_seen = now
            self.stale = False
            self.check_at = now + STALE_SECONDS
            self.dirty = True
            return True
        if kind == 'talk':
            client, talking = event['client_id'], event['talking']
            matches = event['server'] == self.server and event['channel'] == self.channel and client in self.members
            pending = self.pending
            pending_matches = (pending is not None and event['server'] == pending['server'] and
                               event['channel'] == pending['channel'])
            if pending_matches:
                if client not in pending['talks'] and len(pending['talks']) >= MAX_MEMBERS:
                    return False
                pending['talks'][client] = (talking, now)
            if not matches:
                return pending_matches
            if self.members[client]['talking'] != talking:
                self.members[client]['talking'] = talking
                self.dirty = True
            self.known_at[client] = now
            self.last_packet = self.last_seen = now
            self.dirty = self.dirty or self.stale
            self.stale = False
            self.check_at = 0.0
            return True
        sequence, page, pages = event['snapshot'], event['page'], event['pages']
        if sequence <= self.committed:
            return False
        pending = self.pending
        if pending is None or sequence > pending['id']:
            pending = {'id': sequence, 'pages': pages, 'parts': {}, 'talks': {}, 'time': now,
                       'server': event['server'], 'channel': event['channel'],
                       'name': event['channel_name'], 'connected': event['connected'], 'bytes': 0}
        if (sequence != pending['id'] or pages != pending['pages'] or
                event['server'] != pending['server'] or event['channel'] != pending['channel'] or
                event['channel_name'] != pending['name'] or event['connected'] != pending['connected']):
            return False
        normalized = event['clients']
        for index, part in pending['parts'].items():
            if index != page and any(client in part for client in normalized):
                return False
        cost = sum(len(member['name'].encode('utf-8')) + 64 for member in normalized.values())
        previous = pending['parts'].get(page, {})
        old_cost = sum(len(member['name'].encode('utf-8')) + 64 for member in previous.values())
        if pending['bytes'] - old_cost + cost > MAX_PENDING_BYTES:
            return False
        if sum(len(part) for index, part in pending['parts'].items() if index != page) + len(normalized) > budget:
            return False
        pending['parts'][page] = normalized
        pending['bytes'] += cost - old_cost
        self.pending = pending
        self.last_seen = now
        self.check_at = 0.0
        if len(pending['parts']) != pages:
            return True
        members, known = {}, {}
        same_channel = event['server'] == self.server and event['channel'] == self.channel
        for index in range(pages):
            members.update(pending['parts'][index])
        for client, member in members.items():
            if member['talking'] is None:
                timestamp = self.known_at.get(client, -STALE_SECONDS) if same_channel else -STALE_SECONDS
                member['talking'] = (same_channel and now - timestamp <= STALE_SECONDS and
                                     self.members.get(client, {}).get('talking', False))
                known[client] = timestamp
            else:
                known[client] = now
        for client, (talking, timestamp) in pending['talks'].items():
            if client in members:
                members[client]['talking'] = talking if now - timestamp <= STALE_SECONDS else False
                known[client] = timestamp
        self.dirty = (self.dirty or members != self.members or self.server != event['server'] or
                      self.channel != event['channel'] or self.channel_name != event['channel_name'] or
                      self.connected != event['connected'] or bool(self.bridge_message) or self.stale)
        self.members, self.known_at = members, known
        self.server, self.channel, self.channel_name = event['server'], event['channel'], event['channel_name']
        self.connected = event['connected']
        self.committed = sequence
        self.pending = None
        self.last_packet = now
        self.stale = False
        self.bridge_message = ''
        return True


def _retire(source, now):
    state = _sources.pop(source)
    _retired[source] = (state.committed, now + SEQUENCE_RETENTION)
    if len(_retired) > 32:
        del _retired[min(_retired, key=lambda key: _retired[key][1])]


def _accept(event, now, refresh=True):
    global _source_limit_reported, _dirty
    packet = _validate(event)
    if packet is None:
        return False
    source = packet['source']
    state = _sources.get(source)
    if state is None:
        if packet['event'] in ('talk', 'reset'):
            # No roster to mutate; don't reserve sender slots for these packets.
            if packet['event'] == 'reset':
                _retired.pop(source, None)
            return False
        if len(_sources) >= MAX_SOURCES:
            candidates = [key for key, value in _sources.items()
                          if not value.connected and value.pending is None]
            if not candidates:
                if not _source_limit_reported:
                    ac.log('GridTalk sender limit reached')
                    _source_limit_reported = True
                return False
            _retire(min(candidates, key=lambda key: _sources[key].last_seen or 0), now)
            _dirty = True
        state = _SourceState()
        retired = _retired.get(source)
        if retired is not None and now <= retired[1]:
            state.committed = retired[0]
    budget = MAX_MEMBERS - sum(len(s.members) for key, s in _sources.items() if key != source)
    accepted = state.accept(packet, now, budget)
    if accepted:
        _sources[source] = state
    if refresh:
        _refresh_sources(now)
    return accepted


def _refresh_sources(now):
    global _members, _connected, _last_packet, _bridge_message, _dirty
    changed = False
    for source, state in list(_sources.items()):
        state.expire(now)
        if state.last_seen is not None and now - state.last_seen > SOURCE_RETENTION:
            _retire(source, now)
            changed = True
        changed = changed or state.dirty
    for source, retired in list(_retired.items()):
        if now > retired[1]:
            del _retired[source]
    packets = [s.last_packet if s.last_packet is not None else s.last_seen for s in _sources.values()]
    if packets:
        _last_packet = max(value for value in packets if value is not None)
    if not changed:
        return
    members = {}
    for source, state in _sources.items():
        for client, member in state.members.items():
            entry = dict(member)
            entry['source'] = source
            members[(source, client)] = entry
        state.dirty = False
    _members = members
    _connected = any(s.connected and not s.stale for s in _sources.values())
    _bridge_message = '; '.join(s.bridge_message for s in _sources.values() if s.bridge_message)
    _dirty = True


def _render(now):
    global _shapes, _layout_key, _visual_status, _layout_width, _ordered_members, _order_key
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
    if len(_members) > MAX_VISIBLE_ROWS:
        status = 'Showing {} of {} users'.format(MAX_VISIBLE_ROWS, len(_members)) if not status else status
    status = _clip_text(status, _px(12), _px(MAX_WINDOW_WIDTH - 20))
    if not _dirty and status == _visual_status:
        return
    _record_names([member['name'] for member in _members.values()])
    shown = dict((key, _shown_name(member)) for key, member in _members.items())
    order_key = tuple((key, shown[key], member['self']) for key, member in _members.items())
    if order_key != _order_key:
        _ordered_members = sorted(_members, key=lambda key: (shown[key].lower(), key))[:MAX_VISIBLE_ROWS]
        _order_key = order_key
    names = [_members[key] for key in _ordered_members]
    layout_key = (status, tuple((m["source"], m["client_id"], shown[key], m["self"])
                                for key, m in zip(_ordered_members, names)))
    layout_changed = layout_key != _layout_key
    if status != _visual_status:
        ac.setText(_status, status)
        ac.setVisible(_status, 1 if status else 0)
    hold = _settings['speaking_hold_seconds']
    lit = _speaking_keys(now, hold) if hold and not stale else ()
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
        ac.setFontSize(label, _px(14))
        ac.setFontColor(label, 1, 1, 1, 1)
        _rows.append(label)
    top = _top_margin() + (_px(22) if status else 0)
    if layout_changed:
        texts = [(shown[key] or "Client " + str(m["client_id"])) + (" (you)" if m["self"] else "")
                 for key, m in zip(_ordered_members, names)]
        texts = [_clip_text(text, _px(14), _px(MAX_WINDOW_WIDTH - 52) / 1.12) for text in texts]
        _layout_width = max([_px(130), _text_width(status, _px(12)) + _px(20)] +
                            [int(_text_width(text, _px(14)) * 1.12) + _px(52) for text in texts])
    _layout_width = min(_layout_width, _px(MAX_WINDOW_WIDTH))
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
            speaking = (member["talking"] and not stale) or (_ordered_members[index] in lit)
            if layout_changed:
                ac.setText(label, texts[index])
                ac.setPosition(label, _px(36), top + index * _px(28) + _px(3))
            row_states.append(speaking)
            weight = 1 if speaking else 0
            if _font_weights.get(label) != weight:
                ac.setCustomFont(label, "Arial", 0, weight)
                ac.setFontSize(label, _px(14))
                _font_weights[label] = weight
    if layout_changed:
        ac.setSize(_app, width, top + len(names) * _px(28) + _px(4))
    shapes = []
    for index, speaking in enumerate(row_states):
        y = top + index * _px(28)
        state = "active" if speaking else "idle"
        for control, part, x, w in zip(_pills[index], ("left", "middle", "right"),
                                       (_px(6), _px(18), width - _px(18)),
                                       (_px(12), width - _px(36), _px(12))):
            if layout_changed:
                ac.setPosition(control, x, y)
                ac.setSize(control, w, _px(24))
            if _pill_states.get(control) != state:
                ac.setBackgroundTexture(control, "apps/python/GridTalk/assets/" + state + "_" + part + ".png")
                _pill_states[control] = state
        shapes.append((_px(11), y + _px(2), _px(20), _px(20), _textures["green" if speaking else "red"]))
    _shapes = shapes
    _layout_key = layout_key
    _visual_status = status


def _speaking_keys(now, hold):
    """Members who spoke within the last `hold` seconds, and so stay lit."""
    global _hold_deadline
    for key in [key for key in _hold_until if key not in _members]:
        del _hold_until[key]
    _was_talking.intersection_update(_members)
    for key, member in _members.items():
        if member['talking']:
            _was_talking.add(key)
            _hold_until.pop(key, None)
        elif key in _was_talking:
            _was_talking.discard(key)
            _hold_until[key] = now + hold
    for key in [key for key, until in _hold_until.items() if until <= now]:
        del _hold_until[key]
    _hold_deadline = min(_hold_until.values()) if _hold_until else None
    return set(_hold_until)


def _draw_backgrounds(delta_t):
    ac.glColor4f(1, 1, 1, 1)
    for shape in _shapes:
        ac.glQuadTextured(*shape)


def _clip_text(text, size, width):
    text = ' '.join(text.split())
    if _text_width(text, size) <= width:
        return text
    left, right = 0, len(text)
    while left < right:
        middle = (left + right + 1) // 2
        if _text_width(text[:middle] + '...', size) <= width:
            left = middle
        else:
            right = middle - 1
    return text[:left] + '...'


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


def _reload_aliases_if_changed():
    try:
        mtime = os.stat(os.path.join(_CONFIG_DIR, 'Aliases.ini')).st_mtime
    except OSError:
        mtime = None
    if mtime != _aliases_mtime:
        _load_aliases()


def acUpdate(delta_t):
    global _dirty, _last_health, _next_error_log, _suppressed_errors
    now = time.monotonic()
    # AC restores its default background while the window is hovered or dragged,
    # and offers no way to read the value back, so apply ours every frame.
    ac.setBackgroundOpacity(_app, _settings['background_opacity'])
    if _socket is None:
        if now >= _next_bind:
            _bind(now)
        if _socket is None:
            return
    for packet_index in range(MAX_PACKETS_PER_FRAME):
        # Check every four datagrams to keep the ordinary one-packet path cheap.
        if packet_index and packet_index % 4 == 0 and time.monotonic() - now >= MAX_RECEIVE_SECONDS:
            break
        try:
            data, address = _socket.recvfrom(65535)
        except OSError as exc:
            if exc.errno not in (errno.EAGAIN, errno.EWOULDBLOCK, 10035):
                # A persistent socket fault must not write to py_log.txt every frame.
                if now >= _next_error_log:
                    suffix = " ({} similar errors suppressed)".format(_suppressed_errors) if _suppressed_errors else ""
                    ac.log("GridTalk UDP error: " + str(exc) + suffix)
                    _next_error_log = now + ERROR_LOG_SECONDS
                    _suppressed_errors = 0
                else:
                    _suppressed_errors += 1
            break
        try:
            # Coalesce source rosters once after the bounded receive batch.
            if len(data) > MAX_PACKET_BYTES:
                continue
            _accept(json.loads(data.decode("utf-8"), parse_int=_parse_integer), now, refresh=False)
        except (ValueError, UnicodeError, RuntimeError):
            continue
    _refresh_sources(now)
    if _hold_deadline is not None and now >= _hold_deadline:
        _dirty = True
    if now - _last_health >= 1.0:
        _reload_aliases_if_changed()
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
    _retired.clear()


def _parse_integer(value):
    if len(value) > 17:
        raise ValueError('JSON integer exceeds protocol bounds')
    return int(value)
