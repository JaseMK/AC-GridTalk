"""Measure Python + real loopback receive cost; AC/GPU calls are mocked.

Run outside the network sandbox. Uses an ephemeral port, never game port 9999.
"""
import argparse
import collections
import json
import platform
import socket
import subprocess
import sys
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_app(source):
    calls = collections.Counter()
    ac = types.ModuleType("ac")
    def stub(name):
        def invoke(*args):
            calls[name] += 1
            return sum(calls.values()) if name in ("newApp", "addLabel", "newTexture") else None
        return invoke
    for name in ("newApp", "addLabel", "newTexture", "initFont", "setSize", "drawBorder",
                 "setBackgroundColor", "setBackgroundOpacity", "addRenderCallback", "setPosition", "setIconPosition",
                 "setFontSize", "setText", "setVisible", "setCustomFont", "setFontColor",
                 "setBackgroundTexture", "glColor4f", "glQuadTextured", "log"):
        setattr(ac, name, stub(name))
    sys.modules["ac"] = ac
    app = types.ModuleType("GridTalkBench")
    app.__file__ = str(ROOT / 'ac_app/GridTalk/GridTalk.py')
    exec(compile(source, "GridTalk.py", "exec"), app.__dict__)
    clock = [100.0]
    app.time = types.SimpleNamespace(monotonic=lambda: clock[0])
    app.PORT = 0
    app.acMain("benchmark")
    return app, clock, calls


def pages(count, sequence):
    members = [{"client_id": i + 1, "name": "Driver {:02d}".format(i + 1),
                "talking": False, "self": i == 0} for i in range(count)]
    return [{"v": 2, "event": "state", "snapshot": sequence, "page": i // 8,
             "pages": (count + 7) // 8, "connected": True, "server": "123",
             "channel": "7", "channel_name": "Race", "clients": members[i:i + 8]}
            for i in range(0, count, 8)]


def measure(source, count, scenario, iterations):
    app, clock, calls = load_app(source)
    for page in pages(count, 1):
        app._accept(page, clock[0])
    app.acUpdate(1 / 120)
    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    address = app._socket.getsockname()
    durations = []
    snapshots = [json.dumps(page).encode("utf-8") for page in pages(count, 2)]
    talk_packets = [json.dumps({"v": 2, "event": "talk", "server": "123", "channel": "7",
                    "client_id": 1, "talking": active}).encode("utf-8") for active in (False, True)]
    calls.clear()
    cpu_seconds = 0.0
    try:
        for index in range(iterations):
            clock[0] += 1 / 120
            app._last_packet = clock[0]  # connected throughout each isolated case
            if hasattr(app, "_sources"):
                for state in app._sources.values():
                    state.last_packet = clock[0]
            if scenario == "speaking_transition":
                sender.sendto(talk_packets[index % 2], address)
            elif scenario == "burst_32":
                for packet_index in range(32):
                    sender.sendto(talk_packets[packet_index % 2], address)
            elif scenario == "unchanged_snapshot":
                # Reuse encoded payloads but advance the receiver's sequence floor.
                if hasattr(app, "_sources"):
                    app._sources["default"].committed = 1
                else:
                    app._committed = 1
                for data in snapshots:
                    sender.sendto(data, address)
            start_cpu = time.process_time()
            start = time.perf_counter_ns()
            if scenario == "draw_submission":
                app._draw_backgrounds(1 / 120)
            else:
                app.acUpdate(1 / 120)
            durations.append((time.perf_counter_ns() - start) / 1000)
            cpu_seconds += time.process_time() - start_cpu
    finally:
        app.acShutdown()
        sender.close()
    durations.sort()
    return {"users": count, "scenario": scenario, "samples": iterations,
            "mean_us": round(sum(durations) / iterations, 3),
            "p95_us": round(durations[int(iterations * 0.95)], 3),
            "p99_us": round(durations[int(iterations * 0.99)], 3),
            "max_us": round(durations[-1], 3), "cpu_seconds": round(cpu_seconds, 4),
            "ac_calls_per_sample": round(sum(calls.values()) / iterations, 3),
            "calls": dict(calls), "snapshot_bytes": sum(map(len, snapshots))}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ref", help="Git ref to benchmark instead of working source")
    parser.add_argument("--output", required=True)
    parser.add_argument("--iterations", type=int, default=3000)
    args = parser.parse_args()
    if args.ref:
        for path in ('ac_app/GridTalk/GridTalk.py', 'ac_app/TSVoice/TSVoice.py'):
            result = subprocess.run(['git', 'show', args.ref + ':' + path], cwd=str(ROOT), capture_output=True)
            if result.returncode == 0:
                source = result.stdout.decode('utf-8')
                break
        else:
            raise SystemExit('No overlay source found at Git revision ' + args.ref)
    else:
        source = (ROOT / 'ac_app/GridTalk/GridTalk.py').read_text(encoding='utf-8')
    rows = [measure(source, count, scenario, args.iterations) for count in (2, 16, 64)
            for scenario in ("idle_update", "unchanged_snapshot", "speaking_transition", "burst_32", "draw_submission")]
    result = {"python": sys.version, "platform": platform.platform(), "source": args.ref or "working tree",
              "limitations": "Real UDP; mocked AC native UI and GPU. Not an FPS benchmark. Synthetic 120 Hz clock; CPU time is coarse on Windows.",
              "results": rows}
    Path(args.output).write_text(json.dumps(result, indent=2), encoding="utf-8")
    for row in rows:
        print("{users:2d} users {scenario:22s} mean={mean_us:8.3f}us p99={p99_us:8.3f}us AC calls={ac_calls_per_sample}".format(**row))


if __name__ == "__main__":
    main()
