"""Regression checks for the review findings, using mocked AC and real UDP."""
import copy
import importlib.util
import json
import socket
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('benchmark', ROOT / 'tools/benchmark.py')
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)
SOURCE = (ROOT / 'ac_app/GridTalk/GridTalk.py').read_text(encoding='utf-8')


def snapshot(sequence=1, source='teamspeak', talking=True):
    return dict(v=2, source=source, event='state', snapshot=sequence, page=0, pages=1,
                connected=True, server='123', channel='7', channel_name='Race',
                clients=[dict(client_id=1, name='Driver', talking=talking, self=True)])


def check_validation():
    app, clock, calls = benchmark.load_app(SOURCE)
    try:
        for index in range(40):
            assert not app._accept(dict(v=2, source='bad-' + str(index), event='state'), clock[0])
        assert not app._sources
        assert app._accept(snapshot(), clock[0])
        original = copy.deepcopy(app._members)
        packets = []
        for key, value in [('v', True), ('v', 1), ('snapshot', True), ('snapshot', -1),
                           ('snapshot', 9007199254740992), ('pages', 33), ('page', True),
                           ('connected', False), ('source', ''), ('server', []), ('channel', '')]:
            packet = snapshot(2)
            packet[key] = value
            packets.append(packet)
        for key, value in [('client_id', True), ('client_id', 0), ('client_id', -1),
                           ('name', 'W' * 513), ('name', '\ud800'), ('name', '\x00'), ('talking', 'yes'), ('self', 1)]:
            packet = snapshot(2)
            packet['clients'][0][key] = value
            packets.append(packet)
        duplicate = snapshot(2)
        duplicate['clients'] *= 2
        packets.append(duplicate)
        for packet in packets:
            assert not app._accept(packet, clock[0] + 1), packet
            assert app._members == original
            assert app._sources['teamspeak'].last_packet == clock[0]
        extension = snapshot(2)
        extension['metadata'] = {'nested': ['allowed']}
        extension['clients'][0]['metadata'] = 'not retained'
        assert app._accept(extension, clock[0])
        assert 'metadata' not in app._members[('teamspeak', 1)]
        unicode_packet = snapshot(3)
        unicode_packet['clients'][0]['name'] = 'Jasé 日本語 🏎'
        assert app._accept(unicode_packet, clock[0])
        assert app._members[('teamspeak', 1)]['name'] == 'Jasé 日本語 🏎'
        nonfinite = snapshot(4)
        nonfinite['extra'] = float('nan')
        assert not app._accept(nonfinite, clock[0])
        deep = snapshot(3)
        extra = []
        deep['extra'] = extra
        for _ in range(9):
            nested = []
            extra.append(nested)
            extra = nested
        assert not app._accept(deep, clock[0])
        # A duplicate across different pages or a mismatched channel name cannot commit.
        first = dict(snapshot(4), pages=2)
        assert app._accept(first, clock[0])
        assert not app._accept(dict(first, page=1), clock[0])
        second = copy.deepcopy(first)
        second['page'] = 1
        second['clients'][0]['client_id'] = 2
        assert not app._accept(dict(second, channel_name='Different'), clock[0])
        assert app._accept(second, clock[0])
        assert len(app._members) == 2
    finally:
        app.acShutdown()


def check_freshness_and_slots():
    app, clock, calls = benchmark.load_app(SOURCE)
    try:
        assert app._accept(snapshot(), 100)
        app._refresh_sources(106)
        assert not app._members[('teamspeak', 1)]['talking']
        for packet in [dict(v=2, source='teamspeak', event='talk'),
                       dict(v=2, source='teamspeak', event='bridge_status', message='Query failed'),
                       snapshot(), dict(v=2, source='teamspeak', event='talk', server='other',
                                        channel='7', client_id=1, talking=True)]:
            app._accept(packet, 106)
            assert not app._members[('teamspeak', 1)]['talking']
            assert app._sources['teamspeak'].last_packet == 100
        app._accept(snapshot(2, talking=None), 106)
        assert not app._members[('teamspeak', 1)]['talking']
        app._accept(snapshot(3), 107)
        app._accept(snapshot(4, talking=None), 110)
        assert app._members[('teamspeak', 1)]['talking']
        app._accept(snapshot(5, talking=None), 113)
        assert not app._members[('teamspeak', 1)]['talking']
        app._refresh_sources(119)
        assert not app._connected
        assert app._accept(dict(v=2, source='teamspeak', event='talk', server='123',
                                channel='7', client_id=1, talking=False), 119)
        assert app._connected  # trustworthy stop can recover connection without false green
        app._refresh_sources(180)
        assert not app._sources
        assert not app._accept(snapshot(5), 180)  # sequence floor retained after eviction
        assert app._accept(snapshot(6), 180)
        app.acShutdown()
        for index in range(app.MAX_SOURCES):
            assert app._accept(snapshot(source='source-' + str(index)), 200)
        assert not app._accept(snapshot(source='extra'), 200)
        assert len(app._sources) == app.MAX_SOURCES
        app._refresh_sources(261)
        assert not app._sources
        assert app._accept(snapshot(source='extra'), 261)
    finally:
        app.acShutdown()


def check_limits_and_network():
    app, clock, calls = benchmark.load_app(SOURCE)
    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        address = app._socket.getsockname()
        for data in [b'[' * 10000 + b'0' + b']' * 10000, b' ' * 32769, b'\xff', b'invalid']:
            sender.sendto(data, address)
            app.acUpdate(0.016)
        sender.sendto(json.dumps(snapshot()).encode('utf-8'), address)
        app.acUpdate(0.016)
        assert len(app._members) == 1
        for page in benchmark.pages(256, 2):
            assert app._accept(dict(page, source='teamspeak'), clock[0], refresh=False)
        app._refresh_sources(clock[0])
        assert len(app._members) == app.MAX_MEMBERS
        assert not app._accept(snapshot(source='extra'), clock[0])
        app._render(clock[0])
        assert len(app._rows) <= app.MAX_VISIBLE_ROWS
        assert calls['addLabel'] <= app.MAX_VISIBLE_ROWS * 4 + 1
        assert app._layout_width <= app.MAX_WINDOW_WIDTH
        assert app._visual_status == 'Showing 24 of 256 users'
        long_name = snapshot(3)
        long_name['clients'][0]['name'] = 'W' * 512
        assert app._accept(long_name, clock[0])
        app._render(clock[0])
        assert app._layout_width <= app.MAX_WINDOW_WIDTH
        # Overlarge page counts are rejected before allocating pending parts.
        assert not app._accept(dict(snapshot(4), pages=8192), clock[0])
        # Pending talk overrides are bounded even when the user isn't in a received page yet.
        assert app._accept(dict(snapshot(4), pages=2), clock[0])
        for client in range(1, 257):
            app._accept(dict(v=2, source='teamspeak', event='talk', server='123',
                             channel='7', client_id=client, talking=True), clock[0])
        assert not app._accept(dict(v=2, source='teamspeak', event='talk', server='123',
                                   channel='7', client_id=257, talking=True), clock[0])
        assert len(app._sources['teamspeak'].pending['talks']) == 256
        original_socket = app._socket
        class SlowQueue:
            count = 0
            def recvfrom(self, size):
                self.count += 1
                return json.dumps(snapshot(5)).encode('utf-8'), ('127.0.0.1', 1)
        queue = SlowQueue()
        ticks = iter([100.0, 100.002])
        app.time = types.SimpleNamespace(monotonic=lambda: next(ticks))
        app._socket = queue
        try:
            app.acUpdate(0.016)
            assert queue.count == 4  # time budget stops the batch before the 32-packet cap
        finally:
            app._socket = original_socket
    finally:
        sender.close()
        app.acShutdown()


check_validation()
check_freshness_and_slots()
check_limits_and_network()
print('PASS: strict admission, schema bounds, malformed UDP, freshness, source reclamation, and UI/resource limits')
