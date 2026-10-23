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

TEAL = (30, 86, 120)  # deep blue-teal badge
TEAL_DARK = (22, 62, 88)
NODE = (255, 255, 255)
LINK = (170, 214, 232)
RING = (92, 150, 182)
ACCENT = (255, 160, 48)  # the hub: Tuya-orange
TEXT = (28, 40, 38)


def _ecosystem(size: int) -> Image.Image:
    """A hub node with device nodes around it, joined by links: an ecosystem."""
    import math

    s = size
    layer = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    cx, cy = s * 0.5, s * 0.5
    ring = s * 0.30
    hub_r = s * 0.105
    node_r = s * 0.058
    link_w = max(3, s // 60)
    nodes = []
    for i in range(6):
        a = math.radians(-90 + i * 60)
        nodes.append((cx + ring * math.cos(a), cy + ring * math.sin(a)))
    # faint outer ring joining the devices
    d.ellipse((cx - ring, cy - ring, cx + ring, cy + ring), outline=RING, width=max(2, s // 110))
    # spokes
    for x, y in nodes:
        d.line([(cx, cy), (x, y)], fill=LINK, width=link_w)
    # device nodes
    for x, y in nodes:
        d.ellipse((x - node_r, y - node_r, x + node_r, y + node_r), fill=NODE)
        d.ellipse((x - node_r * 0.45, y - node_r * 0.45, x + node_r * 0.45, y + node_r * 0.45), fill=TEAL)
    # hub
    d.ellipse((cx - hub_r, cy - hub_r, cx + hub_r, cy + hub_r), fill=NODE)
    d.ellipse((cx - hub_r * 0.55, cy - hub_r * 0.55, cx + hub_r * 0.55, cy + hub_r * 0.55), fill=ACCENT)
    return layer


def render_icon(size: int) -> Image.Image:
    """Round badge with the ecosystem graph."""
    scale = 4  # supersample for smooth edges
    s = size * scale
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = s * 0.03
    d.ellipse((pad, pad, s - pad, s - pad), fill=TEAL_DARK)
    inner = s * 0.075
    d.ellipse((inner, inner, s - inner, s - inner), fill=TEAL)
    img.alpha_composite(_ecosystem(s))
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
