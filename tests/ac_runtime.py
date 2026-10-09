"""Exercise GridTalk inside AC's actual embedded Python 3.3 runtime.

Usage: python tests/ac_runtime.py [app_path] [--ac-path <Assetto Corsa folder>]
Without --ac-path, the installation is located through Steam's registry keys.
The guest code below runs under Python 3.3: no pathlib, no f-strings, and bytes
socket hosts (str hosts need IDNA/unicodedata, which AC does not ship).
"""
import argparse
import ctypes
import os
import re
import sys
from pathlib import Path

AC_APP_ID = '244210'
GUEST = r'''
import json, sys, time, traceback, types
sys.stdout = open(LOG, 'w', encoding='utf-8')
sys.stderr = sys.stdout


def check(condition, message):
    if not condition:
        raise AssertionError(message)


try:
    labels, visible, weights, logs, drawn, controls = {}, {}, {}, [], [], [0]
    ac = types.ModuleType('ac')
    sys.modules['ac'] = ac

    def control(*args):
        controls[0] += 1
        return controls[0]
    for name in ('newApp', 'addLabel', 'newTexture'):
        setattr(ac, name, control)
    for name in ('initFont', 'setSize', 'drawBorder', 'setBackgroundColor', 'setBackgroundOpacity',
                 'addRenderCallback', 'setPosition', 'setIconPosition', 'setFontSize', 'setFontColor',
                 'setBackgroundTexture', 'glColor4f'):
        setattr(ac, name, lambda *args: None)
    ac.log = logs.append
    ac.setText = lambda label, text: labels.__setitem__(label, text)
    ac.setVisible = lambda label, shown: visible.__setitem__(label, shown)
    ac.setCustomFont = lambda label, font, italic, bold: weights.__setitem__(label, bold)
    ac.glQuadTextured = lambda *shape: drawn.append(shape)

    sys.path.insert(0, APP)
    import GridTalk
    check(sys.version_info[:2] == (3, 3), 'expected AC Python 3.3, got ' + sys.version)
    offset = [0.0]
    GridTalk.time = types.SimpleNamespace(monotonic=lambda: time.monotonic() + offset[0])
    GridTalk.PORT = 0
    GridTalk.acMain('runtime test')
    check(GridTalk._socket is not None, 'receiver did not bind: ' + repr(logs))
    address = (b'127.0.0.1', GridTalk._socket.getsockname()[1])
    sender = GridTalk.socket.socket(GridTalk.socket.AF_INET, GridTalk.socket.SOCK_DGRAM)
    red, green = GridTalk._textures['red'], GridTalk._textures['green']
    status = GridTalk._status
    me = 'Jasé 日本'

    def send(packet):
        data = packet if isinstance(packet, bytes) else json.dumps(packet).encode('utf-8')
        sender.sendto(data, address)

    def frame():
        # One AC frame: receive/update, then the render callback draws the lamps.
        del drawn[:]
        GridTalk.acUpdate(0.016)
        GridTalk._draw_backgrounds(0.016)
        return dict((GridTalk._members[key]['name'], shape[-1])
                    for key, shape in zip(GridTalk._ordered_members, drawn))

    def snapshot(sequence, talking=False):
        return dict(v=2, source='teamspeak', event='state', snapshot=sequence, page=0, pages=1,
                    connected=True, server='123', channel='7', channel_name='Race',
                    clients=[dict(client_id=42, name=me, talking=talking, self=True),
                             dict(client_id=43, name='Driver 43', talking=False, self=False)])

    # Snapshot: the roster is committed and rendered with idle (red) lamps.
    frame()
    check(labels[status] == 'Waiting for voice sender', labels[status])
    send(snapshot(1))
    lamps = frame()
    rows = GridTalk._rows
    check(len(GridTalk._members) == 2, GridTalk._members)
    check(visible[status] == 0, 'status should be hidden while connected')
    check([labels[row] for row in rows] == ['Driver 43', me + ' (you)'], [labels[row] for row in rows])
    check(lamps == {'Driver 43': red, me: red}, lamps)

    # Talk: the speaker's lamp turns green and the name turns bold.
    send(dict(v=2, source='teamspeak', event='talk', server='123', channel='7', client_id=42, talking=True))
    lamps = frame()
    check(GridTalk._members[('teamspeak', 42)]['talking'] is True, 'talk start not applied')
    check(lamps == {'Driver 43': red, me: green}, lamps)
    check(weights[rows[1]] == 1 and weights[rows[0]] == 0, weights)

    # Stale: with no packets for over five seconds, the speaker is cleared.
    offset[0] = 6.0
    lamps = frame()
    check(labels[status] == 'Voice connection lost' and visible[status] == 1, labels[status])
    check(lamps == {'Driver 43': red, me: red}, lamps)
    check(weights[rows[1]] == 0, 'stale speaker still bold')

    # Deep JSON: 3.3's decoder raises RuntimeError (there is no RecursionError),
    # and a parseable but over-deep packet is rejected by the depth bound.
    send(b'[' * 10000 + b'0' + b']' * 10000)
    deep = snapshot(2, talking=True)
    nested = deep['extra'] = []
    for _ in range(GridTalk.MAX_JSON_DEPTH):
        nested.append([])
        nested = nested[0]
    send(deep)
    frame()
    check(GridTalk._sources['teamspeak'].committed == 1, 'deep packet was committed')
    check(labels[status] == 'Voice connection lost', 'deep packet refreshed the connection')

    # Render after recovery: the next valid snapshot restores the connected display.
    send(snapshot(3, talking=True))
    lamps = frame()
    check(visible[status] == 0, 'status not cleared after recovery: ' + labels[status])
    check(lamps == {'Driver 43': red, me: green}, lamps)
    sender.close()
    GridTalk.acShutdown()
    check(GridTalk._socket is None and not GridTalk._members, 'shutdown left state behind')
    print('PASS: AC Python ' + sys.version.split()[0] +
          ' snapshot -> talk -> stale -> deep JSON -> recovery render', flush=True)
except Exception:
    traceback.print_exc()
    raise
'''


def steam_ac_path():
    """Find AC from Steam's uninstall entry, then Steam's library folders."""
    import winreg
    candidates = []
    uninstall = r'SOFTWARE\{}Microsoft\Windows\CurrentVersion\Uninstall\Steam App ' + AC_APP_ID
    for hive, key, value in [(winreg.HKEY_LOCAL_MACHINE, uninstall.format(''), 'InstallLocation'),
                             (winreg.HKEY_LOCAL_MACHINE, uninstall.format('WOW6432Node\\'), 'InstallLocation'),
                             (winreg.HKEY_CURRENT_USER, r'Software\Valve\Steam', 'SteamPath'),
                             (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\WOW6432Node\Valve\Steam', 'InstallPath')]:
        try:
            with winreg.OpenKey(hive, key) as handle:
                found = Path(winreg.QueryValueEx(handle, value)[0])
        except OSError:
            continue
        if value == 'InstallLocation':
            candidates.append(found)
            continue
        candidates.append(found / 'steamapps/common/assettocorsa')
        libraries = found / 'steamapps/libraryfolders.vdf'
        if libraries.is_file():
            text = libraries.read_text(encoding='utf-8', errors='replace')
            for library in re.findall(r'"path"\s+"([^"]+)"', text):
                candidates.append(Path(library.replace('\\\\', '\\')) / 'steamapps/common/assettocorsa')
    return next((c for c in candidates if (c / 'system/x64/python33.dll').is_file()), None)


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('app_path', nargs='?', default=str(root / 'ac_app/GridTalk'))
    parser.add_argument('--ac-path', help='Assetto Corsa installation folder (default: from Steam registry)')
    parser.add_argument('--locate', action='store_true', help='print the AC folder and exit; exit 1 if not found')
    options = parser.parse_args()
    game = Path(options.ac_path) if options.ac_path else steam_ac_path()
    if options.locate:
        if game is None:
            raise SystemExit(1)
        print(game)
        return
    if game is None or not (game / 'system/x64/python33.dll').is_file():
        raise SystemExit('Assetto Corsa not found; pass --ac-path with its installation folder.')
    log = root / 'build/ac-runtime.log'
    log.parent.mkdir(exist_ok=True)
    runtime = ctypes.CDLL(str(game / 'system/x64/python33.dll'))
    runtime.Py_SetPath.argtypes = [ctypes.c_wchar_p]
    runtime.Py_SetPath(os.pathsep.join(str(p) for p in (
        game / 'system/x64/Python33.zip', game / 'python33.zip')))
    runtime.Py_Initialize()
    runtime.PyRun_SimpleString.argtypes = [ctypes.c_char_p]
    code = 'LOG = %r\nAPP = %r\n' % (str(log), str(Path(options.app_path).resolve())) + GUEST
    result = runtime.PyRun_SimpleString(code.encode('utf-8'))
    runtime.PyRun_SimpleString(b'sys.stdout.flush()')
    sys.stdout.reconfigure(errors='backslashreplace')  # guest names may exceed the console code page
    print(log.read_text(encoding='utf-8'))
    raise SystemExit(1 if result else 0)


if __name__ == '__main__':
    main()
