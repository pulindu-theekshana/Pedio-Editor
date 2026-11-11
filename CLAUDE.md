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

## Run
```
venv\Scripts\activate
python main.py
```
First time only: `setup.bat` (venv, pip, libmpv, vulkan-1.dll, ffmpeg into bin\, then `scripts\check_setup.py`).
Build the exe: `build.bat` -> `dist\Pedio Editor.exe` (uses `Pedio.spec`).

## Build order (details + mini-steps: docs/STEPS.md)
- Done on Windows: 1 setup, 2 import + player, 3 timelines, 4 trim / split / delete / reorder / snap, 5 export, 6 picture, 7 audio, 8 video fades, 9 text overlay.
- Implemented but NOT yet tested on Windows: 10 speed + export settings, 11 undo / save / open / autosave / shortcuts, 12 dark theme / hardware decoding / icon / PyInstaller spec, 13 image overlays, 14 keyframes (text / image / audio), 15b/15c text + audio keys and copy / paste.
- Still open: 15a video-clip keyframes (position / scale / rotation / opacity), 16-18 smart masking (needs a model choice + owner agreement), 19 .exe with the AI parts.

## Folder structure
```
pedio-editor/
  main.py              entry point: setup_paths() BEFORE any mpv import, icon, then starts the app
  CLAUDE.md  README.md  requirements.txt  .gitignore
  setup.bat  run.bat  build.bat  Pedio.spec
  docs/STEPS.md        build plan: mini-steps with files to read / touch
  assets/icon.ico      made by scripts/make_icon.py (used by Pedio.spec)
  mpv-2.dll, vulkan-1.dll   (root, gitignored, downloaded by setup)
  bin/                 ffmpeg.exe, ffprobe.exe (gitignored)
  scripts/
    setup_deps.py      downloads libmpv, vulkan loader, ffmpeg
    check_setup.py     checks Python / PySide6 / mpv / ffmpeg
    make_icon.py       writes assets/icon.ico
  pedio/               the app (all imports are absolute: `from pedio.ui.player import ...`)
    __init__.py        (empty)
    paths.py           APP_DIR, BIN_DIR, setup_paths(), find_tool("ffmpeg")
    core/              logic without widgets (empty __init__.py)
      clips.py         Clip (+ Transform) + Segment + Project: clips in order, speed, keyframes, selection
      audio.py         AudioClip (source range, timeline start, volume, mute, fades) + volume keyframes
      layers.py        TextLayer + ImageLayer + value_at helpers + build_ass(): ONE ASS file for preview + export
      keyframes.py     Keyframe, easings, value_at / set / remove, ffmpeg `expression()`, copy_anim, json helpers
      transform.py     Transform (flip / rotate / crop), video_filters() for preview + export, ASPECT_PRESETS
      workers.py       QThread ffmpeg jobs: ProbeWorker, AudioProbeWorker, FrameWorker, ThumbnailWorker, WaveformWorker
      export.py        ExportSettings, probe_source(), build_command() (pure), ExportWorker, AudioMixWorker
      project_io.py    project_to_dict / apply_project_dict / save / open (.pedio JSON, version 2)
      history.py       undo / redo: snapshot of project_to_dict, limit 100
    ui/                widgets (empty __init__.py)
      main_window.py   MainWindow: player / timeline, toolbar, menus via shortcuts, undo, save / open, autosave, export
      export_dialog.py ExportDialog (modal: progress bar + Cancel, runs ExportWorker)
      export_settings.py ExportSettingsDialog (format, quality, size, fps, audio)
      settings_dialog.py SettingsDialog (dark theme, hardware decoding)
      theme.py         DARK_QSS + apply_theme(app, dark)
      crop_editor.py   CropView + CropEditor (replaces the preview while cropping)
      overlay_view.py  TextOverlay (drag the selected text, ghost only while dragging)
      image_overlay.py ImageOverlay (draws active images, drag the selected one)
      player.py        PlayerWidget (mpv edl:// list, seek / volume / mute, vf=lavfi per clip, ASS subtitles, hwdec toggle)
      timeline.py      TimelineView (video / audio / audio-clips / text / image lanes, keyframe diamonds) + TimelinePanel
      toolbar.py       BottomToolbar + CATEGORIES (Video, Audio, Effects, Text, Image, Project, Export)
      panels/
        audio_panel.py AudioPanel (volume, mute, fades)
        fade_panel.py  FadePanel (video fade in / out)
        text_panel.py  TextPanel (text, size, colours, outline, shadow, opacity, start / end, x / y)
        image_panel.py ImagePanel (start / end, x / y, scale, opacity, order)
        speed_panel.py SpeedPanel (0.25x - 4x)
        keyframe_panel.py KeyframePanel (add / delete / clear / copy / paste keys at the playhead)
```
Where new code goes: clip/project data, export command builder, undo history, project save/load -> `pedio/core/`.
New tool dialogs/panels (crop, audio, text, export settings) -> `pedio/ui/` (a `pedio/ui/panels/` subfolder when there are several).

## Rules / gotchas
- `setup_paths()` must run before `import mpv` (adds the DLL folder). main.py does this. `player.py` imports mpv lazily.
- mpv callbacks run on mpv's thread: only emit Qt Signals from them, never touch widgets.
- mpv `vf=lavfi=[...]` filters (rotate / flip / crop / fade / speed) fail on GPU frames ("Impossible to convert ... src: d3d11"). So the player uses `hwdec="auto-copy-safe"` (copy-back), never plain `auto` / `auto-safe`. Settings can turn hardware decoding off.
- Never give a method in a QWidget subclass the name of a Qt method (`rect`, `size`, `update`, `width` ...): it replaces Qt's own and painting then fails silently (black widget).
- Seek with `player.command("seek", t, "absolute+exact" | "absolute+keyframes")` (keyframes while dragging, exact on release).
- libmpv needs vulkan-1.dll next to mpv-2.dll (setup_deps.py handles it). Archives with BCJ2 need 7-Zip (not py7zr).
- ffmpeg jobs run in QThreads with stop(); ignore signals from stale workers (`self.sender()` check).
- Timeline painting is cached in a QPixmap; the playhead is drawn on top. Call `_invalidate()` when data/zoom/scroll changes.
- Hide console windows on Windows: `creationflags=CREATE_NO_WINDOW` (see workers.py).
- Every edit goes through `Project` (never change a `Clip` from the UI directly): it emits `changed`, and the timeline + player follow. `History` listens to the same signal.
- `Project.changed` handler order matters: TimelineView (clamps the playhead) first, then `MainWindow.on_project_changed` (reloads the player).
- No gaps between video clips: clips are always joined end to end (delete / trim close the gap). Audio / text / images sit at their own times.
- The player reloads mpv only when the source list changes; neighbours that continue each other (same speed) are merged, so a plain split does not reload.
- Preview sound = one temporary m4a built by ffmpeg (`AudioMixWorker`) from the video sound + audio clips; `PlayerWidget.set_audio_mix(path)` sets mpv's `audio-file` and reloads at the same position. Debounced 500 ms in `MainWindow`. When there are no clips the mix is cleared.
- Video fades are per clip (`Clip.fade_in/fade_out`). Export uses `fade`/`afade` at the segment's own PTS 0. The preview adds `fade` filters with global timeline times to the player's `vf=lavfi` chain.
- Speed: `Clip.source_length` is the media range, `Clip.length = source_length / speed`. Export trims `-t source_length`, uses `setpts=PTS/speed` and `atempo` (chained for 0.25x / 4x), then trims the audio to `length`. Preview uses `setpts` in the player. Timeline thumbnails / waveform read source time = `src_in + timeline_offset * speed`.
- Text is ONE ASS file (`core/layers.py: build_ass`) for both preview (mpv `sub_file`) and export (ffmpeg `ass=` after the images, so event times are timeline seconds). Animated text is split at keyframes with `\move` / `\t`. `MainWindow._update_text_preview()` rewrites it only when the text signature changes (including opacity / anim).
- Images: `ImageLayer` list order is the drawing order. Preview = `ImageOverlay` widget; export = `-loop 1` inputs + `scale` + `colorchannelmixer=aa` + `overlay=...:enable='between(t,start,end)'`. Text is drawn after the images.
- Keyframes are plain data on the layer / audio clip (`anim`: `{prop: [Keyframe(t, value, easing)]}`) and use timeline seconds. Preview + export both read them from `core/keyframes.py` (ASS tags, Qt overlays, ffmpeg `volume='...'`).
- Export uses `Project.play_list()` (same list as the player). Each segment is cut with input seeking (`-ss/-t -i`), scaled/padded to the first clip's size + fps, joined with `concat`; a clip without audio gets `anullsrc` silence. The file is written as `<name>.<ext>.part` and renamed when complete.
- New per-clip effects (crop, rotate, fades, speed, text, images) go into the per-segment filter chain in `build_command()`; new options go into `ExportSettings`.
- Keep every effect as plain data on the clip / layer, applied in ONE place for the preview and ONE place for the export (one source of truth).
- Limit: every segment adds about 400 characters to the ffmpeg command line (Windows allows about 32000), so an edit with 60+ separate segments would fail. If that ever happens: write the filter to a file (`-/filter_complex file` on ffmpeg 7+).
- Undo compares `project_to_dict()` snapshots; restoring sets `_blocked` so the restore's `changed` does not create another undo step. Selection changes are not undo steps.

## Main APIs (as built)
- `Project` (core/clips.py): `clips` (`Clip(path, src_duration, src_in, src_out, id, size, meta_rotation, transform, fade_in, fade_out, speed, anim)`), `audio_clips` (`AudioClip`), `text_layers`, `image_layers`, `selected_id`, `selected_audio_id`, `selected_text_id`, `selected_image_id`, signals `changed`, `selection_changed`. Reading: `total()`, `extent()`, `starts()`, `boundaries()`, `index_of(id)`, `clip_at(t)`, `text_by_id`, `text_index_of`, `image_by_id`, `image_index_of`, `audio_by_id`, `audio_index_of`, `play_list()` (`Segment(path, start, length, transform, meta_rotation, fade_in, fade_out, speed)`, `source_length`). Edits: `add_clip`, `add_audio_clip`, `remove_audio_clip`, `split(t)`, `delete(id)`, `trim_start`, `trim_end`, `trim_start_at`, `trim_end_at`, `move`, `select`, `rotate`, `flip`, `set_crop`, `set_fades`, `set_speed(id, speed)` (0.25-4), `add_text_layer`, `remove_text_layer`, `update_text_layer`, `select_text`, `add_image_layer`, `remove_image_layer`, `move_image_layer(id, index)`, `update_image_layer`, `select_image`, `set_aspect_all`, `set_transform`, `set_keyframe(kind, id, prop, t, value, easing)`, `remove_keyframe`, `clear_keyframes`, `copy_anim`, `clear_selection`. `MIN_CLIP` = 0.1 s, `MIN_SPEED` = 0.25, `MAX_SPEED` = 4.0. `_KEY_LIMITS`: x / y / opacity 0..1, size 8..400, scale 0.05..2, volume 0..2.
- `TextLayer`: `text, start, end, x, y` (fractions), `size` (1920 x 1080 canvas), `color, outline, outline_color, shadow, shadow_color`, `opacity`, `anim`, `id`; `value(prop, t)`, `copy()`. `ImageLayer`: `path, start, end, x, y` (top-left fraction of free space), `scale` (frame width fraction), `opacity`, `anim`, `id`; `value`, `copy()`. `build_ass([layers])`.
- `AudioClip`: `path`, `start`, `src_in`, `src_out`, `volume`, `mute`, `fade_in`, `fade_out`, `anim`, `id`; `length`, `end`, `volume_at(t)`.
- `project_io`: `project_to_dict(project)`, `apply_project_dict(project, data)`, `save_project(project, path)`, `read_project(path)`, `open_project(project, path)`; VERSION 2.
- `History(project, limit=100)`: `can_undo`, `can_redo`, `undo`, `redo`, `reset`; signal `changed`.
- `PlayerWidget` signals: `file_loaded(str)`, `load_failed(str)`, `position_changed(float)`, `duration_changed(float)`, `playing_changed(bool)`, `muted_changed(bool)`. Methods: `set_edit_list([Segment], position)`, `set_audio_mix(path)`, `audio_mix_path()`, `set_subtitles(path)`, `toggle_play()`, `pause()`, `refresh()`, `seek_to(sec, exact=True)`, `set_muted(bool)`, `toggle_mute()`, `is_muted()`, `set_hwdec(bool)`, `position()`, `duration()`, `shutdown()`.
- `TimelinePanel(player, project)`: `position()`, `set_playhead(t)`, `shutdown()`. Inner `TimelineView(project)`: `set_zoom`, `zoom_fit`, `set_position`, `set_playhead`, `set_snap`, signal `seek_requested(float, bool)`. Lanes: video, audio, audio clips, text, images.
- `BottomToolbar` signal `tool_clicked(category, tool)`; `MainWindow.on_tool()` handles Import, trims, Split, Delete, Crop, Aspect, Rotate, Flips, Speed, audio tools, fades, Add / Edit Text, Keyframes, Add Image / Image Settings, Undo / Redo, Save / Open Project, Settings, Export Video / Settings.
- Export (core/export.py): `ExportSettings(crf, preset, audio_bitrate, size, fps, format)` (`format`: mp4 / mkv / webm); `atempo_chain(speed)`; `build_command(ffmpeg, clips, sources, out_path, settings, audio_clips=(), text_layers=(), ass_name=None, image_layers=())`; `ExportWorker(... image_layers=None)` signals `progress`, `done`, `failed`, `cancelled`, `stop()`. `ExportDialog(... image_layers=None)`. `MainWindow.export_video()` asks for the name, pauses the preview, refuses a source file and opens the dialog. MP4 / MKV = libx264 + AAC + faststart (MP4 only); WebM = libvpx-vp9 + libopus. `build_audio_mix_command()` + `AudioMixWorker(clips, audio_clips, path, generation)` make the preview mix.
- workers: thumbnails about 150 per video (`thumb_step`, source time = `src_in + offset * speed`), waveform 200 peaks per second, `ProbeWorker`, `AudioProbeWorker`, `FrameWorker(path, t, filters)`.
- Picture (core/transform.py): `Transform(rotate, flip, crop)`; `is_default`, `turned()`, `mirrored()`, `with_crop()`; `video_filters(transform, meta_rotation, with_crop)`; `ASPECT_PRESETS`, `centered_crop`, `output_size`, `clean_crop`. `CropEditor.start(clip, source_time)`; signals `applied(rect or None)`, `cancelled`.
