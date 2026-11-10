import os
import tempfile
from functools import partial

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCursor, QKeySequence, QShortcut
from PySide6.QtWidgets import (QFileDialog, QMainWindow, QMenu, QMessageBox,
                               QSplitter, QStackedWidget, QVBoxLayout, QWidget)

from pedio.core.clips import Project
from pedio.core.export import AudioMixWorker
from pedio.core.transform import ASPECT_PRESETS
from pedio.core.workers import AudioProbeWorker, ProbeWorker
from pedio.ui.crop_editor import CropEditor
from pedio.ui.export_dialog import ExportDialog
from pedio.ui.panels.audio_panel import AudioPanel
from pedio.ui.player import AUDIO_EXTS, VIDEO_EXTS, PlayerWidget
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
        self._audio_queue = []         # (path, timeline start, src_in, src_out or None) waiting for the audio probe
        self._audio_probe = None       # the AudioProbeWorker that is running now
        self._mix_worker = None        # AudioMixWorker: builds the preview's sound file
        self._mix_generation = 0
        self._mix_path = ""
        self._mix_signature = None
        self._mix_timer = QTimer(self)  # debounce: one mix build after a burst of edits
        self._mix_timer.setSingleShot(True)
        self._mix_timer.setInterval(500)
        self._mix_timer.timeout.connect(self._start_audio_mix)
        self.audio_panel = None        # created on first use

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

    # ---- audio import (Add Audio) and extracting the sound of a video clip
    def import_audio(self):
        patterns = " ".join(f"*{ext}" for ext in sorted(AUDIO_EXTS))
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Add audio", "", f"Audio files ({patterns});;All files (*.*)")
        self.load_audio(paths)

    def load_audio(self, paths):
        start = self.timeline.position()
        for path in paths:
            self._audio_queue.append((path, start, 0.0, None))
        self._probe_audio_next()

    def extract_audio(self):
        """Put the sound of the selected video clip on the audio lane as its own clip."""
        clip = self._target_clip()
        if clip is None:
            self.statusBar().showMessage("Select a video clip first (click it on the timeline)")
            return
        i = self.project.index_of(clip.id)
        self._audio_queue.append((clip.path, self.project.starts()[i], clip.src_in, clip.src_out))
        self._probe_audio_next()

    def _probe_audio_next(self):
        if self._audio_probe is not None or not self._audio_queue:
            return
        path, start, src_in, src_out = self._audio_queue.pop(0)
        self.statusBar().showMessage(f"Reading the sound of {os.path.basename(path)} ...")
        worker = AudioProbeWorker(path, self)
        worker.done.connect(partial(self.on_audio_probe_done, start, src_in, src_out))
        worker.failed.connect(self.on_audio_probe_failed)
        worker.finished.connect(partial(self._audio_probe_finished, worker))
        self._audio_probe = worker
        worker.start()

    def _audio_probe_finished(self, worker):
        worker.deleteLater()
        if self._audio_probe is worker:
            self._audio_probe = None
        self._probe_audio_next()

    def on_audio_probe_done(self, start, src_in, src_out, path, duration, has_audio):
        end = duration if src_out is None else min(src_out, duration)
        src_in = min(max(src_in, 0.0), max(end - 0.1, 0.0))
        if end - src_in < 0.1:
            self.statusBar().showMessage(f"{os.path.basename(path)} is too short to use")
            return
        self.project.add_audio_clip(path, start, src_in, end)
        self.statusBar().showMessage(f"Added sound {os.path.basename(path)} ({end - src_in:.1f} s)")

    def on_audio_probe_failed(self, path, message):
        self.statusBar().showMessage(message)
        QMessageBox.warning(self, "Audio", message)

    def open_audio_panel(self):
        """Audio > Volume / Fade In-Out: edit the selected audio clip."""
        if self.audio_panel is None:
            self.audio_panel = AudioPanel(self.project, self)
        self.audio_panel.refresh()
        self.audio_panel.show()
        self.audio_panel.raise_()
        self.audio_panel.activateWindow()

    def toggle_audio_mute(self):
        clip = self.project.audio_by_id(self.project.selected_audio_id)
        if clip is None:
            self.statusBar().showMessage("Select an audio clip first (click it on the audio-clips lane)")
            return
        muted = not clip.mute
        self.project.set_audio_mute(clip.id, muted)
        self.statusBar().showMessage("Muted the sound" if muted else "Unmuted the sound")

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
        self._schedule_audio_mix()

    # ---- preview sound: one temporary mix of the video sound + the audio clips
    def _audio_signature(self):
        clips = tuple((c.path, round(c.src_in, 3), round(c.length, 3)) for c in self.project.play_list())
        extra = tuple((c.path, round(c.start, 3), round(c.src_in, 3), round(c.length, 3),
                       round(c.volume, 3), c.mute, round(c.fade_in, 3), round(c.fade_out, 3))
                      for c in self.project.audio_clips)
        return clips, extra

    def _schedule_audio_mix(self):
        """After an edit, rebuild the preview's sound once the edits stop for a moment."""
        if not self.project.clips:
            self.preview.set_audio_mix("")
            self._mix_signature = None
            return
        signature = self._audio_signature()
        if signature == self._mix_signature and self._mix_path:
            return
        self._mix_signature = signature
        self._mix_timer.start()

    def _start_audio_mix(self):
        if not self.project.clips:
            return
        self._mix_generation += 1
        path = os.path.join(tempfile.gettempdir(), f"pedio_mix_{os.getpid()}_{self._mix_generation}.m4a")
        old = self._mix_worker
        if old is not None and old.isRunning():
            old.stop()
            old.wait(3000)
        worker = AudioMixWorker(self.project.play_list(), self.project.audio_clips, path,
                                self._mix_generation, self)
        worker.ready.connect(self._on_mix_ready)
        worker.failed.connect(self._on_mix_failed)
        worker.finished.connect(self._on_mix_worker_finished)
        worker.finished.connect(worker.deleteLater)
        self._mix_worker = worker
        worker.start()

    def _on_mix_worker_finished(self):
        if self.sender() is self._mix_worker:
            self._mix_worker = None

    def _on_mix_ready(self, generation, path):
        if generation != self._mix_generation:
            self._remove_file(path)
            return
        old = self._mix_path
        self._mix_path = path
        self.preview.set_audio_mix(path)
        if old and old != path:
            self._remove_file(old)

    def _on_mix_failed(self, generation, message):
        if generation != self._mix_generation:
            return
        self._mix_signature = None        # try again after the next edit
        if self.project.audio_clips:
            self.statusBar().showMessage(f"Preview sound: {message}")

    @staticmethod
    def _remove_file(path):
        try:
            os.remove(path)
        except OSError:
            pass

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
        audio = self.project.audio_by_id(self.project.selected_audio_id)
        if audio is not None:
            self.project.remove_audio_clip(audio.id)
            self.statusBar().showMessage(f"Removed sound {os.path.basename(audio.path)}")
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
        dialog = ExportDialog(clips, path, parent=self, audio_clips=self.project.audio_clips)
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

    @staticmethod
    def dropped_audio(mime):
        paths = []
        for url in mime.urls():
            if url.isLocalFile():
                path = url.toLocalFile()
                if os.path.splitext(path)[1].lower() in AUDIO_EXTS:
                    paths.append(path)
        return paths

    def dragEnterEvent(self, event):
        if self.dropped_videos(event.mimeData()) or self.dropped_audio(event.mimeData()):
            event.acceptProposedAction()

    def dropEvent(self, event):
        videos = self.dropped_videos(event.mimeData())
        audio = self.dropped_audio(event.mimeData())
        if videos or audio:
            event.acceptProposedAction()
        if videos:
            self.load_videos(videos)
        if audio:
            self.load_audio(audio)

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
            "Add Audio": self.import_audio,
            "Extract Audio": self.extract_audio,
            "Volume": self.open_audio_panel,
            "Mute": self.toggle_audio_mute,
            "Fade In/Out": self.open_audio_panel,
            "Export Video": self.export_video,
        }
        action = actions.get(tool)
        if action:
            action()
        else:
            self.statusBar().showMessage(f"{category} > {tool} (not implemented yet)")

    def closeEvent(self, event):
        self._queue.clear()
        self._audio_queue.clear()
        if self._probe is not None:
            self._probe.wait(3000)
        if self._audio_probe is not None:
            self._audio_probe.wait(3000)
        self._mix_timer.stop()
        if self._mix_worker is not None and self._mix_worker.isRunning():
            self._mix_worker.stop()
            self._mix_worker.wait(3000)
        if self._mix_path:
            self._remove_file(self._mix_path)
        self.crop_editor.shutdown()
        self.timeline.shutdown()
        self.preview.shutdown()
        super().closeEvent(event)
