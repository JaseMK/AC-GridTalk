"""Generate small antialiased UI textures. Pillow is build-time only."""
from pathlib import Path
import math
from PIL import Image, ImageDraw

output = Path(__file__).resolve().parents[1] / "ac_app/TSVoice/assets"
output.mkdir(parents=True, exist_ok=True)
scale = 4
for state, color in (("idle", (43, 45, 48, 140)), ("active", (53, 78, 59, 155))):
    pill = Image.new("RGBA", (96 * scale, 48 * scale))
    draw = ImageDraw.Draw(pill)
    draw.rounded_rectangle((0, 0, 96 * scale - 1, 48 * scale - 1), radius=24 * scale, fill=color)
    pill = pill.resize((96, 48), Image.Resampling.LANCZOS)
    pill.crop((0, 0, 24, 48)).save(output / (state + "_left.png"))
    pill.crop((48, 0, 49, 48)).save(output / (state + "_middle.png"))
    pill.crop((72, 0, 96, 48)).save(output / (state + "_right.png"))

for state, base in (("red", (240, 45, 30)), ("green", (45, 230, 70))):
    lamp = Image.new("RGBA", (40 * scale, 40 * scale))
    pixels = lamp.load()
    for y in range(lamp.height):
        for x in range(lamp.width):
            nx, ny = (x + 0.5) / (20 * scale) - 1, (y + 0.5) / (20 * scale) - 1
            radius = math.hypot(nx, ny)
            if radius > 0.98:
                continue
            if radius > 0.80:
                grey = int(30 + 24 * max(0, -ny))
                color = (grey, grey, grey, 255)
            else:
                light = max(0.25, 1 - radius * 0.65 + (-nx - ny) * 0.12)
                highlight = math.exp(-((nx + 0.25) ** 2 + (ny + 0.32) ** 2) / 0.045) * 0.8
                rgb = [int(min(255, c * light * (1 - highlight) + 255 * highlight)) for c in base]
                color = tuple(rgb) + (255,)
            pixels[x, y] = color
    lamp.resize((40, 40), Image.Resampling.LANCZOS).save(output / (state + ".png"))
print("Generated 8 UI textures")
