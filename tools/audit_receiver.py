"""Reproduce receiver audit findings with mocked AC; never uses game port 9999.

This is a diagnostic probe, not a suite that expects these bugs to remain.
Output is written under ignored build/; baseline evidence is in docs/.
"""
import importlib.util
import json
from pathlib import Path
import time

root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('benchmark', root / 'tools/benchmark.py')
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)
source = (root / 'ac_app/GridTalk/GridTalk.py').read_text(encoding='utf-8')
results = {}

def fresh():
    return bench.load_app(source)

def state(sequence=1, talking=True):
    return dict(v=2, source='teamspeak', event='state', snapshot=sequence, page=0, pages=1,
                connected=True, server='123', channel='7', clients=[dict(client_id=1, name='Driver', talking=talking, self=True)])

app, clock, calls = fresh()
try:
    # Exercise the real JSON decode boundary without sending to the installed game.
    class Datagram:
        def recvfrom(self, size):
            return b'[' * 10000 + b'0' + b']' * 10000, ('127.0.0.1', 12345)
    actual_socket = app._socket
    app._socket = Datagram()
    try:
        app.acUpdate(0.016)
        results['deep_json'] = 'no exception'
    except Exception as error:
        results['deep_json'] = type(error).__name__
    finally:
        app._socket = actual_socket
finally:
    app.acShutdown()

app, clock, calls = fresh()
try:
    app._accept(state(), 100)
    app._refresh_sources(106)
    before = app._members[('teamspeak', 1)]['talking']
    app._accept(dict(v=2, source='teamspeak', event='talk'), 106)
    results['malformed_talk_revives_stale_speaker'] = {'before': before, 'after': app._members[('teamspeak', 1)]['talking']}
    app._refresh_sources(112)
    app._accept(dict(v=2, source='teamspeak', event='bridge_status', message='Query failed'), 112)
    results['diagnostic_revives_stale_speaker'] = app._members[('teamspeak', 1)]['talking']
finally:
    app.acShutdown()

app, clock, calls = fresh()
try:
    for i in range(16):
        app._accept(dict(v=2, source='bad-' + str(i), event='state'), 100)
    app._accept(state(), 100)
    results['source_slot_exhaustion'] = {'sources': len(app._sources), 'legitimate_source_accepted': 'teamspeak' in app._sources}
finally:
    app.acShutdown()

app, clock, calls = fresh()
try:
    packet = state()
    packet['clients'][0]['client_id'] = True
    app._accept(packet, 100)
    results['boolean_client_id_accepted'] = ('teamspeak', True) in app._members
    packet = state(2)
    packet['clients'] = []
    for i in range(2):
        packet['clients'].append(dict(client_id=1, name='Duplicate ' + str(i), talking=False, self=False))
    app._accept(packet, 100)
    results['duplicate_id_overwrites'] = app._members[('teamspeak', 1)]['name']
    app._accept(dict(v=2, source='teamspeak', event='talk', server='123', channel='7', client_id=1, talking=True), 100)
    packet['snapshot'] = 3
    packet['clients'] = [dict(client_id=1, name='Driver', talking=None, self=True)]
    app._accept(packet, 1000)
    results['unknown_query_preserves_speaking_indefinitely'] = app._members[('teamspeak', 1)]['talking']
finally:
    app.acShutdown()

app, clock, calls = fresh()
try:
    count = 1024
    for page in bench.pages(count, 1):
        app._accept(dict(page, source='teamspeak'), 100, refresh=False)
    app._refresh_sources(100)
    start = time.perf_counter()
    app._render(100)
    results['large_roster'] = {'users': len(app._members), 'native_labels_created': calls['addLabel'],
                             'window_height': app._layout_key and 32 + count * 28 + 4,
                             'mocked_render_ms': round((time.perf_counter() - start) * 1000, 3)}
    packet = state(2)
    packet['clients'][0]['name'] = 'W' * 10000
    app._accept(packet, 100)
    app._render(100)
    results['long_name_window_width'] = app._layout_width
finally:
    app.acShutdown()

print(json.dumps(results, indent=2))
(root / 'build/audit-evidence.json').write_text(json.dumps(results, indent=2) + '\n', encoding='utf-8')
