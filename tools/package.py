"""Build the release downloads from a production build.

    cmake -S . -B build -A x64
    cmake --build build --config Release
    python tools/package.py

Writes to dist/:
  AssettoCorsaNotifier-<version>.ts3_plugin  TeamSpeak installs it on double-click
  GridTalk-<version>.zip                     extract into the Assetto Corsa folder
"""
import argparse
import re
import struct
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'ac_app' / 'GridTalk'
PORT = 9999


def version():
    cmake = re.search(r'project\(GridTalk VERSION (\d+\.\d+\.\d+)',
                      (ROOT / 'CMakeLists.txt').read_text(encoding='utf-8')).group(1)
    app = re.search(r"^VERSION = '([^']+)'", (APP / 'GridTalk.py').read_text(encoding='utf-8'),
                    re.MULTILINE).group(1)
    if cmake != app:
        raise SystemExit('Version mismatch: CMakeLists.txt {} vs GridTalk.py {}'.format(cmake, app))
    return cmake


def check_dll(path, release):
    if not path.is_file():
        raise SystemExit('Missing {}. Build the production DLL first (see README).'.format(path))
    data = path.read_bytes()
    pe = struct.unpack_from('<I', data, 0x3c)[0]
    if data[pe:pe + 4] != b'PE\0\0' or struct.unpack_from('<H', data, pe + 4)[0] != 0x8664:
        raise SystemExit('{} is not an x64 DLL; configure CMake with -A x64.'.format(path))
    # The description and version strings are compiled in; catch a test-port or stale build.
    if '127.0.0.1:{})'.format(PORT).encode() not in data:
        raise SystemExit('{} does not target port {}; rebuild without -DGRIDTALK_UDP_PORT.'.format(path, PORT))
    if b'\0' + release.encode() + b'\0' not in data:
        raise SystemExit('{} does not report version {}; rebuild it.'.format(path, release))


def app_files():
    # Package exactly the tracked app files, so a missing texture or socket
    # module fails the build instead of shipping a broken zip.
    listed = subprocess.run(['git', 'ls-files', '-z', '--', str(APP)], cwd=str(ROOT), check=True,
                            stdout=subprocess.PIPE).stdout.decode('utf-8').split(chr(0))
    paths = [ROOT / name for name in sorted(filter(None, listed))]
    missing = [str(path.relative_to(ROOT)) for path in paths if not path.is_file()]
    if missing or not paths:
        raise SystemExit('App files missing from the working tree (git restore them): ' + ', '.join(missing))
    return paths


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--build-dir', default='build')
    parser.add_argument('--output', default='dist')
    args = parser.parse_args()
    release = version()
    dll = ROOT / args.build_dir / 'Release' / 'assetto_corsa_notifier.dll'
    check_dll(dll, release)
    output = ROOT / args.output
    output.mkdir(exist_ok=True)

    plugin = output / 'AssettoCorsaNotifier-{}.ts3_plugin'.format(release)
    package_ini = '\r\n'.join([
        'Name = Assetto Corsa Notifier',
        'Type = Plugin',
        'Author = GridTalk',
        'Version = ' + release,
        'Platforms = win64',
        'Description = "Sends your TeamSpeak channel and who is speaking to the GridTalk overlay in Assetto Corsa."',
        ''])
    with zipfile.ZipFile(str(plugin), 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('package.ini', package_ini)
        archive.write(str(dll), 'plugins/assetto_corsa_notifier.dll')

    app = output / 'GridTalk-{}.zip'.format(release)
    with zipfile.ZipFile(str(app), 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in app_files():
            archive.write(str(path), 'apps/python/GridTalk/' + path.relative_to(APP).as_posix())

    for path in (plugin, app):
        print('{}  {:,} bytes'.format(path.relative_to(ROOT), path.stat().st_size))


if __name__ == '__main__':
    main()
