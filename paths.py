"""Path helpers: makes mpv-2.dll and ffmpeg (in ./bin) findable. Import and call setup_paths() first."""
import os
import shutil
import sys
from pathlib import Path


def _app_dir() -> Path:
    # Works for the packaged .exe (PyInstaller) and for normal runs
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent


APP_DIR = _app_dir()
BIN_DIR = APP_DIR / "bin"


def setup_paths() -> None:
    """Call this BEFORE `import mpv`."""
    if hasattr(os, "add_dll_directory"):  # Windows only
        os.add_dll_directory(str(APP_DIR))
    os.environ["PATH"] = os.pathsep.join(
        [str(APP_DIR), str(BIN_DIR), os.environ.get("PATH", "")]
    )


def find_tool(name: str) -> str | None:
    """Find ffmpeg / ffprobe: ./bin first, then system PATH."""
    exe = name + (".exe" if os.name == "nt" else "")
    local = BIN_DIR / exe
    if local.exists():
        return str(local)
    return shutil.which(name)
