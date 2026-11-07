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
- Import one or more videos (Ctrl+O or drag and drop). They are joined one after another on the timeline.
- Click a clip to select it. Drag its yellow edges to trim, drag the clip itself to reorder.
- Space = play / pause, S = split at the playhead, Del = delete the selected clip, [ and ] = cut the start / end of the clip at the playhead.
- Snap (above the timeline) pulls edges to the playhead and to other clips.

## Export
- Toolbar > Export > Export Video. Pick a file name; the whole timeline is saved as an MP4 (H.264 + AAC).
- The window shows a progress bar and a Cancel button. A cancelled or failed export leaves no broken file.
