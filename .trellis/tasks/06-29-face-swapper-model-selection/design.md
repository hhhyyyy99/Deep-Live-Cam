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

`HyperswapSwapper` implements the inspected `hyperswap_1b_256.onnx` contract:

- `source`: `FLOAT [1, 512]`
- `target`: `FLOAT [1, 3, 256, 256]`
- `output`: `FLOAT [1, 3, 256, 256]`
- `mask`: `FLOAT [1, 1, 256, 256]`

The adapter follows FaceFusion's Hyperswap contract: use
`source_face.embedding_norm` as the source input, align the target face to a
256x256 `arcface_128` crop, normalize target RGB to `[-1, 1]`, run ONNX
Runtime, convert output with `output * 0.5 + 0.5`, and return `(bgr_fake, M)`
for the existing paste-back path. The model `mask` output is currently not used;
the existing paste-back/masking path owns compositing.

The first implementation still needs visual validation in an environment that
has the actual Hyperswap model file.

## Compatibility Notes

- Existing INSwapper behavior stays unchanged.
- UI model listing still scans the `models/` directory.
- Hyperswap support must not require live preview/window capture changes once
  the adapter implements the stable `get(...)` contract.
