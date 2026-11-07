# Pedio Editor - CLAUDE.md

Read this first in a new chat. Update it at the end of every step (keep it short).

## Project
Simple video editor for Windows. Python 3.10-3.13 + PySide6 + python-mpv (libmpv) + FFmpeg.
Owner: Pulindu (some dev knowledge; free Claude plan, so work is split into steps, one chat per step).

## How to answer the owner (his rules)
- Short, simple answers. Do not over-explain.
- Give cmd-prompt commands to run.
- Code edits: file name + "before" / "after" lines. If a file has 3 or fewer edit spots, never resend the whole file.
- Send a whole file only for new files or big rewrites (when more than 3 spots change). Ask before changing the whole project. No zip files except the first creation.
- After each step: update this file, tell him which files changed.

## Run
```
venv\Scripts\activate
python main.py
```
First time only: `setup.bat` (venv, pip, libmpv, vulkan-1.dll, ffmpeg into bin\, then `scripts\check_setup.py`).

## Folder structure
```
pedio-editor/
  main.py              entry point: setup_paths() BEFORE any mpv import, then starts the app
  CLAUDE.md  README.md  requirements.txt  .gitignore
  setup.bat  run.bat
  mpv-2.dll, vulkan-1.dll   (root, gitignored, downloaded by setup)
  bin/                 ffmpeg.exe, ffprobe.exe (gitignored)
  scripts/
    setup_deps.py      downloads libmpv, vulkan loader, ffmpeg
    check_setup.py     checks Python / PySide6 / mpv / ffmpeg
  pedio/               the app (all imports are absolute: `from pedio.ui.player import ...`)
    __init__.py        (empty)
    paths.py           APP_DIR, BIN_DIR, setup_paths(), find_tool("ffmpeg")
    core/              logic without widgets (empty __init__.py)
      workers.py       QThread ffmpeg jobs: ThumbnailWorker, WaveformWorker
    ui/                widgets (empty __init__.py)
      main_window.py   MainWindow (splitter: player / timeline, toolbar at the bottom, import, drag-drop)
      player.py        PlayerWidget (embedded mpv, play/pause, seek bar, volume, mute)
      timeline.py      TimelineView (painted) + TimelinePanel (zoom row, track headers, scrollbar)
      toolbar.py       BottomToolbar + CATEGORIES (Video, Audio, Effects, Text, Export)
```
Where new code goes: clip/project data, export command builder, undo history, project save/load -> `pedio/core/`.
New tool dialogs/panels (crop, audio, text, export settings) -> `pedio/ui/` (a `pedio/ui/panels/` subfolder when there are several).

## Rules / gotchas
- `setup_paths()` must run before `import mpv` (adds the DLL folder). main.py does this. `player.py` imports mpv lazily.
- mpv callbacks run on mpv's thread: only emit Qt Signals from them, never touch widgets.
- Seek with `player.command("seek", t, "absolute+exact" | "absolute+keyframes")` (keyframes while dragging, exact on release).
- libmpv needs vulkan-1.dll next to mpv-2.dll (setup_deps.py handles it). Archives with BCJ2 need 7-Zip (not py7zr).
- ffmpeg jobs run in QThreads with stop(); ignore signals from stale workers (`self.sender()` check).
- Timeline painting is cached in a QPixmap; the playhead is drawn on top. Call `_invalidate()` when data/zoom/scroll changes.
- Hide console windows on Windows: `creationflags=CREATE_NO_WINDOW` (see workers.py).

## Main APIs (as built)
- `PlayerWidget` signals: `file_loaded(str)`, `load_failed(str)`, `position_changed(float)`, `duration_changed(float)`, `playing_changed(bool)`, `muted_changed(bool)`. Methods: `load(path)`, `toggle_play()`, `seek_to(sec, exact=True)`, `set_muted(bool)`, `toggle_mute()`, `is_muted()`, `position()`, `duration()`, `shutdown()`.
- `TimelinePanel(player)`: follows the player signals; `clear()`, `shutdown()`. Inner `TimelineView`: `set_duration`, `add_thumbnail`, `add_peaks`, `set_zoom(pps, anchor_x)`, `zoom_fit`, `set_position`, signal `seek_requested(float, bool)`. Zoom is pixels per second (fit .. 300).
- `BottomToolbar` signal `tool_clicked(category, tool)`; `MainWindow.on_tool()` handles it (only Import works so far).
- workers: thumbnails about 150 per video (`thumb_step`), waveform 200 peaks per second (min/max of 8 kHz mono audio).

## Steps (one chat each)
1. Setup + layout - DONE
2. Import + player (button, drag-drop, play/pause, seek, volume, mute) - DONE
3. Timelines (thumbnails, waveform + mute button, playhead, zoom) - DONE
4. Trim / split / delete / join + shortcuts - NEXT
5. Basic export (FFmpeg, progress, cancel, background thread)
6. Crop / aspect ratio / rotate / flip
7. Audio: add sound, extract from video, volume, fade in/out
8. Video fade in/out transitions (0.1-2 s slider) at split/join points
9. Text overlays (size, shadow, color, outline size/color, timing, position)
10. Speed + export settings
11. Undo/redo, save/open project, autosave, shortcuts
12. Polish + PyInstaller .exe (bundle mpv dll, vulkan-1.dll, ffmpeg; hardware decoding toggle)

## Status / notes
- Step 3 tested on Linux (libmpv + ffmpeg under Xvfb): 51/51 checks. Not yet tested by me on Windows rendering.
- Folder restructure (Nov 2026): code moved into `pedio/` (core, ui); imports are absolute.
- Test scripts live outside the repo (they need a Linux display); nothing to run from here.
- Steps 4+ will need a clip model in `pedio/core/` (list of clips with source in/out), so the timeline can draw more than one clip.
