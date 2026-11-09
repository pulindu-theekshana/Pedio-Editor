import os
from functools import partial

from PySide6.QtCore import Qt
from PySide6.QtGui import QCursor, QKeySequence, QShortcut
from PySide6.QtWidgets import (QFileDialog, QMainWindow, QMenu, QMessageBox,
                               QSplitter, QStackedWidget, QVBoxLayout, QWidget)

from pedio.core.clips import Project
from pedio.core.transform import ASPECT_PRESETS
from pedio.core.workers import ProbeWorker
from pedio.ui.crop_editor import CropEditor
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

        # crop editor: takes the place of the preview while the crop box is open (Video > Crop)
        self.crop_editor = CropEditor()
        self.crop_editor.applied.connect(self.on_crop_applied)
        self.crop_editor.cancelled.connect(self.close_crop_editor)
        self.top = QStackedWidget()
        self.top.addWidget(self.preview)
        self.top.addWidget(self.crop_editor)

        # timeline area (ruler, clips, playhead, zoom, trim handles, drag to reorder)
        self.timeline = TimelinePanel(self.preview, self.project)
        self.project.changed.connect(self.on_project_changed)   # connected after the timeline's own handlers

        splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(self.top)
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
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, activated=self.crop_shortcut_cancel)
        QShortcut(QKeySequence(Qt.Key.Key_Return), self, activated=self.crop_shortcut_done)
        QShortcut(QKeySequence(Qt.Key.Key_Enter), self, activated=self.crop_shortcut_done)
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

    def on_probe_done(self, path, duration, width, height, rotation):
        self.project.add_clip(path, duration, (width, height), rotation)
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
    def crop_open(self):
        return self.top.currentWidget() is self.crop_editor

    def split_at_playhead(self):
        if self.crop_open():
            return
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
        if self.crop_open():
            return
        clip = self._target_clip()
        if clip is None:
            self.statusBar().showMessage("Select a clip first (click it on the timeline)")
            return
        self.project.delete(clip.id)
        self.statusBar().showMessage(f"Deleted {os.path.basename(clip.path)} ({clip.length:.1f} s)")

    def trim_start_to_playhead(self):
        if self.crop_open():
            return
        start = self.project.trim_start_at(self.timeline.position())
        if start is None:
            self.statusBar().showMessage("Move the playhead inside a clip to trim its start")
        else:
            self.timeline.set_playhead(start)   # the playhead shows the same picture as before
            self.statusBar().showMessage("Trimmed the start of the clip")

    def trim_end_to_playhead(self):
        if self.crop_open():
            return
        if self.project.trim_end_at(self.timeline.position()) is None:
            self.statusBar().showMessage("Move the playhead inside a clip to trim its end")
        else:
            self.statusBar().showMessage("Trimmed the end of the clip")

    # ---- picture: rotate, flip, aspect ratio, crop (the clip's Transform, see core/transform.py)
    def rotate_clip(self):
        clip = self._target_clip()
        if clip is None:
            self.statusBar().showMessage("Select a clip first (click it on the timeline)")
        elif self.project.rotate(clip.id):
            self.statusBar().showMessage("Rotated the clip 90 degrees clockwise")

    def flip_clip(self, horizontal):
        clip = self._target_clip()
        if clip is None:
            self.statusBar().showMessage("Select a clip first (click it on the timeline)")
        elif self.project.flip(clip.id, horizontal):
            self.statusBar().showMessage("Flipped the clip " + ("left-right" if horizontal else "top-bottom"))

    def choose_aspect(self):
        """Video > Aspect Ratio: a list of ratios under the mouse. The choice is applied to every clip."""
        if not self.project.clips:
            self.statusBar().showMessage("Import a video first")
            return
        menu = QMenu(self)
        menu.addAction("Original (no crop)").setData("")
        menu.addSeparator()
        for name in ASPECT_PRESETS:
            menu.addAction(name).setData(name)
        action = menu.exec(QCursor.pos())
        if action is not None:
            self.set_aspect(action.data())

    def set_aspect(self, name):
        """name = a key of ASPECT_PRESETS, or "" for no crop. Every clip gets a centered crop with that ratio."""
        if self.project.set_aspect_all(ASPECT_PRESETS.get(name)):
            self.statusBar().showMessage(
                f"Aspect ratio {name} applied to all clips. Use Crop to move the box on a clip" if name
                else "Crops removed from all clips")
        else:
            self.statusBar().showMessage("Nothing changed")

    def start_crop(self):
        """Video > Crop: the preview turns into the crop editor for the selected clip (or the one under the playhead)."""
        if self.crop_open():
            return
        clip = self._target_clip()
        if clip is None:
            self.statusBar().showMessage("Select a clip first (click it on the timeline)")
            return
        i, offset = self.project.clip_at(self.timeline.position())
        inside = i >= 0 and self.project.clips[i] is clip
        self.preview.pause()
        self.crop_editor.start(clip, clip.src_in + (offset if inside else clip.length / 2))
        self.top.setCurrentWidget(self.crop_editor)
        self.toolbar.setEnabled(False)
        self.timeline.setEnabled(False)
        self.statusBar().showMessage("Crop: set the box, then click Done (Enter). Cancel = Esc")

    def close_crop_editor(self):
        self.top.setCurrentWidget(self.preview)
        self.toolbar.setEnabled(True)
        self.timeline.setEnabled(True)
        self.preview.refresh()
        self.statusBar().showMessage("Ready")

    def on_crop_applied(self, rect):
        clip_id = self.crop_editor.clip_id
        self.close_crop_editor()
        if self.project.set_crop(clip_id, rect):
            self.statusBar().showMessage("Cropped the clip" if rect else "Crop removed from the clip")

    def crop_shortcut_cancel(self):
        if self.crop_open():
            self.close_crop_editor()

    def crop_shortcut_done(self):
        if self.crop_open() and self.crop_editor.done_btn.isEnabled():
            self.crop_editor.done_btn.click()

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
            "Crop": self.start_crop,
            "Aspect Ratio": self.choose_aspect,
            "Rotate": self.rotate_clip,
            "Flip H": lambda: self.flip_clip(True),
            "Flip V": lambda: self.flip_clip(False),
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
        self.crop_editor.shutdown()
        self.timeline.shutdown()
        self.preview.shutdown()
        super().closeEvent(event)
