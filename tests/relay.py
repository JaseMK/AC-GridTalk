"""End-to-end test of the TeamSpeak 5/6 relay against a fake Remote Apps server.

The fake speaks WebSocket and the TeamSpeak messages the relay uses. Every UDP
packet the relay sends is validated against the GridTalk schema, and the
packets are replayed through the real AC receiver code.
"""
import argparse
import base64
import ctypes
import ctypes.wintypes
import hashlib
import importlib.util
import json
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import types
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'tests'))
from check_schema import packet_validator  # noqa: E402

GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'
SOURCE = 'teamspeak-remote'
ME = 'Jasé 日本 "quoted" \\ back'

parser = argparse.ArgumentParser()
parser.add_argument('--build-dir', default='build-tests')
options = parser.parse_args()
exe = root / options.build_dir / 'Release' / 'GridTalkRelay.exe'
assert exe.is_file(), 'build the relay first: {}'.format(exe)


class Peer:
    """One WebSocket connection from the relay, seen from the TeamSpeak side."""

    def __init__(self, conn):
        self.conn = conn
        self.buffer = b''
        self.control = []
        conn.settimeout(10)

    def _fill(self, count):
        while len(self.buffer) < count:
            chunk = self.conn.recv(65536)
            if not chunk:
                raise EOFError('relay closed the connection')
            self.buffer += chunk

    def _read(self, count):
        self._fill(count)
        data, self.buffer = self.buffer[:count], self.buffer[count:]
        return data

    def handshake(self):
        while b'\r\n\r\n' not in self.buffer:
            chunk = self.conn.recv(4096)
            if not chunk:
                raise EOFError('relay closed the connection during the handshake')
            self.buffer += chunk
        head, self.buffer = self.buffer.split(b'\r\n\r\n', 1)
        lines = head.decode('ascii').split('\r\n')
        assert lines[0] == 'GET / HTTP/1.1', lines[0]
        headers = {key.strip().lower(): value.strip() for key, value in (line.split(':', 1) for line in lines[1:])}
        assert headers['upgrade'].lower() == 'websocket' and headers['sec-websocket-version'] == '13', headers
        accept = base64.b64encode(hashlib.sha1((headers['sec-websocket-key'] + GUID).encode()).digest()).decode()
        self.conn.sendall(('HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n'
                           'Sec-WebSocket-Accept: ' + accept + '\r\n\r\n').encode())

    def frame(self):
        first, second = self._read(2)
        assert second & 0x80, 'client frames must be masked'
        length = second & 0x7f
        if length == 126:
            length = struct.unpack('>H', self._read(2))[0]
        elif length == 127:
            length = struct.unpack('>Q', self._read(8))[0]
        mask = self._read(4)
        payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(self._read(length)))
        return first & 0x0f, payload

    def message(self):
        while True:
            opcode, payload = self.frame()
            if opcode == 1:
                return json.loads(payload.decode('utf-8'))
            if opcode == 8:
                raise EOFError('relay sent a close frame')
            self.control.append((opcode, payload))

    def send(self, payload, opcode=1, fin=True):
        size = len(payload)
        if size < 126:
            header = bytes([size])
        elif size < 65536:
            header = bytes([126]) + struct.pack('>H', size)
        else:
            header = bytes([127]) + struct.pack('>Q', size)
        self.conn.sendall(bytes([(0x80 if fin else 0) | opcode]) + header + payload)

    def event(self, kind, **payload):
        self.send(json.dumps({'type': kind, 'payload': payload}).encode('utf-8'))

    def close(self):
        self.send(struct.pack('>H', 1001), opcode=8)
        self.conn.close()


class Inbox:
    """Schema-checked UDP packets from the relay, in arrival order."""

    def __init__(self, sock):
        self.sock = sock
        self.items = []
        self.cursor = 0
        self.cond = threading.Condition()
        self.validator = packet_validator()
        self.errors = []
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while True:
            try:
                data = self.sock.recv(65535)
            except OSError:
                return
            try:
                packet = json.loads(data.decode('utf-8'))
                self.validator.validate(packet)
                assert packet.get('source') == SOURCE, packet
                if packet['event'] == 'state':
                    assert packet['page'] < packet['pages'], packet
            except Exception as error:  # reported by the main thread
                self.errors.append((data, error))
                continue
            with self.cond:
                self.items.append(packet)
                self.cond.notify_all()

    def wait(self, predicate, what, timeout=5.0):
        """Return the next packet after the cursor that matches, advancing past it."""
        deadline = time.monotonic() + timeout
        with self.cond:
            while True:
                while self.cursor < len(self.items):
                    packet = self.items[self.cursor]
                    self.cursor += 1
                    if predicate(packet):
                        return packet
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise AssertionError('timed out waiting for {}; recent packets: {}'.format(
                        what, json.dumps(self.items[-4:], ensure_ascii=False)[:2000]))
                self.cond.wait(remaining)

    def roster(self, predicate, what, timeout=5.0):
        """Return the next complete snapshot whose roster matches."""
        pending = {}
        deadline = time.monotonic() + timeout
        while True:
            packet = self.wait(lambda p: p['event'] == 'state', what, max(0.01, deadline - time.monotonic()))
            parts = pending.setdefault(packet['snapshot'], {})
            parts[packet['page']] = packet
            if len(parts) != packet['pages']:
                continue
            clients = {}
            for part in parts.values():
                for key in ('connected', 'server', 'channel', 'channel_name', 'pages'):
                    assert part.get(key) == packet.get(key), (part, packet)
                for client in part['clients']:
                    assert client['client_id'] not in clients, 'duplicate member across pages'
                    clients[client['client_id']] = client
            roster = dict(packet, clients=clients)
            if predicate(roster):
                return roster

    def mark(self):
        with self.cond:
            self.cursor = len(self.items)


def client(client_id, channel, name, **properties):
    return {'id': client_id, 'channelId': channel, 'properties': dict(nickname=name, flagTalking=False, **properties)}


def channel(channel_id, name):
    return {'id': channel_id, 'order': '0', 'parentId': '0', 'properties': {'name': name}}


def accept(server):
    conn, _ = server.accept()
    peer = Peer(conn)
    peer.handshake()
    return peer


def names(roster):
    return {client_id: c['name'] for client_id, c in roster['clients'].items()}


udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
udp.bind(('127.0.0.1', 0))
udp_port = udp.getsockname()[1]
inbox = Inbox(udp)
server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.bind(('127.0.0.1', 0))
server.listen(4)
server.settimeout(10)
ts_port = server.getsockname()[1]
config = Path(tempfile.mkdtemp()) / 'relay.ini'
log = []
relay = subprocess.Popen([str(exe), '--console', '--dump', '--ts-port', str(ts_port), '--udp-port', str(udp_port),
                          '--config', str(config), '--retry-ms', '300'],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
threading.Thread(target=lambda: log.extend(line.decode('utf-8', 'replace').rstrip() for line in relay.stderr),
                 daemon=True).start()

try:
    # Startup: a reset, then a WebSocket handshake and a sign-in with no saved key.
    inbox.wait(lambda p: p['event'] == 'reset', 'startup reset')
    peer = accept(server)
    auth = peer.message()
    assert auth['type'] == 'auth', auth
    assert auth['payload']['identifier'] == 'gridtalk.relay' and auth['payload']['name'] == 'GridTalk', auth
    assert auth['payload']['content'] == {'apiKey': ''}, auth

    # While TeamSpeak waits for the user to approve, the overlay says so.
    hint = inbox.wait(lambda p: p['event'] == 'bridge_status', 'approval hint', timeout=4)
    assert 'Allow GridTalk' in hint['message'], hint

    peer.event('auth', apiKey='KEY-1', connections=[{
        'id': 1, 'clientId': 5, 'properties': {},
        'channelInfos': {'rootChannels': [channel(10, 'Lobby'), channel(11, 'Race "chat"')],
                         'subChannels': {'11': [channel(12, 'Pit')]}},
        'clientInfos': [client(5, 11, ME, inputHardware=True), client(6, 11, 'BigCrow88'),
                        client(7, 10, 'Elsewhere'), client(8, 12, 'In a sub-channel')]}])
    roster = inbox.roster(lambda r: r['connected'], 'first roster')
    assert (roster['server'], roster['channel'], roster['channel_name']) == ('1', '11', 'Race "chat"'), roster
    assert names(roster) == {5: ME, 6: 'BigCrow88'}, names(roster)
    assert roster['clients'][5]['self'] and not roster['clients'][6]['self']
    assert 'api_key=KEY-1' in config.read_text(encoding='utf-8')

    # Talk events: immediate for our channel, never for another channel.
    peer.event('talkStatusChanged', connectionId=1, clientId=6, status=1, isWhisper=False)
    talk = inbox.wait(lambda p: p['event'] == 'talk', 'talk start')
    assert (talk['client_id'], talk['talking'], talk['name'], talk['self']) == (6, True, 'BigCrow88', False), talk
    assert (talk['server'], talk['channel']) == ('1', '11'), talk
    peer.event('talkStatusChanged', connectionId=1, clientId=6, status=0, isWhisper=False)
    assert inbox.wait(lambda p: p['event'] == 'talk', 'talk stop')['talking'] is False
    peer.event('talkStatusChanged', connectionId=1, clientId=7, status=1, isWhisper=False)
    peer.event('talkStatusChanged', connectionId=1, clientId=5, status=1, isWhisper=True)
    talk = inbox.wait(lambda p: p['event'] == 'talk', 'own talk')
    assert (talk['client_id'], talk['self'], talk['whisper'], talk['name']) == (5, True, True, ME), talk
    roster = inbox.roster(lambda r: r['clients'].get(5, {}).get('talking'), 'speaker in the periodic snapshot')
    peer.event('talkStatusChanged', connectionId=1, clientId=5, status=2, isWhisper=False)  # talking while disabled
    assert inbox.wait(lambda p: p['event'] == 'talk', 'talk while disabled')['talking'] is False

    # Joins, moves, leaves and renames update the roster straight away.
    inbox.mark()
    peer.event('clientMoved', connectionId=1, clientId=7, oldChannelId=10, newChannelId=11,
               properties={'nickname': 'Elsewhere'}, type=0, visibility=0)
    started = time.monotonic()
    inbox.roster(lambda r: 7 in r['clients'], 'move into our channel')
    assert time.monotonic() - started < 0.9, 'move should not wait for the periodic snapshot'
    peer.event('clientMoved', connectionId=1, clientId=6, oldChannelId=11, newChannelId=0, properties={}, type=0, visibility=0)
    inbox.roster(lambda r: 6 not in r['clients'], 'leave the server')
    peer.event('clientMoved', connectionId=1, clientId=9, oldChannelId=0, newChannelId=11,
               properties={'nickname': 'Newbie'}, type=0, visibility=0)
    inbox.roster(lambda r: names(r).get(9) == 'Newbie', 'join the server')
    peer.event('clientPropertiesUpdated', connectionId=1, clientId=9, properties={'nickname': 'Renamed'})
    inbox.roster(lambda r: names(r).get(9) == 'Renamed', 'rename')
    # Control characters are escaped; an unpaired surrogate arrives as U+FFFD.
    peer.event('clientPropertiesUpdated', connectionId=1, clientId=7, properties={'nickname': 'Tab\there \ud800 end'})
    inbox.roster(lambda r: names(r).get(7) == 'Tab\there � end', 'escaped rename')

    # A message split into fragments, with a ping between them.
    message = json.dumps({'type': 'talkStatusChanged',
                          'payload': {'connectionId': 1, 'clientId': 9, 'status': 1, 'isWhisper': False}}).encode()
    peer.send(message[:20], opcode=1, fin=False)
    peer.send(b'are you there', opcode=9)
    peer.send(message[20:], opcode=0, fin=True)
    talk = inbox.wait(lambda p: p['event'] == 'talk', 'fragmented talk')
    assert (talk['client_id'], talk['talking']) == (9, True), talk
    peer.send(b'and again', opcode=9)
    peer.event('talkStatusChanged', connectionId=1, clientId=9, status=0, isWhisper=False)
    inbox.wait(lambda p: p['event'] == 'talk' and not p['talking'], 'talk after ping')
    peer.conn.settimeout(2)
    pongs = []
    while len(pongs) < 2:
        opcode, payload = peer.frame()
        if opcode == 10:
            pongs.append(payload)
    assert pongs == [b'are you there', b'and again'], pongs

    # A large channel is paginated, eight members a page.
    for client_id in range(100, 120):
        peer.event('clientMoved', connectionId=1, clientId=client_id, oldChannelId=0, newChannelId=11,
                   properties={'nickname': 'Driver {}'.format(client_id)}, type=0, visibility=0)
    roster = inbox.roster(lambda r: len(r['clients']) == 23, 'large roster')
    assert roster['pages'] == 3, roster['pages']
    replay_until = len(inbox.items)

    # A second server tab: GridTalk follows the tab whose microphone is open.
    peer.event('connectStatusChanged', connectionId=2, status=4, error=0, info={'clientId': 50})
    peer.event('channels', connectionId=2, info={'rootChannels': [channel(20, 'Other server')], 'subChannels': {}})
    peer.event('clientMoved', connectionId=2, clientId=50, oldChannelId=0, newChannelId=20,
               properties={'nickname': ME}, type=0, visibility=0)
    peer.event('clientMoved', connectionId=2, clientId=51, oldChannelId=0, newChannelId=20,
               properties={'nickname': 'Stranger'}, type=0, visibility=0)
    time.sleep(0.3)
    roster = inbox.roster(lambda r: True, 'snapshot while the first tab is active')
    assert roster['server'] == '1', roster['server']
    peer.event('clientSelfPropertyUpdated', connectionId=1, flag='inputHardware', newValue=False, oldValue=True)
    peer.event('clientSelfPropertyUpdated', connectionId=2, flag='inputHardware', newValue=True, oldValue=False)
    roster = inbox.roster(lambda r: r.get('server') == '2', 'switch to the second tab')
    assert names(roster) == {50: ME, 51: 'Stranger'} and roster['channel_name'] == 'Other server', roster
    peer.event('talkStatusChanged', connectionId=1, clientId=9, status=1, isWhisper=False)
    peer.event('talkStatusChanged', connectionId=2, clientId=51, status=1, isWhisper=False)
    talk = inbox.wait(lambda p: p['event'] == 'talk', 'talk on the active tab')
    assert (talk['server'], talk['client_id']) == ('2', 51), talk
    peer.event('connectStatusChanged', connectionId=2, status=0, error=0, info={})
    inbox.roster(lambda r: r.get('server') == '1', 'back to the first tab after disconnecting the second')
    # Our own client leaving the server (channel 0) drops that tab.
    peer.event('clientMoved', connectionId=1, clientId=5, oldChannelId=11, newChannelId=0, properties={},
               type=0, visibility=0)
    inbox.roster(lambda r: not r['connected'], 'no server')

    # Resource use while running.
    psapi = ctypes.WinDLL('psapi')
    kernel32 = ctypes.WinDLL('kernel32')

    class Counters(ctypes.Structure):
        _fields_ = [('cb', ctypes.wintypes.DWORD), ('PageFaultCount', ctypes.wintypes.DWORD)] + \
                   [(name, ctypes.c_size_t) for name in ('PeakWorkingSetSize', 'WorkingSetSize',
                    'QuotaPeakPagedPoolUsage', 'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage',
                    'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage')]

    kernel32.OpenProcess.restype = ctypes.wintypes.HANDLE
    handle = kernel32.OpenProcess(0x1000 | 0x0010, False, relay.pid)
    counters = Counters(cb=ctypes.sizeof(Counters))
    assert psapi.GetProcessMemoryInfo(ctypes.wintypes.HANDLE(handle), ctypes.byref(counters), counters.cb)
    times = [ctypes.wintypes.FILETIME() for _ in range(4)]
    assert kernel32.GetProcessTimes(ctypes.wintypes.HANDLE(handle), *[ctypes.byref(t) for t in times])
    cpu = sum((t.dwHighDateTime << 32 | t.dwLowDateTime) for t in times[2:]) / 1e7
    kernel32.CloseHandle(ctypes.wintypes.HANDLE(handle))
    peak_mb = counters.PeakWorkingSetSize / 2 ** 20
    private_mb = counters.PagefileUsage / 2 ** 20
    assert peak_mb < 16 and private_mb < 8, (peak_mb, private_mb)

    # TeamSpeak closes: the overlay is cleared, and the saved key is used next time.
    inbox.mark()
    peer.close()
    inbox.wait(lambda p: p['event'] == 'reset', 'reset when TeamSpeak goes away')
    peer = accept(server)
    auth = peer.message()
    assert auth['payload']['content'] == {'apiKey': 'KEY-1'}, auth

    # A refused saved key is forgotten, so the next attempt asks the user again.
    peer.conn.close()
    peer = accept(server)
    auth = peer.message()
    assert auth['payload']['content'] == {'apiKey': ''}, auth
    assert 'api_key=KEY-1' not in config.read_text(encoding='utf-8')
    peer.event('auth', apiKey='KEY-2', connections=[{
        'id': 3, 'clientId': 5, 'channelInfos': {'rootChannels': [channel(30, 'Back again')], 'subChannels': {}},
        'clientInfos': [client(5, 30, ME)]}])
    roster = inbox.roster(lambda r: r['connected'], 'roster after signing in again')
    assert roster['server'] == '3' and names(roster) == {5: ME}, roster

    # --stop ends a running relay cleanly: a close frame, then a reset.
    inbox.mark()
    subprocess.run([str(exe), '--stop', '--udp-port', str(udp_port)], check=True, timeout=10)
    assert relay.wait(timeout=10) == 0
    inbox.wait(lambda p: p['event'] == 'reset', 'reset on shutdown')
    opcode, payload = peer.frame()
    while opcode != 8:
        opcode, payload = peer.frame()
    assert payload == struct.pack('>H', 1000), payload
    assert not inbox.errors, inbox.errors
except BaseException:
    relay.kill()
    time.sleep(0.2)
    print('\n'.join(log[-40:]), file=sys.stderr)
    raise

# The AC receiver accepts the relay's packets and shows the same roster.
ac = types.ModuleType('ac')
for name in ('newApp', 'addLabel', 'newTexture'):
    setattr(ac, name, lambda *args: 1)
for name in ('log', 'setText', 'setVisible', 'setPosition', 'setSize', 'setFontSize', 'setFontColor', 'setCustomFont',
             'setBackgroundTexture', 'setBackgroundOpacity', 'drawBorder', 'setIconPosition', 'setTitle',
             'setBackgroundColor', 'addRenderCallback', 'initFont', 'glColor4f', 'glQuadTextured'):
    setattr(ac, name, lambda *args: None)
sys.modules['ac'] = ac
spec = importlib.util.spec_from_file_location('GridTalk', root / 'ac_app/GridTalk/GridTalk.py')
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)
now = time.monotonic()
for packet in inbox.items[:replay_until]:
    app._accept(packet, now)
shown = {key[1]: member['name'] for key, member in app._members.items() if key[0] == SOURCE}
assert len(shown) == 23 and shown[5] == ME and shown[9] == 'Renamed', shown
assert app._connected

print('PASS: relay handshake, approval hint and saved key, talk/move/join/leave/rename, fragments and pings')
print('PASS: pagination, active-tab switching, disconnects, reconnect, refused key, --stop, schema and receiver replay')
print('PASS: relay peak working set {:.1f} MB, private {:.1f} MB, CPU {:.2f} s over the run'.format(peak_mb, private_mb, cpu))
