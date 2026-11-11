# PyInstaller spec: python -m PyInstaller Pedio.spec
# mpv-2.dll / vulkan-1.dll and bin\ffmpeg.exe / ffprobe.exe are bundled next to the exe.
import os

ROOT = os.path.abspath(os.path.dirname(SPEC))
binaries = []
for name in ("mpv-2.dll", "vulkan-1.dll"):
    path = os.path.join(ROOT, name)
    if os.path.exists(path):
        binaries.append((path, "."))
for name in ("ffmpeg.exe", "ffprobe.exe"):
    path = os.path.join(ROOT, "bin", name)
    if os.path.exists(path):
        binaries.append((path, "bin"))

icon = os.path.join(ROOT, "assets", "icon.ico")

a = Analysis(
    ["main.py"],
    pathex=[ROOT],
    binaries=binaries,
    datas=[],
    hiddenimports=["mpv"],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Pedio Editor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=icon if os.path.exists(icon) else None,
)
