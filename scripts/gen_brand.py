"""Render the brand assets (icon + logo) with Pillow.

Output (sizes follow https://github.com/home-assistant/brands requirements)::

    brand/icon.png      256×256   brand/icon@2x.png   512×512
    brand/logo.png      height 256   brand/logo@2x.png  height 512

Run::

    .venv/Scripts/python scripts/gen_brand.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "brand"

TEAL = (23, 128, 112)
TEAL_DARK = (16, 94, 82)
LEAF = (255, 255, 255)
VEIN = (178, 226, 210)
TEXT = (28, 40, 38)


def _leaf(size: int) -> Image.Image:
    """A leaf: a lens of two arcs pointing to the top-right, with a vein and a stem."""
    import math

    s = size
    layer = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    base = (s * 0.27, s * 0.76)
    length, width = s * 0.56, s * 0.19
    angle = math.radians(-46)
    ca, sa = math.cos(angle), math.sin(angle)

    def to_xy(t: float, side: float) -> tuple[float, float]:
        x, y = t * length, side * width * math.sin(math.pi * t) ** 0.85
        return base[0] + x * ca - y * sa, base[1] + x * sa + y * ca

    steps = 64
    upper = [to_xy(i / steps, 1) for i in range(steps + 1)]
    lower = [to_xy(i / steps, -1) for i in range(steps, -1, -1)]
    d.polygon(upper + lower, fill=LEAF)
    w = max(2, s // 44)
    d.line([to_xy(0.08, 0), to_xy(0.88, 0)], fill=VEIN, width=w)
    for t in (0.3, 0.5, 0.7):
        for side in (1, -1):
            d.line([to_xy(t, 0), to_xy(t + 0.14, side * 0.55)], fill=VEIN, width=max(1, w // 2))
    stem_end = (base[0] - s * 0.09 * ca, base[1] - s * 0.09 * sa)
    d.line([base, stem_end], fill=LEAF, width=w + 2)
    return layer


def _wifi(size: int) -> Image.Image:
    """Three signal arcs in the top-right corner."""
    s = size
    layer = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    cx, cy = s * 0.71, s * 0.29
    w = max(2, s // 40)
    for r in (0.055, 0.105, 0.155):
        rr = s * r
        d.arc((cx - rr, cy - rr, cx + rr, cy + rr), start=210, end=330, fill=LEAF, width=w)
    dot = s * 0.018
    d.ellipse((cx - dot, cy - dot, cx + dot, cy + dot), fill=LEAF)
    return layer


def render_icon(size: int) -> Image.Image:
    """Round teal badge with a leaf and signal arcs."""
    scale = 4  # supersample for smooth edges
    s = size * scale
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = s * 0.03
    d.ellipse((pad, pad, s - pad, s - pad), fill=TEAL_DARK)
    inner = s * 0.075
    d.ellipse((inner, inner, s - inner, s - inner), fill=TEAL)
    img.alpha_composite(_leaf(s))
    img.alpha_composite(_wifi(s))
    return img.resize((size, size), Image.LANCZOS)


def _font(px: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for name in ("arialbd.ttf", "ARIALBD.TTF", "arial.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, px)
        except OSError:
            continue
    return ImageFont.load_default()


def render_logo(height: int) -> Image.Image:
    """Icon followed by the wordmark."""
    icon = render_icon(height)
    font = _font(int(height * 0.46))
    text = "Tuya Eco"
    probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    bbox = probe.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    gap = int(height * 0.14)
    img = Image.new("RGBA", (height + gap + tw + int(height * 0.08), height), (0, 0, 0, 0))
    img.alpha_composite(icon, (0, 0))
    d = ImageDraw.Draw(img)
    d.text((height + gap - bbox[0], (height - th) // 2 - bbox[1]), text, font=font, fill=TEXT)
    return img


def main() -> None:
    OUT.mkdir(exist_ok=True)
    render_icon(256).save(OUT / "icon.png")
    render_icon(512).save(OUT / "icon@2x.png")
    render_logo(256).save(OUT / "logo.png")
    render_logo(512).save(OUT / "logo@2x.png")
    for path in sorted(OUT.glob("*.png")):
        with Image.open(path) as im:
            print(f"{path.name}: {im.size[0]}x{im.size[1]}")


if __name__ == "__main__":
    main()
