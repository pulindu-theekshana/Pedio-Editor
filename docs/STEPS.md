# Pedio Editor - build plan (mini-steps)

One chat = ONE mini-step (6a, 6b, 7a ...). Each is small: 1 feature, 1-3 files, preview and export together
(unless the line says "no UI"). When it is done: tick it here, commit, push, stop.

Prompt to paste into a new chat (change the id):
```
Read CLAUDE.md, then mini-step 6a in docs/STEPS.md. Do only that one. Read only the files listed there.
No test scripts (py_compile only). Commit as me and push. Then tick it and give me the next prompt.
```

## Done
1 setup + layout, 2 import + player, 3 timelines, 4 trim / split / delete / reorder / snap, 5 export (owner confirmed it works on Windows).

## Step 6 - Picture: rotate, flip, aspect ratio, crop - DONE
Tested on Windows: preview, export and timeline thumbnails match. Fixed on the way: mpv `hwdec=auto-copy-safe` (GPU frames broke the filters), `CropView.rect` clashed with `QWidget.rect`.
- [x] 6a Rotate, flip, aspect presets (preview = export)
- [x] 6b Crop box + crop in export; CLAUDE.md updated; merged into main

## Step 7 - Audio
- [ ] 7a Audio track model + export mix, no UI. Files: core/audio.py (AudioClip: path, start, in/out, volume, mute), core/clips.py (add / remove), core/export.py (amix)
- [ ] 7b Audio lane on the timeline, import MP3 / WAV / AAC, extract audio from a video. Files: ui/timeline.py, ui/main_window.py, ui/toolbar.py
- [ ] 7c Preview plays the mix, volume per clip, mute. Files: ui/player.py, ui/panels/audio_panel.py
- [ ] 7d Audio fade in/out at the ends (0.1-2 s slider): data + preview + export

## Step 8 - Video fades
- [ ] 8a Fade data at splits / joins + export (`fade` filter in build_command()). Files: core/clips.py, core/export.py
- [ ] 8b Preview fade + 0.1-2 s slider. Files: ui/player.py, ui/panels/

## Step 9 - Text overlay
- [ ] 9a Layer model, no UI: core/layers.py (TextLayer: text, size, color, shadow, outline size/color, start/end, x/y; image layers reuse it in Step 13) + export (drawtext)
- [ ] 9b Text on the preview + drag to position. Files: ui/player.py, ui/overlay_view.py
- [ ] 9c Text panel (size, color, shadow, outline) + text lane on the timeline for start/end. Files: ui/panels/text_panel.py, ui/timeline.py

## Step 10 - Speed + export settings
- [ ] 10a Clip speed: data + preview + export (setpts / atempo)
- [ ] 10b Export settings: resolution, quality, format. Files: core/export.py (ExportSettings), ui/export_dialog.py

## Step 11 - Project handling
- [ ] 11a Undo / redo. Files: core/history.py, core/clips.py
- [ ] 11b Save / open project file. Files: core/project_io.py
- [ ] 11c Autosave + full keyboard shortcuts (also pause the preview while exporting)

## Step 12 - Finish the simple editor
- [ ] 12a Dark theme + hardware decoding toggle
- [ ] 12b App icon + PyInstaller .exe (mpv-2.dll, vulkan-1.dll, ffmpeg)

## Advanced features (only after Step 12 works)
### Step 13 - Image overlays
- [ ] 13a ImageLayer in core/layers.py + export (overlay filter)
- [ ] 13b Preview + drag / scale on the preview
- [ ] 13c Image panel (position, scale, opacity, start/end, order) + lane

### Step 14 - Keyframes 1
- [ ] 14a Keyframe engine, pure + no UI: core/keyframes.py (value at time t; linear / hold / ease)
- [ ] 14b Animate layer position / scale / opacity: export + preview
- [ ] 14c Keyframe lane + diamonds on the timeline

### Step 15 - Keyframes 2
- [ ] 15a Animate the video clip (position, scale, rotation, opacity): preview + export
- [ ] 15b Animate text properties + audio volume
- [ ] 15c Copy / paste keyframes

### Step 16 - Smart masking 1
- [ ] 16a Check current model options and tell the owner the choice first; download on first use; must run on a CPU-only laptop. Files: core/ai/model.py
- [ ] 16b Brush on the preview (foreground / background / eraser). Files: ui/mask_brush.py
- [ ] 16c Mask of the current frame (run the model on one frame)

### Step 17 - Smart masking 2
- [ ] 17a Tracking worker (QThread, progress, cancel)
- [ ] 17b Mask cache on disk
- [ ] 17c Correction strokes on any frame + re-track from there

### Step 18 - Smart masking 3
- [ ] 18a Put text / graphics between subject and background: layer order + export compositing
- [ ] 18b Preview compositing + edge feather

### Step 19 - .exe with the AI parts
- [ ] 19a PyInstaller spec with the AI parts (model downloaded on first use, not bundled)
- [ ] 19b Build + check commands

## Notes
- Steps 4 and 5 were tested on Linux only, never on Windows.
- The preview keeps playing while the export dialog is open (harmless; pause it in 11c).
- There is no undo until 11a: Delete cannot be undone.
