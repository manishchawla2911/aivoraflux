"""Generate dashboard/static/og-image.png (1200x630) for social previews.

Run: python scripts/make_og_image.py   (requires Pillow)
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "dashboard" / "static"
W, H = 1200, 630


def font(size: int, bold: bool = False):
    for path in (
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


img = Image.new("RGB", (W, H), (34, 37, 38))
glow = Image.new("RGB", (W, H), (0, 0, 0))
g = ImageDraw.Draw(glow)
g.ellipse((700, -200, 1400, 450), fill=(255, 101, 116))
g.ellipse((-200, 380, 500, 900), fill=(204, 220, 239))
glow = glow.filter(ImageFilter.GaussianBlur(160))
img = Image.blend(img, glow, 0.22)

d = ImageDraw.Draw(img)
for x in range(0, W, 60):
    d.line([(x, 0), (x, H)], fill=(44, 48, 50))
for y in range(0, H, 60):
    d.line([(0, y), (W, y)], fill=(44, 48, 50))

logo = Image.open(STATIC / "logo-white.png").convert("RGBA")
logo = logo.resize((int(logo.width * 70 / logo.height), 70))
img.paste(logo, (80, 80), logo)

d.text((80, 230), "Building Intelligence.", font=font(70, True), fill=(204, 220, 239))
d.text((80, 318), "Engineering What's Next.", font=font(70, True), fill=(255, 101, 116))
d.text((80, 430), "AI agents  ·  Intelligent automation  ·  Custom software", font=font(30), fill=(169, 184, 200))
d.text((80, 530), "aivoraflux.com", font=font(26, True), fill=(255, 101, 116))

img.save(STATIC / "og-image.png", optimize=True)
print("wrote", STATIC / "og-image.png")
