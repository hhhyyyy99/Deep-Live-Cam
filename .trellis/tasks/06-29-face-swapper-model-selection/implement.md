# Face Swapper Model Selection Implementation Plan

## Completed In This Step

- Add model type detection for INSwapper, Hyperswap, and unknown ONNX files.
- Route Hyperswap models to a dedicated adapter entry point instead of the
  InsightFace loader.
- Add `tools/inspect_face_swapper_models.py` to inspect ONNX inputs/outputs.
- Add tests covering model type detection and Hyperswap dispatch.
- Implement first-pass `HyperswapSwapper` support for the inspected
  `source [1,512]` + `target [1,3,256,256]` contract.

## Next Step For Real Hyperswap Validation

1. In an environment with the actual model, run:

   ```bash
   python3 tools/inspect_face_swapper_models.py --model models/hyperswap_1b_256.onnx
   ```

2. Confirm the contract still matches:
   - `source: FLOAT [1, 512]`
   - `target: FLOAT [1, 3, 256, 256]`
   - `output: FLOAT [1, 3, 256, 256]`
   - `mask: FLOAT [1, 1, 256, 256]`

3. Run live/image swap with `hyperswap_1b_256.onnx` selected.

4. If output color or identity is wrong, tune:
   - source embedding normalization
   - target normalization range
   - output range conversion
   - aligned crop template

   ```bash
   python3 -m unittest discover tests
   env PYTHONPYCACHEPREFIX=/private/tmp/deep-live-cam-pycache python3 -m py_compile modules/processors/frame/face_swapper.py modules/processors/frame/hyperswap_swapper.py tools/inspect_face_swapper_models.py
   ```

## Rollback Point

If visual validation shows the inspected contract is insufficient, keep the
model type detection and inspection script, then revise `HyperswapSwapper`
against the actual preprocessing/postprocessing from the model source.
