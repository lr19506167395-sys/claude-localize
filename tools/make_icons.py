#!/usr/bin/env python3
"""生成简洁的菜单栏 template 图标与应用图标。"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "resources/icons"
OUT.mkdir(parents=True, exist_ok=True)


def font(size: int, bold: bool = True):
    candidates = [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
    ]
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size=size, index=0)
    return ImageFont.load_default()


def template(size: int, busy: bool = False):
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    c = (0, 0, 0, 255)
    m = max(1, size // 12)
    r = size // 5
    d.rounded_rectangle((m, m, size - m, size - m - size // 8), radius=r, outline=c, width=max(1, size // 15))
    f = font(int(size * 0.52))
    box = d.textbbox((0, 0), "中", font=f)
    tw, th = box[2] - box[0], box[3] - box[1]
    d.text(((size - tw) / 2, (size - th) / 2 - box[1] - size * 0.06), "中", font=f, fill=c)
    if busy:
        d.ellipse((size * 0.65, size * 0.68, size * 0.94, size * 0.97), fill=(255, 255, 255, 255))
        d.ellipse((size * 0.70, size * 0.73, size * 0.89, size * 0.92), fill=c)
    return im


for name, size in [("menubar_22", 22), ("menubar_44", 44), ("menubar_66", 66)]:
    template(size).save(OUT / f"{name}_template.png")
    template(size, True).save(OUT / f"{name}_busy_template.png")

base = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
d = ImageDraw.Draw(base)
d.rounded_rectangle((0, 0, 1023, 1023), radius=225, fill=(38, 64, 84, 255))
d.rounded_rectangle((120, 150, 904, 800), radius=150, fill=(247, 243, 231, 255))
f = font(490)
box = d.textbbox((0, 0), "中", font=f)
tw, th = box[2] - box[0], box[3] - box[1]
d.text(((1024 - tw) / 2, (1024 - th) / 2 - box[1] - 20), "中", font=f, fill=(38, 64, 84, 255))
d.rounded_rectangle((280, 840, 744, 885), radius=20, fill=(247, 243, 231, 255))

iconset = ROOT / "build/AppIcon.iconset"
iconset.mkdir(parents=True, exist_ok=True)
for size in (16, 32, 64, 128, 256, 512, 1024):
    base.resize((size, size), Image.Resampling.LANCZOS).save(iconset / f"icon_{size}x{size}.png")
for size in (16, 32, 128, 256, 512):
    base.resize((size * 2, size * 2), Image.Resampling.LANCZOS).save(iconset / f"icon_{size}x{size}@2x.png")
print(f"图标已生成：{OUT}")
