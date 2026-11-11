"""Make assets/icon.ico (used by Pedio.spec). Run once: python scripts/make_icon.py"""
import os

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPolygon

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "assets", "icon.ico")


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    image = QImage(256, 256, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#3d6f9e"))
    painter.drawRoundedRect(8, 32, 240, 192, 40, 40)
    painter.setBrush(QColor("#ffffff"))
    painter.drawPolygon(QPolygon([QPoint(104, 80), QPoint(104, 176), QPoint(184, 128)]))
    painter.end()
    ok = image.save(OUT, "ICO")
    print(f"{'Wrote' if ok else 'Could not write'} {OUT}")


if __name__ == "__main__":
    main()
