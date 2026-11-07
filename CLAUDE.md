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
      clips.py         Clip + Project (the edit: clips in order, selection, split/trim/delete/move)
      workers.py       QThread ffmpeg jobs: ProbeWorker, ThumbnailWorker, WaveformWorker
      export.py        ExportSettings, probe_source(), build_command() (pure), ExportWorker (QThread: progress / cancel)
    ui/                widgets (empty __init__.py)
      main_window.py   MainWindow (splitter: player / timeline, toolbar at the bottom, multi-import, drag-drop, edit shortcuts, export_video)
      export_dialog.py ExportDialog (modal: progress bar + Cancel, runs the ExportWorker)
      player.py        PlayerWidget (embedded mpv plays the whole edit as one edl:// list; seek bar, volume, mute)
      timeline.py      TimelineView (painted clips, trim handles, drag reorder, snapping) + TimelinePanel (zoom row, Snap, headers, scrollbar)
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
- Every edit goes through `Project` (never change a `Clip` from the UI directly): it emits `changed`, and the timeline + player follow.
- `Project.changed` handler order matters: TimelineView (clamps the playhead) first, then `MainWindow.on_project_changed` (reloads the player).
- No gaps on the timeline: clips are always joined end to end (delete / trim close the gap).
- The player reloads mpv only when `Project.play_list()` changes; neighbours that continue each other are merged, so a plain split does not reload.
- Window shortcuts need an active window (under Xvfb with no window manager call `QApplication.setActiveWindow(win)` in tests).
- Export uses `Project.play_list()` (same list as the player). Each segment is cut with input seeking (`-ss/-t -i`), scaled/padded to the first clip's size + fps, joined with `concat`; a clip without audio gets `anullsrc` silence. The file is written as `<name>.mp4.part` and renamed when complete.
- New per-clip effects (crop, rotate, fades, text, speed) go into the per-segment filter chain in `build_command()`; new options go into `ExportSettings`.
- Keep every effect as plain data on the clip / layer, applied in ONE place for the preview and ONE place for the export. Then keyframes (Steps 14-15) only add "value at time t" and nothing has to be rewritten.
- Limit: every segment adds about 400 characters to the ffmpeg command line (Windows allows about 32000), so an edit with 60+ separate segments would fail. If that ever happens: write the filter to a file (`-/filter_complex file` on ffmpeg 7+).

## Main APIs (as built)
- `Project` (core/clips.py): `clips` (list of `Clip(path, src_duration, src_in, src_out, id)`), `selected_id`, signals `changed`, `selection_changed`. Reading: `total()`, `starts()`, `boundaries()`, `index_of(id)`, `clip_at(t)`, `play_list()`. Edits (return True if something changed): `add_clip(path, duration)`, `split(t)`, `delete(id)`, `trim_start(id, new_in)`, `trim_end(id, new_out)`, `trim_start_at(t)`, `trim_end_at(t)`, `move(id, new_index)`, `select(id)`. `MIN_CLIP` = 0.1 s.
- `PlayerWidget` signals: `file_loaded(str)`, `load_failed(str)`, `position_changed(float)`, `duration_changed(float)`, `playing_changed(bool)`, `muted_changed(bool)`. Methods: `set_edit_list([(path, start, length)], position)`, `toggle_play()`, `seek_to(sec, exact=True)`, `set_muted(bool)`, `toggle_mute()`, `is_muted()`, `position()`, `duration()`, `shutdown()`. Positions are timeline seconds (mpv edl:// time).
- `TimelinePanel(player, project)`: `position()`, `set_playhead(t)`, `shutdown()`. Inner `TimelineView(project)`: `set_zoom(pps, anchor_x)`, `zoom_fit`, `set_position`, `set_playhead`, `set_snap`, signal `seek_requested(float, bool)`. Zoom is pixels per second (fit .. 300). Thumbnails / waveform are kept per source file (`Media`), shared by all clips of that file.
- `BottomToolbar` signal `tool_clicked(category, tool)`; `MainWindow.on_tool()` handles Import, Trim Start, Trim End, Split, Delete, Export Video.
- Export (core/export.py): `ExportWorker(clips, out_path, settings=None)` with `clips = [(path, start, length)]`; signals `progress(float 0..1)`, `done(path)`, `failed(msg)`, `cancelled()`; `stop()` kills ffmpeg. `ExportDialog(clips, out_path, settings, parent)`: `exec()`, then `.succeeded`. `MainWindow.export_video()` asks for the file name (refuses a source file) and opens the dialog. Output: MP4, libx264 CRF 20 veryfast, AAC 192k, yuv420p, faststart.
- workers: thumbnails about 150 per video (`thumb_step`), waveform 200 peaks per second (min/max of 8 kHz mono audio), `ProbeWorker` (ffprobe: length + has-video check) before a file is added.

## Using the timeline (Step 4)
- Mouse: click a clip = select + move playhead. Drag its yellow edge handles = trim. Drag the clip body = reorder. Drag on the ruler = move playhead. Ctrl+wheel = zoom. "Snap" button toggles snapping (clip edges + playhead, 8 px).
- Keys: Space play/pause, S split at playhead, Del delete (selected clip, else the clip under the playhead), [ and ] cut the start / end of the clip under the playhead, Ctrl+O import (several files allowed).

## Steps (one chat per step; every new feature = a new chat)
1. Setup + layout - DONE
2. Import + player (button, drag-drop, play/pause, seek, volume, mute) - DONE
3. Timelines (thumbnails, waveform + mute button, playhead, zoom) - DONE
4. Trim / split / delete / join / reorder / snapping + shortcuts - DONE
5. Basic export (FFmpeg, progress, cancel, background thread) - DONE
6. Aspect ratio presets (9:16, 16:9, 4:3, 1:1 ...), crop with a draggable box on the preview, rotate, flip; export matches - NEXT. Preview: mpv video filters / `video-crop`; export: add to the per-segment filter chain in `build_command()`; output size goes in `ExportSettings.size`.
7. Audio: add MP3/WAV/AAC tracks, extract audio from a video, volume per clip, mute, audio fade in/out (0.1-2 s); export matches
8. Video fade in/out at split points and where two clips meet (0.1-2 s slider); export matches
9. Text overlay: size, color, shadow, outline size/color, start/end time on the timeline, drag to position on the preview; export matches
10. Clip speed + export settings (resolution, quality, format)
11. Undo/redo, save/open project file, autosave, full keyboard shortcuts
12. Dark theme, hardware decoding toggle, app icon, PyInstaller .exe (mpv dll, vulkan-1.dll, ffmpeg) = the simple editor is finished

Advanced features (only after Step 12 works):
13. Image / graphic overlay layers (PNG / JPG: position, scale, opacity, start/end, order); shares the layer model of the text overlay
14. Keyframes 1: keyframe engine in `pedio/core/` (value at time t, linear / hold / ease), keyframe lane + diamonds on the timeline, animate layer position / scale / opacity; preview + export match
15. Keyframes 2: animate the video clip (position / scale / rotation / opacity), text and audio volume; copy / paste keyframes
16. Smart masking 1: AI model setup (downloaded on first use, must run on a CPU-only laptop), brush on the preview (paint foreground / background, eraser), mask of one frame
17. Smart masking 2: track the mask across the whole clip (background thread, progress, cancel, mask cache on disk), correction strokes on any frame
18. Smart masking 3: put text / graphics behind the subject (between foreground and background), edge feather; preview + export match
19. Rebuild the .exe with the AI parts (model downloaded on first use, not bundled)

## Status / notes
- Step 4 tested on Linux (libmpv + ffmpeg under Xvfb): 141 checks (model, import, playback across clips incl. a clip without audio and odd file names, split / delete / trim / reorder / snap, shortcuts, editing while playing). Not yet tested on Windows.
- Folder restructure (Nov 2026): code moved into `pedio/` (core, ui); imports are absolute.
- Test scripts live outside the repo (they need a Linux display); nothing to run from here.
- No undo yet (Step 11): Delete cannot be undone.
- Step 5 tested on Linux (ffmpeg 6.1, PySide6 offscreen): 36 export checks + 9 window checks (mixed sizes / fps, clip without audio, odd file names, trimmed + split + reordered, same source twice, cancel, bad folder, missing file, dialog Cancel / Esc). The real MainWindow + mpv preview were not run for export; not yet tested on Windows.
- The preview keeps playing while the export dialog is open (harmless; pause it in the Step 11 polish if it slows the export).
- Keep one source of truth: the player and the export both read `Project.play_list()`.
