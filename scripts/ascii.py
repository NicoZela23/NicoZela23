"""Convert the GitHub avatar into the ASCII portrait used by the profile card.

Run once (or whenever the avatar changes):
    python scripts/ascii.py [image_path_or_url]
Writes assets/ascii.txt. Needs Pillow.
"""
import io
import sys
import urllib.request
from pathlib import Path

from PIL import Image, ImageFilter, ImageOps

AVATAR_URL = "https://github.com/NicoZela23.png?size=460"
OUT = Path(__file__).resolve().parent.parent / "assets" / "ascii.txt"

COLS = 48
CELL_ASPECT = 9 / 20      # char width / line height in the card SVG
CROP = (0.18, 0.02, 0.82, 0.78)  # left, top, right, bottom (fractions) - frames head + shoulders
RAMP = " .:-=+*#%@"
BG_MAX_SAT = 60
BG_MIN_VAL = 120
INVERT = True  # dark areas (hair, glasses, shirt) get dense glyphs
GAMMA = 0.55   # < 1 fills in the mid-tones so the face keeps some texture


def load(src: str) -> Image.Image:
    if src.startswith("http"):
        with urllib.request.urlopen(src) as r:
            return Image.open(io.BytesIO(r.read())).convert("RGB")
    return Image.open(src).convert("RGB")


def remove_background(img: Image.Image) -> Image.Image:
    """Blank out the plain, low-saturation backdrop: flood-fill it from the top and side edges."""
    small = img.resize((200, 200))
    w, h = small.size
    hsv = small.convert("HSV")
    is_bg = [[hsv.getpixel((x, y))[1] < BG_MAX_SAT and hsv.getpixel((x, y))[2] > BG_MIN_VAL
              for x in range(w)] for y in range(h)]
    seeds = [(x, 0) for x in range(w)] + [(x, y) for y in range(int(h * 0.6)) for x in (0, w - 1)]
    seen = set()
    stack = [s for s in seeds if is_bg[s[1]][s[0]]]
    while stack:
        x, y = stack.pop()
        if (x, y) in seen:
            continue
        seen.add((x, y))
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if 0 <= nx < w and 0 <= ny < h and (nx, ny) not in seen and is_bg[ny][nx]:
                stack.append((nx, ny))
    mask = Image.new("L", (w, h), 0)
    for x, y in seen:
        mask.putpixel((x, y), 255)
    mask = mask.filter(ImageFilter.MaxFilter(3)).resize(img.size)
    # equalize using the subject only, so the backdrop doesn't eat the tonal range
    gray = ImageOps.equalize(img.convert("L"), mask=ImageOps.invert(mask))
    gray = gray.filter(ImageFilter.UnsharpMask(radius=2, percent=150, threshold=3))
    if INVERT:
        gray = ImageOps.invert(gray)
    gray = gray.point(lambda v: round(255 * (v / 255) ** GAMMA))
    return Image.composite(Image.new("L", img.size, 0), gray, mask)


def to_ascii(img: Image.Image) -> list[str]:
    w, h = img.size
    l, t, r, b = CROP
    img = img.crop((int(w * l), int(h * t), int(w * r), int(h * b)))
    rows = round(COLS * CELL_ASPECT * img.height / img.width)
    img = img.resize((COLS, rows), Image.LANCZOS)
    lines = []
    for y in range(rows):
        line = ""
        for x in range(COLS):
            v = img.getpixel((x, y))
            line += RAMP[min(len(RAMP) - 1, v * len(RAMP) // 256)]
        lines.append(line.rstrip())
    return lines


def main() -> None:
    src = sys.argv[1] if len(sys.argv) > 1 else AVATAR_URL
    lines = to_ascii(remove_background(load(src)))
    OUT.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\n{len(lines)} rows -> {OUT}")


if __name__ == "__main__":
    main()
