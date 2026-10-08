"""Import and exercise GridTalk with AC's actual embedded Python 3.3 runtime."""
import ctypes
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
app_path = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else root / 'ac_app/GridTalk'
game = Path(r'C:\Program Files (x86)\Steam\steamapps\common\assettocorsa')
runtime = ctypes.CDLL(str(game / 'system/x64/python33.dll'))
runtime.Py_SetPath.argtypes = [ctypes.c_wchar_p]
runtime.Py_SetPath(';'.join(str(p) for p in (
    game / 'system/x64/Python33.zip', game / 'python33.zip')))
runtime.Py_Initialize()
runtime.PyRun_SimpleString.argtypes = [ctypes.c_char_p]
code = '''
import sys, types, traceback
sys.stdout = open(%r, 'w')
sys.stderr = sys.stdout
try:
    ac = types.ModuleType('ac')
    sys.modules['ac'] = ac
    sys.path.insert(0, %r)
    import GridTalk
    candidate = GridTalk.socket.socket(GridTalk.socket.AF_INET, GridTalk.socket.SOCK_DGRAM)
    candidate.bind((b'127.0.0.1', 0))
    candidate.setblocking(False)
    candidate.close()
    for name in ('newApp', 'addLabel', 'newTexture'):
        setattr(ac, name, lambda *args: 1)
    for name in ('initFont', 'setSize', 'drawBorder', 'setBackgroundColor',
                 'setBackgroundOpacity', 'addRenderCallback', 'setPosition',
                 'setFontSize', 'setText', 'setVisible', 'glColor4f', 'log'):
        setattr(ac, name, lambda *args: None)
    GridTalk.PORT = 0
    GridTalk.acMain('runtime test')
    assert GridTalk._socket is not None
    GridTalk.acUpdate(0.01)
    GridTalk.acShutdown()
    assert GridTalk._validate(dict(v=2, event='bridge_status', message='Ready'))
    assert GridTalk.time.monotonic() > 0
    print('GridTalk imported with Python ' + sys.version, flush=True)
except Exception:
    traceback.print_exc()
    raise
''' % (str(root / 'build/ac-runtime.log'), str(app_path))
result = runtime.PyRun_SimpleString(code.encode('utf-8'))
runtime.PyRun_SimpleString(b'sys.stdout.flush()')
print((root / 'build/ac-runtime.log').read_text())
raise SystemExit(1 if result else 0)
