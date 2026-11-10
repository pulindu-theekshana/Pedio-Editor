"""Text layers on the timeline. Plain data + one ASS builder that the preview and the export share.

The preview lets mpv render the generated ASS file as an external subtitle; the export passes the same file
to ffmpeg's `ass` filter. The canvas is fixed at 1920 x 1080, so `x` / `y` / `size` mean the same on every
clip; libass scales the canvas to the real frame size.
"""
from dataclasses import dataclass, field
from itertools import count

CANVAS_W = 1920
CANVAS_H = 1080
DEFAULT_FONT = "Arial"

_next_id = count(1)


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
    id: int = field(default_factory=lambda: next(_next_id))

    @property
    def length(self):
        return max(self.end - self.start, 0.0)

    def copy(self):
        return TextLayer(**{name: getattr(self, name) for name in self.__dataclass_fields__})


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


def build_ass(layers, width=CANVAS_W, height=CANVAS_H):
    """The whole ASS file as a string. Empty layers still give a valid (blank) file."""
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
        x = round(min(max(layer.x, 0.0), 1.0) * width)
        y = round(min(max(layer.y, 0.0), 1.0) * height)
        tags = (
            f"\\an5\\pos({x},{y})\\fs{max(int(layer.size), 1)}"
            f"\\c{_ass_color(layer.color)}"
            f"\\3c{_ass_color(layer.outline_color)}"
            f"\\4c{_ass_color(layer.shadow_color)}"
            f"\\bord{max(int(layer.outline), 0)}\\shad{max(int(layer.shadow), 0)}"
        )
        lines.append(f"Dialogue: 0,{_ass_time(layer.start)},{_ass_time(layer.end)},Pedio,,0,0,0,,"
                     f"{{{tags}}}{_ass_text(layer.text)}")
    return "\n".join(lines) + "\n"
