# Pedio Editor

A simple video editor built with Python, PySide6, mpv and FFmpeg (Windows).

## Setup (once)
1. Copy `setup.bat` into this folder and double-click it.
   It creates a venv, installs the packages, and downloads ffmpeg and libmpv.

## Run
```
venv\Scripts\activate
python main.py
```

## Editing
- Import one or more videos (Ctrl+I or drag and drop). They are joined one after another on the timeline.
- Click a clip to select it. Drag its yellow edges to trim, drag the clip itself to reorder.
- Space = play / pause, S = split, Del = delete the selected clip / text / image / audio, [ and ] = cut the start / end at the playhead.
- Snap (above the timeline) pulls edges to the playhead and to other clips. Ctrl+wheel = zoom.
- Video: rotate, flip, crop, aspect ratio, speed (0.25x - 4x), fades.
- Audio: add / extract sound, volume, mute, fades, volume keyframes.
- Text and images have their own timeline lanes; drag them on the preview. Keyframes: text x / y / size / opacity, image x / y / scale / opacity.
- Undo / redo: Ctrl+Z / Ctrl+Y (or Ctrl+Shift+Z). K opens the keyframe panel.

## Save and open
- Ctrl+S saves the edit as a `.pedio` file (Ctrl+Shift+S = save as, Ctrl+O = open). The media files stay where they are.
- The edit is autosaved to the temp folder every 60 seconds.

## Export
- Toolbar > Export > Export Settings: MP4 / MKV (H.264 + AAC) or WebM (VP9 + Opus), quality, size, frame rate.
- Toolbar > Export > Export Video. Pick a file name; the whole timeline is saved.
- The window shows a progress bar and a Cancel button. A cancelled or failed export leaves no broken file.

## Build the .exe
```
setup.bat
build.bat
```
The result is `dist\Pedio Editor.exe` (mpv and ffmpeg are bundled).
