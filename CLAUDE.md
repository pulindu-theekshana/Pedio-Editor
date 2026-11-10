# Pedio Editor - CLAUDE.md

Read this first in a new chat, then ONLY the mini-step you were given in `docs/STEPS.md`. Keep this file short.

## Project
Simple video editor for Windows. Python 3.10-3.13 + PySide6 + python-mpv (libmpv) + FFmpeg.
Owner: Pulindu (some dev knowledge; free Claude plan: a 5-hour usage limit, so work is split into small mini-steps, one chat each).

## How to answer the owner (his rules)
- Short, simple answers. Do not over-explain.
- Give cmd-prompt commands to run.
- Code edits: file name + "before" / "after" lines. If a file has 3 or fewer edit spots, never resend the whole file.
- Send a whole file only for new files or big rewrites (when more than 3 spots change). Ask before changing the whole project. No zip files except the first creation.
- Commits: author and committer = pulindu-theekshana <tpulindu@gmail.com>. No Claude author, co-author or Claude lines in commit messages or PR text.

## Work rules (save tokens, never lose work)
- One chat = ONE mini-step from `docs/STEPS.md`. Do not start the next one.
- Read this file + only the files listed in the mini-step. Use grep and line ranges for the rest; never read a whole big file for one function.
- No test scripts, no Xvfb, no mpv or ffmpeg runs on Linux. Only `python -m py_compile <changed files>`. The owner tests on Windows and pastes the last ~15 lines of any error.
- Commit + push at the end of every mini-step. If it is unfinished or untested, push a `stepN-wip` branch instead of main. Stop at a clean point if the mini-step turns out bigger than planned, and say what is left.
- At the end: tick the mini-step in `docs/STEPS.md`; update this file only if the structure or an API changed; tell him which files changed; give the cmd commands (`git pull`, `python main.py`) and a 3-line prompt for the next chat.
- Also write this file to the project doc `claude/CLAUDE.md` when it changes.

## Run
```
venv\Scripts\activate
python main.py
```
First time only: `setup.bat` (venv, pip, libmpv, vulkan-1.dll, ffmpeg into bin\, then `scripts\check_setup.py`).

## Build order (details + mini-steps: docs/STEPS.md)
- Done (all tested on Windows): 1 setup, 2 import + player, 3 timelines, 4 trim / split / delete / reorder / snap, 5 export, 6 picture (rotate, flip, aspect ratio, crop: preview, export and timeline thumbnails match).
  - CURRENT: 7 audio, next mini-step 7b.
- Basic, then: 8 video fades | 9 text overlay | 10 speed + export settings | 11 undo, save / open, autosave, shortcuts | 12 dark theme, hardware decoding, icon, .exe
- Advanced (after 12): 13 image overlays | 14-15 keyframes | 16-18 smart masking | 19 .exe with the AI parts

## Folder structure
```
pedio-editor/
  main.py              entry point: setup_paths() BEFORE any mpv import, then starts the app
  CLAUDE.md  README.md  requirements.txt  .gitignore
  setup.bat  run.bat
  docs/STEPS.md        build plan: mini-steps with files to read / touch
  mpv-2.dll, vulkan-1.dll   (root, gitignored, downloaded by setup)
  bin/                 ffmpeg.exe, ffprobe.exe (gitignored)
  scripts/
    setup_deps.py      downloads libmpv, vulkan loader, ffmpeg
    check_setup.py     checks Python / PySide6 / mpv / ffmpeg
  pedio/               the app (all imports are absolute: `from pedio.ui.player import ...`)
    __init__.py        (empty)
    paths.py           APP_DIR, BIN_DIR, setup_paths(), find_tool("ffmpeg")
    core/              logic without widgets (empty __init__.py)
      clips.py         Clip (+ its Transform) + Segment + Project (the edit: clips in order, selection, split/trim/delete/move, rotate/flip/crop)
      audio.py         AudioClip (source range, timeline start, volume, mute)
      transform.py     Transform (flip / rotate / crop as plain data), video_filters() = the ONE place that builds the ffmpeg filters for preview + export, ASPECT_PRESETS
      workers.py       QThread ffmpeg jobs: ProbeWorker, FrameWorker, ThumbnailWorker, WaveformWorker
      export.py        ExportSettings, probe_source(), build_command() (pure), ExportWorker (QThread: progress / cancel)
    ui/                widgets (empty __init__.py)
      main_window.py   MainWindow (splitter: player / timeline, toolbar at the bottom, multi-import, drag-drop, edit shortcuts, export_video)
      export_dialog.py ExportDialog (modal: progress bar + Cancel, runs the ExportWorker)
      crop_editor.py   CropView (picture + draggable box) + CropEditor (replaces the preview while cropping)
      player.py        PlayerWidget (embedded mpv plays the whole edit as one edl:// list; seek bar, volume, mute; applies each clip's Transform as vf=lavfi)
      timeline.py      TimelineView (painted clips, trim handles, drag reorder, snapping; thumbnails follow flip / rotate / crop) + TimelinePanel (zoom row, Snap, headers, scrollbar)
      toolbar.py       BottomToolbar + CATEGORIES (Video, Audio, Effects, Text, Export)
```
Where new code goes: clip/project data, export command builder, undo history, project save/load -> `pedio/core/`.
New tool dialogs/panels (crop, audio, text, export settings) -> `pedio/ui/` (a `pedio/ui/panels/` subfolder when there are several).

## Rules / gotchas
- `setup_paths()` must run before `import mpv` (adds the DLL folder). main.py does this. `player.py` imports mpv lazily.
- mpv callbacks run on mpv's thread: only emit Qt Signals from them, never touch widgets.
- mpv `vf=lavfi=[...]` filters (rotate / flip / crop, later fades / text) fail on GPU frames ("Impossible to convert ... src: d3d11"). So the player uses `hwdec="auto-copy-safe"` (copy-back), never plain `auto` / `auto-safe`.
- Never give a method in a QWidget subclass the name of a Qt method (`rect`, `size`, `update`, `width` ...): it replaces Qt's own and painting then fails silently (black widget).
- Seek with `player.command("seek", t, "absolute+exact" | "absolute+keyframes")` (keyframes while dragging, exact on release).
- libmpv needs vulkan-1.dll next to mpv-2.dll (setup_deps.py handles it). Archives with BCJ2 need 7-Zip (not py7zr).
- ffmpeg jobs run in QThreads with stop(); ignore signals from stale workers (`self.sender()` check).
- Timeline painting is cached in a QPixmap; the playhead is drawn on top. Call `_invalidate()` when data/zoom/scroll changes.
- Hide console windows on Windows: `creationflags=CREATE_NO_WINDOW` (see workers.py).
- Every edit goes through `Project` (never change a `Clip` from the UI directly): it emits `changed`, and the timeline + player follow.
- `Project.changed` handler order matters: TimelineView (clamps the playhead) first, then `MainWindow.on_project_changed` (reloads the player).
- No gaps on the timeline: clips are always joined end to end (delete / trim close the gap).
- The player reloads mpv only when `Project.play_list()` changes; neighbours that continue each other are merged, so a plain split does not reload.
- Export uses `Project.play_list()` (same list as the player). Each segment is cut with input seeking (`-ss/-t -i`), scaled/padded to the first clip's size + fps, joined with `concat`; a clip without audio gets `anullsrc` silence. The file is written as `<name>.mp4.part` and renamed when complete.
- New per-clip effects (crop, rotate, fades, text, speed) go into the per-segment filter chain in `build_command()`; new options go into `ExportSettings`.
- Keep every effect as plain data on the clip / layer, applied in ONE place for the preview and ONE place for the export (one source of truth). Then keyframes (Steps 14-15) only add "value at time t" and nothing has to be rewritten.
- Limit: every segment adds about 400 characters to the ffmpeg command line (Windows allows about 32000), so an edit with 60+ separate segments would fail. If that ever happens: write the filter to a file (`-/filter_complex file` on ffmpeg 7+).

## Main APIs (as built)
- `Project` (core/clips.py): `clips` (list of `Clip(path, src_duration, src_in, src_out, id, size, meta_rotation, transform)`), `audio_clips` (list of `AudioClip(path, start, src_in, src_out, volume, mute, id)`), `selected_id`, signals `changed`, `selection_changed`. Reading: `total()`, `starts()`, `boundaries()`, `index_of(id)`, `clip_at(t)`, `play_list()` (= list of `Segment(path, start, length, transform, meta_rotation)`). Edits (return True if something changed): `add_clip(path, duration)`, `add_audio_clip(path, start, src_in, src_out, volume=1.0, mute=False)`, `remove_audio_clip(id)`, `split(t)`, `delete(id)`, `trim_start(id, new_in)`, `trim_end(id, new_out)`, `trim_start_at(t)`, `trim_end_at(t)`, `move(id, new_index)`, `select(id)`, `rotate(id)`, `flip(id, horizontal)`, `set_crop(id, rect)`, `set_aspect_all(ratio)`, `set_transform(id, Transform)`. `MIN_CLIP` = 0.1 s.
- `PlayerWidget` signals: `file_loaded(str)`, `load_failed(str)`, `position_changed(float)`, `duration_changed(float)`, `playing_changed(bool)`, `muted_changed(bool)`. Methods: `set_edit_list([Segment], position)`, `toggle_play()`, `pause()`, `refresh()`, `seek_to(sec, exact=True)`, `set_muted(bool)`, `toggle_mute()`, `is_muted()`, `position()`, `duration()`, `shutdown()`. Positions are timeline seconds (mpv edl:// time).
- `TimelinePanel(player, project)`: `position()`, `set_playhead(t)`, `shutdown()`. Inner `TimelineView(project)`: `set_zoom(pps, anchor_x)`, `zoom_fit`, `set_position`, `set_playhead`, `set_snap`, signal `seek_requested(float, bool)`. Zoom is pixels per second (fit .. 300). Thumbnails / waveform are kept per source file (`Media`), shared by all clips of that file.
- `BottomToolbar` signal `tool_clicked(category, tool)`; `MainWindow.on_tool()` handles Import, Trim Start, Trim End, Split, Delete, Crop, Aspect Ratio, Rotate, Flip H, Flip V, Export Video.
- Export (core/export.py): `ExportWorker(clips, out_path, settings=None, parent=None, audio_clips=None)` with `clips = [Segment]` (= `Project.play_list()`) and optional `audio_clips = [AudioClip]`; signals `progress(float 0..1)`, `done(path)`, `failed(msg)`, `cancelled()`; `stop()` kills ffmpeg. `ExportDialog(clips, out_path, settings, parent)`: `exec()`, then `.succeeded`. `MainWindow.export_video()` asks for the file name (refuses a source file) and opens the dialog. Output: MP4, libx264 CRF 20 veryfast, AAC 192k, yuv420p, faststart.
- workers: thumbnails about 150 per video (`thumb_step`), waveform 200 peaks per second (min/max of 8 kHz mono audio), `ProbeWorker` (ffprobe: length, picture size, phone rotation) before a file is added; `FrameWorker(path, t, filters)` = one PNG picture for the crop editor.
- Picture (core/transform.py): `Transform(rotate = 0/90/180/270 clockwise, flip = mirror left-right (applied first), crop = (x, y, w, h) fractions of the picture after flip + rotate, or None)`; `is_default`, `turned()`, `mirrored(horizontal)`, `with_crop(rect)`. `video_filters(transform, meta_rotation, with_crop)` -> ffmpeg filter list, used by the player (`vf=lavfi`) and the export. Also `ASPECT_PRESETS`, `centered_crop`, `output_size`. `CropEditor.start(clip, t)`; signals `applied(rect or None)`, `cancelled`.
