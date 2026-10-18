# -*- coding: utf-8 -*-
"""Сборка активов из assets/start.svg:
   - assets/icon.ico  (многоразмерная иконка для exe и ярлыка автозагрузки)
   - assets/preview_256.png (превью)
Запуск:  venv\\Scripts\\python build_icon.py
"""
import os
import sys

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
SVG = os.path.join(HERE, "assets", "start.svg")
ICO = os.path.join(HERE, "assets", "icon.ico")
PNG = os.path.join(HERE, "assets", "preview_256.png")


def qimage_to_pil(img: QImage) -> Image.Image:
    img = img.convertToFormat(QImage.Format.Format_RGBA8888)
    buf = bytes(img.constBits())
    return Image.frombytes("RGBA", (img.width(), img.height()), buf, "raw", "RGBA")


def render(size: int) -> Image.Image:
    renderer = QSvgRenderer(SVG)
    if not renderer.isValid():
        raise SystemExit("SVG не открывается: " + SVG)
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(p, QRectF(0, 0, size, size))
    p.end()
    return qimage_to_pil(img)


def main() -> int:
    app = QGuiApplication.instance() or QGuiApplication(sys.argv)  # noqa: F841
    big = render(256)
    big.save(PNG)
    big.save(ICO, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("OK:", PNG)
    print("OK:", ICO, os.path.getsize(ICO), "bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
