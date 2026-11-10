"""The edit itself: clips joined end to end on one timeline. No widgets here (Qt signals only)."""
from dataclasses import dataclass, field, replace
from itertools import count
from typing import NamedTuple

from PySide6.QtCore import QObject, Signal

from pedio.core.audio import AudioClip
from pedio.core.layers import TextLayer, normalize_color
from pedio.core.transform import Transform, centered_crop

MIN_CLIP = 0.1  # shortest clip allowed, in seconds

_next_id = count(1)


@dataclass
class Clip:
    path: str
    src_duration: float   # length of the whole source file
    src_in: float         # where the clip starts inside the source file
    src_out: float        # where the clip ends inside the source file
    id: int = field(default_factory=lambda: next(_next_id))
    size: tuple = (0, 0)  # picture size as a player shows it (phone rotation applied); (0, 0) = unknown
    meta_rotation: int = 0           # clockwise turn the file asks for in its metadata (phone videos)
    transform: Transform = Transform()   # mirror / rotate / crop chosen by the user
    fade_in: float = 0.0             # seconds of fade from black at the clip start (0 .. 2)
    fade_out: float = 0.0            # seconds of fade to black at the clip end (0 .. 2)

    @property
    def length(self):
        return self.src_out - self.src_in

    @property
    def picture_size(self):
        """Size of the picture after mirror + rotate, before the crop. (0, 0) if unknown."""
        w, h = self.size
        return (h, w) if self.transform.rotate % 180 == 90 else (w, h)


class Segment(NamedTuple):
    """One piece of the edit for the player and the export: a part of a file + how to show it."""
    path: str
    start: float          # seconds into the file
    length: float
    transform: Transform = Transform()
    meta_rotation: int = 0
    fade_in: float = 0.0
    fade_out: float = 0.0


class Project(QObject):
    """List of clips (timeline order) + the selected clip. Every edit goes through a method here."""

    changed = Signal()            # clips were added / split / trimmed / deleted / moved
    selection_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.clips = []
        self.audio_clips = []
        self.text_layers = []
        self.selected_id = 0        # 0 = no video clip selected
        self.selected_audio_id = 0  # 0 = no audio clip selected
        self.selected_text_id = 0   # 0 = no text layer selected

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
        """[Segment] for the player and the export. Neighbours that continue each other (same file,
        same transform, no fade between them) are merged, so a plain split does not change this list."""
        out = []
        for c in self.clips:
            last = out[-1] if out else None
            if (last and last.path == c.path and abs(last.start + last.length - c.src_in) < 1e-6
                    and last.transform == c.transform and abs(last.fade_out) < 1e-6
                    and abs(c.fade_in) < 1e-6):
                out[-1] = last._replace(length=last.length + c.length, fade_out=c.fade_out)
            else:
                out.append(Segment(c.path, c.src_in, c.length, c.transform, c.meta_rotation,
                                   c.fade_in, c.fade_out))
        return out

    # ------------------------------------------------------------------ audio reading
    def audio_index_of(self, audio_id):
        for i, c in enumerate(self.audio_clips):
            if c.id == audio_id:
                return i
        return -1

    def audio_by_id(self, audio_id):
        i = self.audio_index_of(audio_id)
        return self.audio_clips[i] if i >= 0 else None

    def extent(self):
        """Length of the whole edit, including audio / text that reach past the last video clip."""
        end = self.total()
        for c in self.audio_clips:
            end = max(end, c.end)
        for layer in self.text_layers:
            end = max(end, layer.end)
        return end

    # ------------------------------------------------------------------ text layers
    def text_index_of(self, layer_id):
        for i, layer in enumerate(self.text_layers):
            if layer.id == layer_id:
                return i
        return -1

    def text_by_id(self, layer_id):
        i = self.text_index_of(layer_id)
        return self.text_layers[i] if i >= 0 else None

    # ------------------------------------------------------------------ selection
    def select(self, clip_id):
        if clip_id and self.index_of(clip_id) < 0:
            clip_id = 0
        if clip_id != self.selected_id:
            self.selected_id = clip_id
            self.selected_audio_id = 0
            self.selected_text_id = 0
            self.selection_changed.emit()

    def select_audio(self, audio_id):
        if audio_id and self.audio_index_of(audio_id) < 0:
            audio_id = 0
        if audio_id != self.selected_audio_id:
            self.selected_audio_id = audio_id
            self.selected_id = 0
            self.selected_text_id = 0
            self.selection_changed.emit()

    def select_text(self, layer_id):
        if layer_id and self.text_index_of(layer_id) < 0:
            layer_id = 0
        if layer_id != self.selected_text_id:
            self.selected_text_id = layer_id
            self.selected_id = 0
            self.selected_audio_id = 0
            self.selection_changed.emit()

    def clear_selection(self):
        if self.selected_id or self.selected_audio_id or self.selected_text_id:
            self.selected_id = 0
            self.selected_audio_id = 0
            self.selected_text_id = 0
            self.selection_changed.emit()

    # ------------------------------------------------------------------ edits (each returns True if something changed)
    def add_clip(self, path, duration, size=(0, 0), meta_rotation=0):
        clip = Clip(path, duration, 0.0, duration, size=tuple(size), meta_rotation=meta_rotation)
        self.clips.append(clip)
        self.changed.emit()
        return clip

    def add_audio_clip(self, path, start, src_in, src_out, volume=1.0, mute=False):
        """Place a range of an audio file on the timeline. Returns the new AudioClip."""
        clip = AudioClip(path, float(start), float(src_in), float(src_out), float(volume), bool(mute))
        self.audio_clips.append(clip)
        self.changed.emit()
        return clip

    def remove_audio_clip(self, audio_id):
        """Remove an audio clip by id. Returns True when a clip was removed."""
        i = self.audio_index_of(audio_id)
        if i < 0:
            return False
        del self.audio_clips[i]
        if self.selected_audio_id == audio_id:
            self.selected_audio_id = 0
            self.selection_changed.emit()
        self.changed.emit()
        return True

    def add_text_layer(self, text="Text", start=0.0, end=None, x=0.5, y=0.5, size=64,
                       color="#ffffff", outline=3, outline_color="#000000", shadow=2,
                       shadow_color="#000000"):
        """Add a text overlay over the timeline. end defaults to start + 3 s."""
        start = max(float(start), 0.0)
        end = start + 3.0 if end is None else float(end)
        layer = TextLayer(str(text), start, max(end, start + MIN_CLIP), float(x), float(y),
                          int(size), normalize_color(color), int(outline),
                          normalize_color(outline_color), int(shadow),
                          normalize_color(shadow_color))
        self.text_layers.append(layer)
        self.changed.emit()
        return layer

    def remove_text_layer(self, layer_id):
        i = self.text_index_of(layer_id)
        if i < 0:
            return False
        del self.text_layers[i]
        if self.selected_text_id == layer_id:
            self.selected_text_id = 0
            self.selection_changed.emit()
        self.changed.emit()
        return True

    def update_text_layer(self, layer_id, **changes):
        """Change any TextLayer field by name (text, start, end, x, y, size, colours, ...)."""
        layer = self.text_by_id(layer_id)
        if layer is None:
            return False
        updates = {name: value for name, value in changes.items()
                   if name in TextLayer.__dataclass_fields__ and name != "id"}
        if "text" in updates:
            updates["text"] = str(updates["text"])
        if "start" in updates:
            updates["start"] = max(float(updates["start"]), 0.0)
        if "end" in updates:
            updates["end"] = float(updates["end"])
        start = updates.get("start", layer.start)
        end = updates.get("end", layer.end)
        if end < start + MIN_CLIP:
            end = start + MIN_CLIP
            if "end" in updates or "start" in updates:
                updates["end"] = end
        for name in ("x", "y"):
            if name in updates:
                updates[name] = min(max(float(updates[name]), 0.0), 1.0)
        if "size" in updates:
            updates["size"] = min(max(int(updates["size"]), 8), 400)
        for name in ("outline", "shadow"):
            if name in updates:
                updates[name] = min(max(int(updates[name]), 0), 40)
        for name in ("color", "outline_color", "shadow_color"):
            if name in updates:
                updates[name] = normalize_color(updates[name])
        if all(getattr(layer, name) == value for name, value in updates.items()):
            return False
        for name, value in updates.items():
            setattr(layer, name, value)
        self.changed.emit()
        return True

    def move_audio_clip(self, audio_id, start):
        """Put an audio clip at a new timeline start (seconds). Can be negative-free: 0 is the left edge."""
        clip = self.audio_by_id(audio_id)
        if clip is None:
            return False
        start = max(0.0, float(start))
        if abs(start - clip.start) < 1e-6:
            return False
        clip.start = start
        self.changed.emit()
        return True

    def set_audio_volume(self, audio_id, volume):
        clip = self.audio_by_id(audio_id)
        if clip is None:
            return False
        volume = min(max(float(volume), 0.0), 2.0)
        if abs(volume - clip.volume) < 1e-6:
            return False
        clip.volume = volume
        self.changed.emit()
        return True

    def set_audio_mute(self, audio_id, mute):
        clip = self.audio_by_id(audio_id)
        if clip is None or clip.mute == bool(mute):
            return False
        clip.mute = bool(mute)
        self.changed.emit()
        return True

    def set_audio_fades(self, audio_id, fade_in, fade_out):
        """Fade in / out at the clip ends, 0 .. min(2 s, half the clip)."""
        clip = self.audio_by_id(audio_id)
        if clip is None:
            return False
        top = min(2.0, clip.length / 2)
        fade_in = min(max(float(fade_in), 0.0), top)
        fade_out = min(max(float(fade_out), 0.0), top)
        if abs(fade_in - clip.fade_in) < 1e-6 and abs(fade_out - clip.fade_out) < 1e-6:
            return False
        clip.fade_in, clip.fade_out = fade_in, fade_out
        self.changed.emit()
        return True

    def split(self, t):
        """Cut the clip under timeline time t in two. Both halves keep the clip's transform (and any later per-clip effect)."""
        i, offset = self.clip_at(t)
        if i < 0:
            return False
        c = self.clips[i]
        if offset < MIN_CLIP or c.length - offset < MIN_CLIP:
            return False
        cut = c.src_in + offset
        self.clips.insert(i + 1, replace(c, src_in=cut, id=next(_next_id)))
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

    # ---- picture: mirror / rotate / crop (see core/transform.py)
    def set_transform(self, clip_id, transform):
        i = self.index_of(clip_id)
        if i < 0 or self.clips[i].transform == transform:
            return False
        self.clips[i].transform = transform
        self.changed.emit()
        return True

    def rotate(self, clip_id, quarter_turns=1):
        """Turn the clip's picture 90 degrees clockwise (x times)."""
        i = self.index_of(clip_id)
        return i >= 0 and self.set_transform(clip_id, self.clips[i].transform.turned(quarter_turns))

    def flip(self, clip_id, horizontal=True):
        """Mirror the clip's picture left-right (horizontal) or top-bottom."""
        i = self.index_of(clip_id)
        return i >= 0 and self.set_transform(clip_id, self.clips[i].transform.mirrored(horizontal))

    def set_crop(self, clip_id, rect):
        """rect = (x, y, w, h) as fractions of the picture, or None for no crop."""
        i = self.index_of(clip_id)
        return i >= 0 and self.set_transform(clip_id, self.clips[i].transform.with_crop(rect))

    def set_fades(self, clip_id, fade_in, fade_out):
        """Fade from / to black at the clip ends, 0 .. min(2 s, half the clip)."""
        i = self.index_of(clip_id)
        if i < 0:
            return False
        c = self.clips[i]
        top = min(2.0, c.length / 2)
        fade_in = min(max(float(fade_in), 0.0), top)
        fade_out = min(max(float(fade_out), 0.0), top)
        if abs(fade_in - c.fade_in) < 1e-6 and abs(fade_out - c.fade_out) < 1e-6:
            return False
        c.fade_in, c.fade_out = fade_in, fade_out
        self.changed.emit()
        return True

    def set_aspect_all(self, ratio):
        """Crop EVERY clip to the biggest centered box with this ratio (w, h); None = remove all crops.
        Clips whose picture size is unknown are skipped."""
        changed = False
        for c in self.clips:
            w, h = c.picture_size
            if ratio is None:
                crop = None
            elif w > 0 and h > 0:
                crop = centered_crop(w, h, ratio)
            else:
                continue
            new = c.transform.with_crop(crop)
            if new != c.transform:
                c.transform = new
                changed = True
        if changed:
            self.changed.emit()
        return changed
