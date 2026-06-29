# Face Swapper Model Selection Design

## Architecture

The face swapper pipeline keeps a stable runtime contract:

```python
swapper.get(frame, target_face, source_face, paste_back=False)
```

`modules.processors.frame.face_swapper` owns model discovery, selection,
cache invalidation, model type detection, and dispatch to a concrete adapter.

## Model Types

- `inswapper`: existing InsightFace-compatible models, loaded through
  `insightface.model_zoo.get_model`.
- `hyperswap`: Hyperswap-family ONNX models. These are not InsightFace model-zoo
  models and require a dedicated adapter.
- `unknown`: listed as local ONNX candidates, but still loaded through the
  legacy InsightFace path until a concrete adapter exists.

## Data Flow

1. UI scans `models/*.onnx` and stores the selected file name in
   `modules.globals.face_swapper_model`.
2. `set_face_swapper_model()` invalidates cached model state and CUDA graph
   state.
3. `get_face_swapper()` resolves the selected model path and detects the model
   type.
4. INSwapper models use the existing InsightFace loader.
5. Hyperswap models dispatch to `HyperswapSwapper`.

## Hyperswap Adapter Status

`HyperswapSwapper` is currently an explicit adapter placeholder. It prevents
Hyperswap models from going through the wrong InsightFace loader and emits an
actionable message pointing to the inspection tool.

The next implementation step depends on the actual ONNX input/output contract
from `tools/inspect_face_swapper_models.py`.

## Compatibility Notes

- Existing INSwapper behavior stays unchanged.
- UI model listing still scans the `models/` directory.
- Hyperswap support must not require live preview/window capture changes once
  the adapter implements the stable `get(...)` contract.
