"""Quick check that Python, PySide6, mpv and FFmpeg are all working."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pedio.paths import setup_paths, find_tool  # noqa: E402

setup_paths()

ok = True

print("Python:", sys.version.split()[0])

try:
    import PySide6
    print("PySide6:", PySide6.__version__, "- OK")
except Exception as e:
    ok = False
    print("PySide6: FAILED -", e)

try:
    import mpv
    p = mpv.MPV()
    p.terminate()
    print("mpv: OK")
except Exception as e:
    ok = False
    print("mpv: FAILED -", e)
    if e.__cause__:  # python-mpv hides the real Windows error in here
        print("     real error:", repr(e.__cause__))

for tool in ("ffmpeg", "ffprobe"):
    path = find_tool(tool)
    if path:
        print(f"{tool}: OK ({path})")
    else:
        ok = False
        print(f"{tool}: NOT FOUND")

print()
print("ALL GOOD" if ok else "SOMETHING FAILED - see above")
sys.exit(0 if ok else 1)
