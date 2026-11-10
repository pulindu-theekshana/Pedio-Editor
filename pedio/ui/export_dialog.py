"""Progress window for an export: progress bar + Cancel. Modal, so the edit cannot change while it runs."""
import os

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QProgressBar,
                               QPushButton, QVBoxLayout)

from pedio.core.export import ExportWorker


class ExportDialog(QDialog):
    def __init__(self, clips, out_path, settings=None, parent=None, audio_clips=None):
        super().__init__(parent)
        self.setWindowTitle("Export")
        self.setModal(True)
        self.setMinimumWidth(440)
        self.out_path = out_path

        self.label = QLabel(f"Exporting to {os.path.basename(out_path)} ...")
        self.label.setWordWrap(True)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.folder_btn = QPushButton("Show in folder")
        self.folder_btn.clicked.connect(self.show_in_folder)
        self.folder_btn.hide()
        self.button = QPushButton("Cancel")
        self.button.clicked.connect(self.reject)    # reject() cancels while running, closes when finished

        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(self.folder_btn)
        buttons.addWidget(self.button)
        root = QVBoxLayout(self)
        root.addWidget(self.label)
        root.addWidget(self.bar)
        root.addLayout(buttons)

        self.worker = ExportWorker(clips, out_path, settings, self, audio_clips=audio_clips)
        self.worker.progress.connect(self.on_progress)
        self.worker.done.connect(self.on_done)
        self.worker.failed.connect(self.on_failed)
        self.worker.cancelled.connect(self.on_cancelled)
        self._ended = False       # True once done / failed / cancelled has been reported
        self.succeeded = False    # True when the file was written

    def showEvent(self, event):
        super().showEvent(event)
        if not self.worker.isRunning() and not self._ended:
            self.worker.start()

    # ---- worker signals
    def on_progress(self, fraction):
        self.bar.setValue(round(fraction * 100))

    def on_done(self, path):
        self.succeeded = True
        self.bar.setValue(100)
        self.label.setText(f"Done.\n{path}")
        self._finish("Close")
        self.folder_btn.show()

    def on_failed(self, message):
        self.label.setText(message)
        self._finish("Close")

    def on_cancelled(self):
        self.label.setText("Export cancelled.")
        self._finish("Close")
        self.reject()                                # nothing to show: close the window

    def _finish(self, button_text):
        self._ended = True
        self.button.setEnabled(True)
        self.button.setText(button_text)

    # ---- buttons / closing
    def show_in_folder(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(os.path.abspath(self.out_path))))

    def reject(self):
        """Cancel button, Esc and the window's X: stop a running export first, then close."""
        if self.worker.isRunning() and not self._ended:
            self.button.setEnabled(False)
            self.label.setText("Cancelling ...")
            self.worker.stop()                       # on_cancelled() closes the window when ffmpeg has stopped
            return
        super().reject()

    def done(self, result):
        if self.worker.isRunning():                  # never destroy a running thread
            self.worker.stop()
            self.worker.wait(5000)
        super().done(result)
