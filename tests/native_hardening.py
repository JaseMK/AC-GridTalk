"""Native formatter/UTF-8 boundaries and lifecycle tests against a test-port DLL."""
import argparse
import json
import socket
import subprocess
from pathlib import Path
from check_schema import packet_validator

parser = argparse.ArgumentParser()
parser.add_argument('--port', type=int, default=19999)
parser.add_argument('--build-dir', default='build-tests')
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
executable = str(root / args.build_dir / 'Release/test_sender.exe')
validator = packet_validator()
subprocess.run([str(root / args.build_dir / 'Release/test_format.exe')], check=True, timeout=5)
for mode in ('--boundary', '--utf8', '--oversize'):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
        receiver.bind(('127.0.0.1', args.port))
        receiver.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1048576)
        receiver.settimeout(3)
        subprocess.run([executable, mode], check=True, timeout=10)
        packets = []
        while True:
            data = receiver.recv(65535)
            assert len(data) <= 32768
            packet = json.loads(data.decode('utf-8'))
            validator.validate(packet)
            packets.append(packet)
            if len(packets) > 1 and packet['event'] == 'reset':
                break
        if mode == '--oversize':
            assert packets[1]['event'] == 'bridge_status'
        else:
            member = packets[1]['clients'][0]
            assert len(member['name']) == (511 if mode == '--boundary' else 510)
subprocess.run([executable, '--stress'], check=True, timeout=20)
for _ in range(3):
    subprocess.run([str(root / args.build_dir / 'Release/test_lifecycle.exe'),
                    str(root / args.build_dir / 'Release/assetto_corsa_notifier.dll')], check=True, timeout=20)
print('PASS: maximum escaped names, partial UTF-8 tail, oversized native roster, schema and lifecycle boundaries')
