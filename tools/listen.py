"""Run with AC closed to inspect GridTalk packets from any voice sender."""
import json
import socket

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
try:
    sock.bind(("127.0.0.1", 9999))
except OSError as exc:
    raise SystemExit("Port 9999 is occupied. Close AC or another receiver first. " + str(exc))
sock.settimeout(5)
print("Listening on 127.0.0.1:9999. Voice senders should refresh snapshots about once per second.")
try:
    while True:
        try:
            data, address = sock.recvfrom(65535)
            print(json.dumps(json.loads(data.decode("utf-8")), ensure_ascii=False))
        except socket.timeout:
            print("No UDP for 5 seconds. Check your voice sender; for TeamSpeak, enable Assetto Corsa Notifier.")
        except (ValueError, UnicodeError):
            print("Received an invalid packet")
except KeyboardInterrupt:
    pass
finally:
    sock.close()
