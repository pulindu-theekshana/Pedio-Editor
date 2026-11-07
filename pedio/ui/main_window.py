import os
from functools import partial

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QFileDialog, QMainWindow, QMessageBox,
                               QSplitter, QVBoxLayout, QWidget)

from pedio.core.clips import Project
from pedio.core.workers import ProbeWorker
from pedio.ui.export_dialog import ExportDialog
from pedio.ui.player import VIDEO_EXTS, PlayerWidget
from pedio.ui.timeline import TimelinePanel
from pedio.ui.toolbar import BottomToolbar


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Pedio Editor")
        self.resize(1200, 750)
        self.setAcceptDrops(True)

        self.project = Project(self)   # the clips on the timeline
        self._queue = []               # files waiting to be read (imported one after another, in order)
        self._probe = None             # the ProbeWorker that is running now

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # preview area (mpv plays the whole timeline: all clips one after another)
        self.preview = PlayerWidget()
        self.preview.load_failed.connect(self.on_load_failed)

        # timeline area (ruler, clips, playhead, zoom, trim handles, drag to reorder)
        self.timeline = TimelinePanel(self.preview, self.project)
        self.project.changed.connect(self.on_project_changed)   # connected after the timeline's own handlers

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
        # Space (play / pause) is in PlayerWidget
        QShortcut(QKeySequence(Qt.Key.Key_S), self, activated=self.split_at_playhead)
        QShortcut(QKeySequence(Qt.Key.Key_Delete), self, activated=self.delete_clip)
        QShortcut(QKeySequence(Qt.Key.Key_BracketLeft), self, activated=self.trim_start_to_playhead)
        QShortcut(QKeySequence(Qt.Key.Key_BracketRight), self, activated=self.trim_end_to_playhead)
        self.statusBar().showMessage("Ready")

    # ---- import (several files are added one after another at the end of the timeline)
    def import_video(self):
        patterns = " ".join(f"*{ext}" for ext in sorted(VIDEO_EXTS))
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Import video", "", f"Video files ({patterns});;All files (*.*)")
        self.load_videos(paths)

    def load_videos(self, paths):
        self._queue.extend(paths)
        self._probe_next()

    def _probe_next(self):
        if self._probe is not None or not self._queue:
            return
        path = self._queue.pop(0)
        self.statusBar().showMessage(f"Reading {os.path.basename(path)} ...")
        worker = ProbeWorker(path, self)
        worker.done.connect(self.on_probe_done)
        worker.failed.connect(self.on_probe_failed)
        worker.finished.connect(partial(self._probe_finished, worker))
        self._probe = worker
        worker.start()

    def _probe_finished(self, worker):
        worker.deleteLater()
        if self._probe is worker:
            self._probe = None
        self._probe_next()

    def on_probe_done(self, path, duration):
        self.project.add_clip(path, duration)
        self.statusBar().showMessage(f"Added {os.path.basename(path)} ({duration:.1f} s)")

    def on_probe_failed(self, path, message):
        self.on_load_failed(message)

    def on_load_failed(self, message):
        self.statusBar().showMessage(message)
        QMessageBox.warning(self, "Import failed", message)

    def on_project_changed(self):
        """Any edit: give the player the new clip list (it keeps the playhead position)."""
        self.preview.set_edit_list(self.project.play_list(), self.timeline.position())
        count = len(self.project.clips)
        if count == 0:
            self.setWindowTitle("Pedio Editor")
        elif count == 1:
            self.setWindowTitle(f"{os.path.basename(self.project.clips[0].path)} - Pedio Editor")
        else:
            self.setWindowTitle(f"{count} clips - Pedio Editor")

    # ---- editing
    def split_at_playhead(self):
        if self.project.split(self.timeline.position()):
            self.statusBar().showMessage("Split at the playhead")
        else:
            self.statusBar().showMessage("Move the playhead inside a clip (0.1 s or more from its edges) to split")

    def _target_clip(self):
        """The selected clip, or else the clip under the playhead."""
        i = self.project.index_of(self.project.selected_id)
        if i < 0:
            i, _ = self.project.clip_at(self.timeline.position())
        return self.project.clips[i] if i >= 0 else None

    def delete_clip(self):
        clip = self._target_clip()
        if clip is None:
            self.statusBar().showMessage("Select a clip first (click it on the timeline)")
            return
        self.project.delete(clip.id)
        self.statusBar().showMessage(f"Deleted {os.path.basename(clip.path)} ({clip.length:.1f} s)")

    def trim_start_to_playhead(self):
        start = self.project.trim_start_at(self.timeline.position())
        if start is None:
            self.statusBar().showMessage("Move the playhead inside a clip to trim its start")
        else:
            self.timeline.set_playhead(start)   # the playhead shows the same picture as before
            self.statusBar().showMessage("Trimmed the start of the clip")

    def trim_end_to_playhead(self):
        if self.project.trim_end_at(self.timeline.position()) is None:
            self.statusBar().showMessage("Move the playhead inside a clip to trim its end")
        else:
            self.statusBar().showMessage("Trimmed the end of the clip")

    # ---- export (the same clip list the player plays)
    def export_video(self):
        clips = self.project.play_list()
        if not clips:
            self.statusBar().showMessage("Nothing to export. Import a video first")
            return
        first = self.project.clips[0].path
        default = os.path.splitext(first)[0] + "_edit.mp4"
        path, _ = QFileDialog.getSaveFileName(self, "Export video", default, "MP4 video (*.mp4)")
        if not path:
            return
        if not path.lower().endswith(".mp4"):
            path += ".mp4"
        same = os.path.normcase(os.path.abspath(path))
        if any(os.path.normcase(os.path.abspath(c[0])) == same for c in clips):
            QMessageBox.warning(self, "Export", "This file is used by the edit. Choose another file name.")
            return
        dialog = ExportDialog(clips, path, parent=self)
        dialog.exec()
        if dialog.succeeded:
            self.statusBar().showMessage(f"Exported {os.path.basename(path)}")
        dialog.deleteLater()

    # ---- drag and drop
    @staticmethod
    def dropped_videos(mime):
        paths = []
        for url in mime.urls():
            if url.isLocalFile():
                path = url.toLocalFile()
                if os.path.splitext(path)[1].lower() in VIDEO_EXTS:
                    paths.append(path)
        return paths

    def dragEnterEvent(self, event):
        if self.dropped_videos(event.mimeData()):
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = self.dropped_videos(event.mimeData())
        if paths:
            event.acceptProposedAction()
            self.load_videos(paths)

    # ---- toolbar
    def on_tool(self, category, tool):
        actions = {
            "Import": self.import_video,
            "Trim Start": self.trim_start_to_playhead,
            "Trim End": self.trim_end_to_playhead,
            "Split": self.split_at_playhead,
            "Delete": self.delete_clip,
            "Export Video": self.export_video,
        }
        action = actions.get(tool)
        if action:
            action()
        else:
            self.statusBar().showMessage(f"{category} > {tool} (not implemented yet)")

    def closeEvent(self, event):
        self._queue.clear()
        if self._probe is not None:
            self._probe.wait(3000)
        self.timeline.shutdown()
        self.preview.shutdown()
        super().closeEvent(event)
