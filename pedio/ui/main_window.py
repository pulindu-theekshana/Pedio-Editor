import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QFileDialog, QMainWindow, QMessageBox,
                               QSplitter, QVBoxLayout, QWidget)

from pedio.ui.player import VIDEO_EXTS, PlayerWidget
from pedio.ui.timeline import TimelinePanel
from pedio.ui.toolbar import BottomToolbar


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Pedio Editor")
        self.resize(1200, 750)
        self.setAcceptDrops(True)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # preview area (mpv player with play/pause, seek bar, volume)
        self.preview = PlayerWidget()
        self.preview.file_loaded.connect(self.on_file_loaded)
        self.preview.load_failed.connect(self.on_load_failed)

        # timeline area (ruler, video thumbnails, audio waveform, playhead, zoom)
        self.timeline = TimelinePanel(self.preview)

        splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(self.preview)
        splitter.addWidget(self.timeline)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        root.addWidget(splitter, 1)

        # bottom toolbar
        self.toolbar = BottomToolbar()
        self.toolbar.tool_clicked.connect(self.on_tool)
        root.addWidget(self.toolbar)

        QShortcut(QKeySequence(QKeySequence.StandardKey.Open), self, activated=self.import_video)  # Ctrl+O
        self.statusBar().showMessage("Ready")

    # ---- import
    def import_video(self):
        patterns = " ".join(f"*{ext}" for ext in sorted(VIDEO_EXTS))
        path, _ = QFileDialog.getOpenFileName(
            self, "Import video", "", f"Video files ({patterns});;All files (*.*)")
        if path:
            self.load_video(path)

    def load_video(self, path):
        self.statusBar().showMessage(f"Loading {os.path.basename(path)} ...")
        self.preview.load(path)

    def on_file_loaded(self, path):
        self.setWindowTitle(f"{os.path.basename(path)} - Pedio Editor")
        self.statusBar().showMessage(f"Loaded: {path}")

    def on_load_failed(self, message):
        self.statusBar().showMessage(message)
        QMessageBox.warning(self, "Import failed", message)

    # ---- drag and drop
    @staticmethod
    def dropped_video(mime):
        for url in mime.urls():
            if url.isLocalFile():
                path = url.toLocalFile()
                if os.path.splitext(path)[1].lower() in VIDEO_EXTS:
                    return path
        return None

    def dragEnterEvent(self, event):
        if self.dropped_video(event.mimeData()):
            event.acceptProposedAction()

    def dropEvent(self, event):
        path = self.dropped_video(event.mimeData())
        if path:
            event.acceptProposedAction()
            self.load_video(path)

    # ---- toolbar
    def on_tool(self, category, tool):
        if tool == "Import":
            self.import_video()
        else:
            self.statusBar().showMessage(f"{category} > {tool} (not implemented yet)")

    def closeEvent(self, event):
        self.timeline.shutdown()
        self.preview.shutdown()
        super().closeEvent(event)
