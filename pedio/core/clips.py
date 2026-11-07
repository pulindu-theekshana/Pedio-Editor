"""The edit itself: clips joined end to end on one timeline. No widgets here (Qt signals only)."""
from dataclasses import dataclass, field
from itertools import count

from PySide6.QtCore import QObject, Signal

MIN_CLIP = 0.1  # shortest clip allowed, in seconds

_next_id = count(1)


@dataclass
class Clip:
    path: str
    src_duration: float   # length of the whole source file
    src_in: float         # where the clip starts inside the source file
    src_out: float        # where the clip ends inside the source file
    id: int = field(default_factory=lambda: next(_next_id))

    @property
    def length(self):
        return self.src_out - self.src_in


class Project(QObject):
    """List of clips (timeline order) + the selected clip. Every edit goes through a method here."""

    changed = Signal()            # clips were added / split / trimmed / deleted / moved
    selection_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.clips = []
        self.selected_id = 0      # 0 = nothing selected

    # ------------------------------------------------------------------ reading
    def total(self):
        return sum(c.length for c in self.clips)

    def starts(self):
        """Timeline start time of every clip."""
        out, t = [], 0.0
        for c in self.clips:
            out.append(t)
            t += c.length
        return out

    def boundaries(self):
        """Times where a clip starts or ends: 0, then the end of every clip."""
        out, t = [0.0], 0.0
        for c in self.clips:
            t += c.length
            out.append(t)
        return out

    def index_of(self, clip_id):
        for i, c in enumerate(self.clips):
            if c.id == clip_id:
                return i
        return -1

    def clip_at(self, t):
        """(index, seconds into that clip) for timeline time t, or (-1, 0.0) if no clip is there."""
        start = 0.0
        for i, c in enumerate(self.clips):
            if start <= t < start + c.length:
                return i, t - start
            start += c.length
        return -1, 0.0

    def play_list(self):
        """[(path, start, length)] for the player. Neighbours that continue each other are merged,
        so a plain split does not change this list (and the player does not have to reload)."""
        out = []
        for c in self.clips:
            if out and out[-1][0] == c.path and abs(out[-1][1] + out[-1][2] - c.src_in) < 1e-6:
                path, start, length = out[-1]
                out[-1] = (path, start, length + c.length)
            else:
                out.append((c.path, c.src_in, c.length))
        return out

    # ------------------------------------------------------------------ selection
    def select(self, clip_id):
        if clip_id and self.index_of(clip_id) < 0:
            clip_id = 0
        if clip_id != self.selected_id:
            self.selected_id = clip_id
            self.selection_changed.emit()

    # ------------------------------------------------------------------ edits (each returns True if something changed)
    def add_clip(self, path, duration):
        clip = Clip(path, duration, 0.0, duration)
        self.clips.append(clip)
        self.changed.emit()
        return clip

    def split(self, t):
        """Cut the clip under timeline time t in two."""
        i, offset = self.clip_at(t)
        if i < 0:
            return False
        c = self.clips[i]
        if offset < MIN_CLIP or c.length - offset < MIN_CLIP:
            return False
        cut = c.src_in + offset
        self.clips.insert(i + 1, Clip(c.path, c.src_duration, cut, c.src_out))
        c.src_out = cut
        self.changed.emit()
        return True

    def delete(self, clip_id):
        """Remove a clip; the clips after it move left (no gaps)."""
        i = self.index_of(clip_id)
        if i < 0:
            return False
        del self.clips[i]
        if self.selected_id == clip_id:
            self.selected_id = 0
            self.selection_changed.emit()
        self.changed.emit()
        return True

    def trim_start(self, clip_id, new_in):
        i = self.index_of(clip_id)
        if i < 0:
            return False
        c = self.clips[i]
        new_in = min(max(new_in, 0.0), c.src_out - MIN_CLIP)
        if abs(new_in - c.src_in) < 1e-6:
            return False
        c.src_in = new_in
        self.changed.emit()
        return True

    def trim_end(self, clip_id, new_out):
        i = self.index_of(clip_id)
        if i < 0:
            return False
        c = self.clips[i]
        new_out = min(max(new_out, c.src_in + MIN_CLIP), c.src_duration)
        if abs(new_out - c.src_out) < 1e-6:
            return False
        c.src_out = new_out
        self.changed.emit()
        return True

    def trim_start_at(self, t):
        """Cut the beginning of the clip under t, up to t. Returns where the playhead should go, or None."""
        i, offset = self.clip_at(t)
        if i < 0:
            return None
        c = self.clips[i]
        if offset < 0.001 or c.length - offset < MIN_CLIP:
            return None
        start = t - offset
        return start if self.trim_start(c.id, c.src_in + offset) else None

    def trim_end_at(self, t):
        """Cut the end of the clip under t, from t on. Returns where the playhead should go, or None."""
        i, offset = self.clip_at(t)
        if i < 0:
            return None
        c = self.clips[i]
        if offset < MIN_CLIP:
            return None
        return t if self.trim_end(c.id, c.src_in + offset) else None

    def move(self, clip_id, new_index):
        """Put a clip at new_index (its index in the list after the move)."""
        i = self.index_of(clip_id)
        if i < 0:
            return False
        new_index = min(max(new_index, 0), len(self.clips) - 1)
        if new_index == i:
            return False
        self.clips.insert(new_index, self.clips.pop(i))
        self.changed.emit()
        return True
