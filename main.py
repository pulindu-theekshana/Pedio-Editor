import sys

from pedio.paths import setup_paths

setup_paths()  # adds the mpv dll + ffmpeg to PATH (must run before `import mpv`)

from PySide6.QtWidgets import QApplication  # noqa: E402

from pedio.ui.main_window import MainWindow  # noqa: E402

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = MainWindow()
    win.show()
    sys.exit(app.exec())
