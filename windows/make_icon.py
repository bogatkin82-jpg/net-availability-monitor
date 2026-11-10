#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Генератор windows/icon.ico для NetMonitor (Pillow).

Иконка: тёмный скруглённый квадрат + зелёная «точка статуса» с ореолом.
Запускается в CI и локально перед сборкой:

    python windows/make_icon.py

Требует Pillow (единственная зависимость; ставится для сборки exe).
Готовый icon.ico можно закоммитить — тогда генерация не обязательна.
"""

import os
import sys

ICON_SIZES = [(16, 16), (20, 20), (24, 24), (32, 32), (40, 40),
              (48, 48), (64, 64), (128, 128), (256, 256)]

BACKGROUND = (20, 22, 26, 255)    # #14161a — тёмный фон
DOT_COLOR = (52, 199, 89, 255)    # #34c759 — зелёный «всё работает»
HALO_COLOR = (52, 199, 89, 64)    # ореол вокруг точки


def build_icon(size=1024):
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    # Фон: скруглённый квадрат с небольшим отступом.
    margin = int(size * 0.04)
    radius = int(size * 0.22)
    draw.rounded_rectangle(
        [margin, margin, size - margin - 1, size - margin - 1],
        radius=radius, fill=BACKGROUND)

    center = size // 2
    # Ореол-«пульс» вокруг точки.
    halo = int(size * 0.30)
    draw.ellipse([center - halo, center - halo, center + halo, center + halo],
                 fill=HALO_COLOR)
    # Основная точка статуса.
    dot = int(size * 0.20)
    draw.ellipse([center - dot, center - dot, center + dot, center + dot],
                 fill=DOT_COLOR)
    # Блик, чтобы точка не выглядела плоской.
    highlight = int(size * 0.055)
    hx = center - int(size * 0.075)
    hy = center - int(size * 0.075)
    draw.ellipse([hx - highlight, hy - highlight, hx + highlight, hy + highlight],
                 fill=(255, 255, 255, 70))
    return image


def main():
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        sys.stderr.write("Для генерации icon.ico нужен Pillow: pip install pillow\n")
        return 1
    output_dir = os.path.dirname(os.path.abspath(__file__))
    ico_path = os.path.join(output_dir, "icon.ico")
    png_path = os.path.join(output_dir, "icon.png")
    image = build_icon(1024)
    image.save(ico_path, format="ICO", sizes=ICON_SIZES)
    image.resize((256, 256)).save(png_path, format="PNG")
    print("Готово: %s (%d размеров) и %s" % (ico_path, len(ICON_SIZES), png_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
