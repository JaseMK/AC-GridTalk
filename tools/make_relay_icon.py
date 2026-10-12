"""Generate the relay's tray/app icons (relay/gridtalk.ico, relay/gridtalk-idle.ico).

Standard library only. Shapes are drawn with signed distance fields, which
antialiases every size without supersampling. The lamp uses the same shading as
the overlay's lamps (tools/make_ui_assets.py): green while the relay is sending
your channel, grey otherwise.
"""
import math
import struct
import zlib
from pathlib import Path

OUTPUT = Path(__file__).resolve().parents[1] / 'relay'
SIZES = (16, 20, 24, 32, 40, 48, 64, 256)  # 100% to 250% display scaling, plus Explorer's large view
TILE = (38, 40, 44)
VARIANTS = {
    'gridtalk.ico': {'lamp': (45, 230, 70), 'wave': (236, 238, 240)},
    'gridtalk-idle.ico': {'lamp': (128, 132, 138), 'wave': (150, 154, 160)},
}


def clamp(value):
    return 0.0 if value < 0 else 1.0 if value > 1 else value


def rounded_square(x, y, inset, radius):
    """Signed distance to a square spanning the unit canvas less inset, with rounded corners."""
    qx = abs(x - 0.5) - (0.5 - inset - radius)
    qy = abs(y - 0.5) - (0.5 - inset - radius)
    outside = math.hypot(max(qx, 0), max(qy, 0))
    return outside + min(max(qx, qy), 0) - radius


def arc(x, y, cx, cy, radius, width, spread):
    """Signed distance to an arc around (cx, cy) opening to the right, with round caps."""
    angle = math.atan2(y - cy, x - cx)
    if abs(angle) <= spread:
        return abs(math.hypot(x - cx, y - cy) - radius) - width / 2
    caps = [(cx + radius * math.cos(a), cy + radius * math.sin(a)) for a in (spread, -spread)]
    return min(math.hypot(x - px, y - py) for px, py in caps) - width / 2


def lamp_colour(nx, ny, base):
    """Overlay lamp shading at normalised position (nx, ny) within a unit circle."""
    radius = math.hypot(nx, ny)
    if radius > 0.80:
        grey = 30 + 24 * max(0.0, -ny)
        return (grey, grey, grey)
    light = max(0.25, 1 - radius * 0.65 + (-nx - ny) * 0.12)
    highlight = math.exp(-((nx + 0.25) ** 2 + (ny + 0.32) ** 2) / 0.045) * 0.8
    return tuple(min(255.0, c * light * (1 - highlight) + 255 * highlight) for c in base)


def render(size, lamp, wave):
    pixel = 1.0 / size
    # Small icons get proportionally larger features and a single wave, so they stay legible.
    small = size <= 24
    lamp_x, lamp_y, lamp_r = (0.33, 0.5, 0.22) if small else (0.30, 0.5, 0.18)
    waves = [(0.42, 0.11)] if small else [(0.31, 0.075), (0.47, 0.075)]  # (radius, stroke width)
    rows = []
    for py in range(size):
        row = bytearray()
        for px in range(size):
            x, y = (px + 0.5) * pixel, (py + 0.5) * pixel
            red = green = blue = alpha = 0.0
            tile = clamp(0.5 - rounded_square(x, y, 0.03, 0.2) / pixel)
            # Each layer: (coverage, colour), painted bottom to top and clipped to the tile.
            layers = [(tile, TILE)]
            d = math.hypot(x - lamp_x, y - lamp_y) - lamp_r
            layers.append((tile * clamp(0.5 - d / pixel), lamp_colour((x - lamp_x) / lamp_r, (y - lamp_y) / lamp_r, lamp)))
            for radius, width in waves:
                d = arc(x, y, lamp_x, lamp_y, radius, width, math.radians(45))
                layers.append((tile * clamp(0.5 - d / pixel), wave))
            for coverage, colour in layers:
                if coverage <= 0:
                    continue
                keep = 1 - coverage
                red = colour[0] * coverage + red * keep
                green = colour[1] * coverage + green * keep
                blue = colour[2] * coverage + blue * keep
                alpha = coverage + alpha * keep
            if alpha > 0:  # un-premultiply for PNG
                row += bytes((int(red / alpha + 0.5), int(green / alpha + 0.5), int(blue / alpha + 0.5), int(alpha * 255 + 0.5)))
            else:
                row += bytes(4)
        rows.append(bytes(row))
    return rows


def png(size, rows):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)
    raw = b''.join(b'\0' + row for row in rows)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', size, size, 8, 6, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress(raw, 9)) + chunk(b'IEND', b''))


def ico(images):
    """An .ico of PNG-compressed images (supported by Windows Vista and later)."""
    header = struct.pack('<HHH', 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    entries, data = b'', b''
    for size, image in images:
        entries += struct.pack('<BBBBHHII', size % 256, size % 256, 0, 0, 1, 32, len(image), offset + len(data))
        data += image
    return header + entries + data


for name, colours in VARIANTS.items():
    images = [(size, png(size, render(size, **colours))) for size in SIZES]
    (OUTPUT / name).write_bytes(ico(images))
    if name == 'gridtalk.ico':  # a preview for reviewing the design
        (OUTPUT.parent / 'build').mkdir(exist_ok=True)
        (OUTPUT.parent / 'build' / 'relay-icon-preview.png').write_bytes(images[-1][1])
print('Generated {} icons in {} sizes'.format(len(VARIANTS), len(SIZES)))
