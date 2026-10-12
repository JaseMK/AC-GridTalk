"""Build the release downloads from a production build.

    cmake -S . -B build -A x64
    cmake --build build --config Release
    python tools/package.py

Writes to dist/:
  AssettoCorsaNotifier-<version>.ts3_plugin  TeamSpeak installs it on double-click
  GridTalk-<version>.zip                     extract into the Assetto Corsa folder
  GridTalk-TS6-Relay-<version>.exe           TeamSpeak 5/6 relay (standalone tray app)
and the GitHub release notes (this version's CHANGELOG.md section) to
build/release-notes.md.
"""
import argparse
import re
import shutil
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


INSTALL_NOTES = """**Install:** everyone needs `GridTalk-<version>.zip` (the Assetto Corsa app), plus
**one** TeamSpeak connector:

| Your TeamSpeak | Connector to download |
| --- | --- |
| TeamSpeak 3 | `AssettoCorsaNotifier-<version>.ts3_plugin` (TeamSpeak plugin) |
| TeamSpeak 5 or 6 | `GridTalk-TS6-Relay-<version>.exe` (small tray app) |

TeamSpeak 3 users don't need the TS6 Relay. Step-by-step instructions are in
the [README](https://github.com/JaseMK/AC-GridTalk#install).
"""


def release_notes(release):
    changelog = (ROOT / 'CHANGELOG.md').read_text(encoding='utf-8')
    section = re.search(r'^## ' + re.escape(release) + r' .*?\n(.*?)(?=^## |\Z)',
                        changelog, re.MULTILINE | re.DOTALL)
    if not section or not section.group(1).strip():
        raise SystemExit('CHANGELOG.md has no entry for {}; add one before releasing.'.format(release))
    return (section.group(1).strip() + '\n\n' + INSTALL_NOTES.replace('<version>', release))


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


def check_relay(path, release):
    if not path.is_file():
        raise SystemExit('Missing {}. Build the production relay first (see README).'.format(path))
    data = path.read_bytes()
    pe = struct.unpack_from('<I', data, 0x3c)[0]
    if data[pe:pe + 4] != b'PE\0\0' or struct.unpack_from('<H', data, pe + 4)[0] != 0x8664:
        raise SystemExit('{} is not an x64 executable; configure CMake with -A x64.'.format(path))
    marker = 'GridTalk TS6 Relay {} (default UDP 127.0.0.1:{})'.format(release, PORT).encode()
    if marker not in data:
        raise SystemExit('{} is not a {} build for port {}; rebuild it without -DGRIDTALK_UDP_PORT.'.format(
            path, release, PORT))


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
    # Run every check before writing anything.
    notes = release_notes(release)
    files = app_files()
    dll = ROOT / args.build_dir / 'Release' / 'assetto_corsa_notifier.dll'
    check_dll(dll, release)
    relay = ROOT / args.build_dir / 'Release' / 'GridTalk-TS6-Relay.exe'
    check_relay(relay, release)
    notes_path = ROOT / 'build' / 'release-notes.md'
    notes_path.parent.mkdir(exist_ok=True)
    notes_path.write_text(notes, encoding='utf-8')
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
        for path in files:
            archive.write(str(path), 'apps/python/GridTalk/' + path.relative_to(APP).as_posix())

    relay_download = output / 'GridTalk-TS6-Relay-{}.exe'.format(release)
    shutil.copyfile(str(relay), str(relay_download))

    for path in (plugin, app, relay_download):
        print('{}  {:,} bytes'.format(path.relative_to(ROOT), path.stat().st_size))


if __name__ == '__main__':
    main()
