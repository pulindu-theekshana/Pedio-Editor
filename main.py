import sys

from pedio.paths import setup_paths

setup_paths()  # adds the mpv dll + ffmpeg to PATH (must run before `import mpv`)

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap, QPolygon  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from pedio.ui.main_window import MainWindow  # noqa: E402


def app_icon():
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#3d6f9e"))
    painter.drawRoundedRect(2, 8, 60, 48, 10, 10)
    painter.setBrush(QColor("#ffffff"))
    painter.drawPolygon(QPolygon([QPoint(26, 20), QPoint(26, 44), QPoint(46, 32)]))
    painter.end()
    return QIcon(pixmap)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName("Pedio Editor")
    app.setStyle("Fusion")
    app.setWindowIcon(app_icon())
    win = MainWindow()
    win.show()
    sys.exit(app.exec())
