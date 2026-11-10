"""Undo / redo for every edit in `Project`.

Every Project edit emits `changed`. The history keeps the last known snapshot of the edit; when the
project changes it compares the new snapshot with that one and pushes the old snapshot on the undo
stack. One snapshot = `project_io.project_to_dict()` (the same data a save file stores), so new effects
are covered automatically when they are added to `project_io`.
"""
from PySide6.QtCore import QObject, Signal

from pedio.core.project_io import apply_project_dict, project_to_dict


class History(QObject):
    changed = Signal()          # undo / redo availability changed

    def __init__(self, project, limit=100, parent=None):
        super().__init__(parent)
        self.project = project
        self.limit = max(int(limit), 1)
        self._undo = []
        self._redo = []
        self._current = project_to_dict(project)
        self._blocked = False
        project.changed.connect(self._record)

    # ------------------------------------------------------------------ state
    def can_undo(self):
        return bool(self._undo)

    def can_redo(self):
        return bool(self._redo)

    def reset(self):
        """Forget the history (new / opened project)."""
        self._undo.clear()
        self._redo.clear()
        self._current = project_to_dict(self.project)
        self.changed.emit()

    def _record(self):
        if self._blocked:
            return
        snapshot = project_to_dict(self.project)
        if snapshot == self._current:
            return
        self._undo.append(self._current)
        if len(self._undo) > self.limit:
            self._undo.pop(0)
        self._redo.clear()
        self._current = snapshot
        self.changed.emit()

    def _restore(self, snapshot):
        self._blocked = True
        try:
            apply_project_dict(self.project, snapshot)
        finally:
            self._blocked = False
        self._current = snapshot
        self.changed.emit()

    # ------------------------------------------------------------------ actions
    def undo(self):
        if not self._undo:
            return False
        self._redo.append(self._current)
        self._restore(self._undo.pop())
        return True

    def redo(self):
        if not self._redo:
            return False
        self._undo.append(self._current)
        self._restore(self._redo.pop())
        return True
