"""Audio clips placed on the edit timeline. No widgets or ffmpeg code live here."""
from dataclasses import dataclass, field
from itertools import count


_next_id = count(1)


@dataclass
class AudioClip:
    """One source-audio range placed at ``start`` seconds on the timeline."""
    path: str
    start: float
    src_in: float
    src_out: float
    volume: float = 1.0
    mute: bool = False
    id: int = field(default_factory=lambda: next(_next_id))

    @property
    def length(self):
        return self.src_out - self.src_in

    @property
    def end(self):
        return self.start + self.length
