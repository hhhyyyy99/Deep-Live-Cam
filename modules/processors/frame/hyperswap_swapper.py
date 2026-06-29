"""Hyperswap face swapper adapter.

Hyperswap ONNX models are not InsightFace model-zoo models.  They need a
dedicated preprocessing/inference/postprocessing path before they can expose
the same ``get(frame, target_face, source_face, paste_back=False)`` contract
that the rest of the live pipeline already uses for INSwapper.
"""

from typing import Any, List


class HyperswapAdapterNotImplemented(RuntimeError):
    pass


class HyperswapSwapper:
    def __init__(self, model_path: str, providers: List[Any]):
        self.model_path = model_path
        self.providers = providers
        raise HyperswapAdapterNotImplemented(
            "Hyperswap model detected, but the Hyperswap adapter is not implemented yet. "
            "Run `python3 tools/inspect_face_swapper_models.py --model "
            f"{model_path}` and use the reported inputs/outputs to implement "
            "modules.processors.frame.hyperswap_swapper.HyperswapSwapper."
        )
