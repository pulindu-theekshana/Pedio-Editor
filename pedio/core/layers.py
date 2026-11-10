"""Overlay layers on the timeline: text and images.

Everything is plain data. `build_ass()` is the ONE place that turns text layers into an ASS file,
shared by the preview (mpv renders it as an external subtitle) and the export (ffmpeg `ass` filter).
Image layers are drawn by Qt over the preview and by ffmpeg `overlay` in the export.

The canvas is fixed at 1920 x 1080, so `x` / `y` / `size` mean the same on every clip; libass scales
the canvas to the real frame size.
"""
import copy as _copy
from dataclasses import dataclass, field
from itertools import count

from pedio.core.keyframes import copy_anim, value_at

CANVAS_W = 1920
CANVAS_H = 1080
DEFAULT_FONT = "Arial"

_next_id = count(1)


def reserve_ids(max_id):
    """Continue numbering after a project file / undo snapshot that used these ids."""
    global _next_id
    _next_id = count(int(max_id) + 1)


@dataclass
class TextLayer:
    """One text overlay. Times are timeline seconds; x / y are fractions of the frame (0..1)."""
    text: str = "Text"
    start: float = 0.0
    end: float = 3.0
    x: float = 0.5           # centre of the text, 0 = left, 1 = right
    y: float = 0.5           # centre of the text, 0 = top, 1 = bottom
    size: int = 64           # font size on the 1920 x 1080 canvas
    color: str = "#ffffff"
    outline: int = 3         # outline thickness in canvas pixels
    outline_color: str = "#000000"
    shadow: int = 2          # shadow distance in canvas pixels
    shadow_color: str = "#000000"
    opacity: float = 1.0
    anim: dict = field(default_factory=dict)   # property -> [Keyframe]
    id: int = field(default_factory=lambda: next(_next_id))

    @property
    def length(self):
        return max(self.end - self.start, 0.0)

    def copy(self):
        return TextLayer(**{name: (_copy.deepcopy(getattr(self, name)) if name == "anim"
                                   else getattr(self, name))
                            for name in self.__dataclass_fields__})

    def value(self, prop, t):
        """The animated value of x / y / size / opacity at timeline time t."""
        return value_at(self.anim.get(prop), t, getattr(self, prop))


@dataclass
class ImageLayer:
    """A picture (PNG / JPG) over the video. x / y = centre (fractions), scale = width / frame width."""
    path: str = ""
    start: float = 0.0
    end: float = 3.0
    x: float = 0.5
    y: float = 0.5
    scale: float = 0.5       # 0.05 .. 2.0 of the frame width
    opacity: float = 1.0
    anim: dict = field(default_factory=dict)
    id: int = field(default_factory=lambda: next(_next_id))

    @property
    def length(self):
        return max(self.end - self.start, 0.0)

    def copy(self):
        return ImageLayer(**{name: (_copy.deepcopy(getattr(self, name)) if name == "anim"
                                   else getattr(self, name))
                             for name in self.__dataclass_fields__})

    def value(self, prop, t):
        return value_at(self.anim.get(prop), t, getattr(self, prop))


def normalize_color(value, fallback="#ffffff"):
    """'#rrggbb' (or 'rrggbb') -> '#rrggbb'. Anything else gives the fallback."""
    text = str(value or "").strip().lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) != 6:
        return fallback
    try:
        int(text, 16)
    except ValueError:
        return fallback
    return "#" + text.lower()


def _ass_color(hex_color, alpha=0):
    """'#rrggbb' -> ASS '&HAABBGGRR&' (alpha 0 = opaque, 255 = invisible)."""
    value = normalize_color(hex_color).lstrip("#").upper()
    r, g, b = value[0:2], value[2:4], value[4:6]
    return f"&H{alpha:02X}{b}{g}{r}&"


def _ass_time(t):
    """Seconds -> ASS 'H:MM:SS.cc'."""
    centi = max(int(round(float(t) * 100)), 0)
    total, cs = divmod(centi, 100)
    hours, rest = divmod(total, 3600)
    minutes, seconds = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{cs:02d}"


def _ass_text(text):
    """Escape the pieces ASS treats specially. New lines become \\N."""
    text = str(text).replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\N")


def _alpha(opacity):
    return int(round((1.0 - min(max(float(opacity), 0.0), 1.0)) * 255))


def _event_times(layer):
    """Start / end cut points for animation: the layer range plus every keyframe time inside it."""
    times = {round(layer.start, 6), round(layer.end, 6)}
    for points in (layer.anim or {}).values():
        for k in points:
            if layer.start + 1e-6 < k.t < layer.end - 1e-6:
                times.add(round(k.t, 6))
    return sorted(times)


def _static_tags(layer, x, y, size, opacity):
    return (f"\\an5\\pos({x},{y})\\fs{max(int(size), 1)}"
            f"\\c{_ass_color(layer.color)}"
            f"\\3c{_ass_color(layer.outline_color)}"
            f"\\4c{_ass_color(layer.shadow_color)}"
            f"\\bord{max(int(layer.outline), 0)}\\shad{max(int(layer.shadow), 0)}"
            f"\\alpha&H{_alpha(opacity):02X}&")


def _animated_tags(layer, ta, tb):
    xa = round(min(max(layer.value("x", ta), 0.0), 1.0) * CANVAS_W)
    ya = round(min(max(layer.value("y", ta), 0.0), 1.0) * CANVAS_H)
    xb = round(min(max(layer.value("x", tb), 0.0), 1.0) * CANVAS_W)
    yb = round(min(max(layer.value("y", tb), 0.0), 1.0) * CANVAS_H)
    sa = max(int(layer.value("size", ta)), 1)
    sb = max(int(layer.value("size", tb)), 1)
    aa = _alpha(layer.value("opacity", ta))
    ab = _alpha(layer.value("opacity", tb))
    tags = (f"\\an5\\fs{sa}\\c{_ass_color(layer.color)}"
            f"\\3c{_ass_color(layer.outline_color)}\\4c{_ass_color(layer.shadow_color)}"
            f"\\bord{max(int(layer.outline), 0)}\\shad{max(int(layer.shadow), 0)}"
            f"\\alpha&H{aa:02X}&")
    if (xa, ya) != (xb, yb):
        tags += f"\\move({xa},{ya},{xb},{yb})"
    else:
        tags += f"\\pos({xa},{ya})"
    if sa != sb or aa != ab:
        dur_ms = max(int(round((tb - ta) * 1000)), 1)
        tags += f"\\t(0,{dur_ms},\\fs{sb}\\alpha&H{ab:02X}&)"
    return tags


def build_ass(layers, width=CANVAS_W, height=CANVAS_H):
    """The whole ASS file as a string. Empty layers still give a valid (blank) file.
    Animated layers are split at their keyframes; position moves with `\\move`, size / opacity with `\\t`."""
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Pedio,{DEFAULT_FONT},64,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,"
        "0,0,1,3,2,5,10,10,10,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for layer in layers:
        if not str(layer.text).strip() or layer.end <= layer.start:
            continue
        times = _event_times(layer)
        animated = any(len(points) >= 2 for points in (layer.anim or {}).values())
        if len(times) <= 2 and not animated:
            x = round(min(max(layer.x, 0.0), 1.0) * width)
            y = round(min(max(layer.y, 0.0), 1.0) * height)
            tags = _static_tags(layer, x, y, layer.size, layer.opacity)
            lines.append(f"Dialogue: 0,{_ass_time(layer.start)},{_ass_time(layer.end)},Pedio,,0,0,0,,"
                         f"{{{tags}}}{_ass_text(layer.text)}")
            continue
        for ta, tb in zip(times, times[1:]):
            if tb - ta < 1e-3:
                continue
            tags = _animated_tags(layer, ta, tb)
            lines.append(f"Dialogue: 0,{_ass_time(ta)},{_ass_time(tb)},Pedio,,0,0,0,,"
                         f"{{{tags}}}{_ass_text(layer.text)}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------- plain values for preview / export
def layer_value(layer, prop, t, default):
    """A property of either layer type at time t (plain value when it has no keyframes)."""
    anim = getattr(layer, "anim", None) or {}
    return value_at(anim.get(prop), t, getattr(layer, prop, default))
