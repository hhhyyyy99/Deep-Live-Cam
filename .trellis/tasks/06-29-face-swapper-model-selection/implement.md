# Face Swapper Model Selection Implementation Plan

## Completed In This Step

- Add model type detection for INSwapper, Hyperswap, and unknown ONNX files.
- Route Hyperswap models to a dedicated adapter entry point instead of the
  InsightFace loader.
- Add `tools/inspect_face_swapper_models.py` to inspect ONNX inputs/outputs.
- Add tests covering model type detection and Hyperswap dispatch.

## Next Step For Real Hyperswap Support

1. Run:

   ```bash
   python3 tools/inspect_face_swapper_models.py --model models/hyperswap_1b_256.onnx
   ```

2. Record:
   - input names, shapes, and dtypes
   - output names, shapes, and dtypes
   - opset version
   - whether additional identity/embedding inputs are required

3. Implement `modules.processors.frame.hyperswap_swapper.HyperswapSwapper`:
   - create an ONNX Runtime session
   - align source and target faces to the model input size
   - normalize tensors according to the model contract
   - run inference
   - return `(bgr_fake, M)` when `paste_back=False`

4. Validate with:

   ```bash
   python3 -m unittest discover tests
   env PYTHONPYCACHEPREFIX=/private/tmp/deep-live-cam-pycache python3 -m py_compile modules/processors/frame/face_swapper.py modules/processors/frame/hyperswap_swapper.py tools/inspect_face_swapper_models.py
   ```

## Rollback Point

If Hyperswap adapter implementation proves incompatible with the available
model files, keep the type detection and inspection script but leave
`HyperswapSwapper` explicit about the missing adapter contract.
