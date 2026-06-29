#!/usr/bin/env python3
"""Inspect local ONNX face swapper model contracts.

Examples:
    python3 tools/inspect_face_swapper_models.py
    python3 tools/inspect_face_swapper_models.py --model models/hyperswap_1b_256.onnx
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
MODELS_DIR = ROOT_DIR / "models"


def _shape_to_text(value_info) -> str:
    dims = []
    tensor_type = value_info.type.tensor_type
    for dim in tensor_type.shape.dim:
        if dim.dim_value:
            dims.append(str(dim.dim_value))
        elif dim.dim_param:
            dims.append(dim.dim_param)
        else:
            dims.append("?")
    return "[" + ", ".join(dims) + "]"


def _elem_type_to_text(value_info) -> str:
    try:
        import onnx
        elem_type = value_info.type.tensor_type.elem_type
        return onnx.TensorProto.DataType.Name(elem_type)
    except Exception:
        return "unknown"


def inspect_model(model_path: Path) -> None:
    try:
        import onnx
    except ImportError:
        print("ERROR: onnx is not installed. Install project requirements first.")
        raise SystemExit(1)

    print(f"\n{model_path}")
    model = onnx.load(str(model_path), load_external_data=False)
    opsets = ", ".join(
        f"{op.domain or 'ai.onnx'}:{op.version}" for op in model.opset_import
    )
    print(f"  opset: {opsets}")
    print("  inputs:")
    for value_info in model.graph.input:
        print(
            "    "
            f"{value_info.name}: {_elem_type_to_text(value_info)} "
            f"{_shape_to_text(value_info)}"
        )
    print("  outputs:")
    for value_info in model.graph.output:
        print(
            "    "
            f"{value_info.name}: {_elem_type_to_text(value_info)} "
            f"{_shape_to_text(value_info)}"
        )
    print(f"  initializers: {len(model.graph.initializer)}")


def iter_models() -> list[Path]:
    if not MODELS_DIR.is_dir():
        return []
    return sorted(MODELS_DIR.glob("*.onnx"), key=lambda path: path.name.lower())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--model",
        help="Specific ONNX model path. Defaults to every .onnx file in models/.",
    )
    args = parser.parse_args()

    if args.model:
        model_paths = [Path(args.model).expanduser()]
    else:
        model_paths = iter_models()

    if not model_paths:
        print(f"No ONNX models found in {MODELS_DIR}.")
        return

    for model_path in model_paths:
        if not model_path.is_absolute():
            model_path = Path(os.getcwd()) / model_path
        if not model_path.exists():
            print(f"Missing model: {model_path}")
            continue
        inspect_model(model_path)


if __name__ == "__main__":
    main()
