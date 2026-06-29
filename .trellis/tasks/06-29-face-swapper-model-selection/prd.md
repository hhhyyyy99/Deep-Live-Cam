# Support face swapper model selection

## Goal

Allow users to choose the primary face swapper ONNX model from inside the desktop application instead of relying on the current hard-coded `inswapper_128_fp16.onnx` / `inswapper_128.onnx` preference.

## User Value

- Users can compare compatible swapper model variants without renaming files or editing code.
- The current default behavior remains available for users who do not need model selection.
- The feature is visible in the same app surface as existing face processing controls.

## Confirmed Facts From Repository

- The main face swapper model is loaded in `modules/processors/frame/face_swapper.py`.
- `get_face_swapper()` currently caches one global `FACE_SWAPPER` instance and chooses:
  - `models/inswapper_128_fp16.onnx` when CUDA + fp16 file are available.
  - `models/inswapper_128.onnx` otherwise.
- `pre_check()` currently downloads `inswapper_128.onnx` if needed.
- `pre_start()` currently accepts either `inswapper_128_fp16.onnx` or `inswapper_128.onnx`.
- The PySide UI already has a Face Enhancer dropdown in `modules/ui.py`, but no dropdown for the primary face swapper model.
- The `models/` folder is the existing local model directory; the current checkout only has `models/instructions.txt`.
- The face enhancer dropdown is separate and should not be conflated with the main swapper model.

## Requirements

- Add an in-app control for selecting the primary face swapper model.
- Preserve the current automatic default behavior unless the user chooses a specific model.
- Discover selectable models by scanning the current contents of the existing `models/` directory.
- Allow the model selector to refresh from `models/` so files added or removed during a UI session can be reflected without restarting the app.
- Limit the selection to files that are intended to be compatible primary face swapper ONNX models; face enhancer and detector models must not appear as primary swapper choices.
- Store the selected model in application state so live webcam, window capture, image processing, and video processing use the same selected swapper.
- Reload the cached face swapper when the selected model changes.
- Support hot model switching while a live preview is running: the current preview should keep running, the cached swapper should be invalidated, and the next swap should load the newly selected model. A short loading pause is acceptable.
- Validate that the selected model file exists before processing starts.
- Show a clear status/error message when the selected model is missing or fails to load.
- Existing desktop flows must continue to work when no explicit model is selected.

## Acceptance Criteria

- [ ] The desktop UI exposes a primary face swapper model selector near existing face processing options.
- [ ] The selector includes an automatic/default option that preserves the current fp16-then-fp32 preference.
- [ ] Compatible swapper model files currently present in `models/` are available as selectable choices.
- [ ] The user can refresh the selector so the list matches the current `models/` directory contents.
- [ ] Choosing a model causes subsequent swap operations to load and use that exact model path.
- [ ] Changing the selection invalidates the cached `FACE_SWAPPER` instance so the old model is not reused.
- [ ] Changing the selection during a running live preview applies to subsequent frames without requiring the preview window to be closed and reopened.
- [ ] Missing selected model files block processing with an actionable status message.
- [ ] Existing Face Enhancer choices remain independent.
- [ ] Existing behavior is unchanged when the automatic/default option is selected.

## Notes

- Keep this as a lightweight PRD-only task unless implementation uncovers a broader model compatibility issue.

## Out Of Scope

- Adding or training new face swapper models.
- Supporting arbitrary ONNX model architectures that `insightface.model_zoo.get_model()` cannot load as a face swapper.
- Changing face enhancer model selection.
- Mobile screen capture / LAN streaming work.

## Decisions

- Model changes should hot-apply while a live preview is running. The preview may briefly pause while the newly selected ONNX model loads.
