"""Hyperswap ONNX adapter for the face swapper pipeline."""

from __future__ import annotations

from typing import Any, List, Optional, Tuple

import cv2
import numpy as np
import onnxruntime

from modules.typing import Face, Frame


ARCFACE_128_TEMPLATE = np.array(
    [
        [0.36167656, 0.40387734],
        [0.63696719, 0.40235469],
        [0.50019687, 0.56044219],
        [0.38710391, 0.72160547],
        [0.61507734, 0.72034453],
    ],
    dtype=np.float32,
)


class HyperswapSwapper:
    """Expose Hyperswap as an INSwapper-like ``get(...)`` object."""

    def __init__(self, model_path: str, providers: List[Any]):
        self.model_path = model_path
        self.session = onnxruntime.InferenceSession(model_path, providers=providers)
        self.input_names = [input_meta.name for input_meta in self.session.get_inputs()]
        self.output_names = [output_meta.name for output_meta in self.session.get_outputs()]
        self.source_input_name = self._find_input_name("source", fallback_index=0)
        self.target_input_name = self._find_input_name("target", fallback_index=1)
        self.output_name = self._find_output_name("output", fallback_index=0)
        self.mask_output_name = self._find_output_name("mask", fallback_index=1)
        target_size = self._infer_target_size() or 256
        self.input_size = (target_size, target_size)

    def _find_input_name(self, expected: str, fallback_index: int) -> str:
        for name in self.input_names:
            if name.lower() == expected:
                return name
        return self.input_names[fallback_index]

    def _find_output_name(self, expected: str, fallback_index: int) -> Optional[str]:
        for name in self.output_names:
            if name.lower() == expected:
                return name
        if fallback_index < len(self.output_names):
            return self.output_names[fallback_index]
        return None

    def _infer_target_size(self) -> Optional[int]:
        for input_meta in self.session.get_inputs():
            if input_meta.name != self.target_input_name:
                continue
            shape = input_meta.shape
            if len(shape) == 4 and isinstance(shape[2], int) and shape[2] == shape[3]:
                return shape[2]
        return None

    def get(
        self,
        img: Frame,
        target_face: Face,
        source_face: Face,
        paste_back: bool = True,
    ):
        aligned_target, affine_matrix = _align_target_face(img, target_face, self.input_size[0])
        if aligned_target is None or affine_matrix is None:
            return None if paste_back else (None, None)

        source = _source_embedding(source_face)
        if source is None:
            return None if paste_back else (None, None)

        target = _preprocess_target(aligned_target, self.input_size[0])
        outputs = self.session.run(
            self.output_names,
            {
                self.source_input_name: source,
                self.target_input_name: target,
            },
        )
        output_map = dict(zip(self.output_names, outputs))
        fake_face = _postprocess_output(output_map[self.output_name])

        if paste_back:
            raise NotImplementedError("HyperswapSwapper only supports paste_back=False")
        return fake_face, affine_matrix


def _target_landmarks(face: Face) -> Optional[np.ndarray]:
    if hasattr(face, "kps") and face.kps is not None:
        landmarks = face.kps
    elif hasattr(face, "landmark_2d_106") and face.landmark_2d_106 is not None:
        lm106 = face.landmark_2d_106
        landmarks = np.array(
            [lm106[38], lm106[88], lm106[86], lm106[52], lm106[61]],
            dtype=np.float32,
        )
    else:
        return None

    landmarks = np.asarray(landmarks, dtype=np.float32)
    if landmarks.shape[0] < 5:
        return None
    return landmarks[:5]


def _align_target_face(
    frame: Frame, face: Face, input_size: int
) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
    landmarks = _target_landmarks(face)
    if landmarks is None:
        return None, None

    template = ARCFACE_128_TEMPLATE * input_size
    affine_matrix, _ = cv2.estimateAffinePartial2D(
        landmarks,
        template,
        method=cv2.RANSAC,
        ransacReprojThreshold=100,
    )
    if affine_matrix is None:
        return None, None

    aligned_face = cv2.warpAffine(
        frame,
        affine_matrix,
        (input_size, input_size),
        flags=cv2.INTER_AREA,
        borderMode=cv2.BORDER_REPLICATE,
    )
    return aligned_face, affine_matrix


def _source_embedding(source_face: Face) -> Optional[np.ndarray]:
    embedding = getattr(source_face, "embedding_norm", None)
    if embedding is None:
        embedding = getattr(source_face, "normed_embedding", None)
    if embedding is None:
        return None

    embedding = np.asarray(embedding, dtype=np.float32).reshape(1, -1)
    if embedding.shape[1] != 512:
        return None
    return embedding.astype(np.float32)


def _preprocess_target(aligned_target: np.ndarray, input_size: int) -> np.ndarray:
    if aligned_target.shape[0] != input_size or aligned_target.shape[1] != input_size:
        aligned_target = cv2.resize(
            aligned_target,
            (input_size, input_size),
            interpolation=cv2.INTER_LINEAR,
        )
    rgb = cv2.cvtColor(aligned_target, cv2.COLOR_BGR2RGB)
    tensor = rgb.astype(np.float32) / 255.0 * 2.0 - 1.0
    tensor = np.transpose(tensor, (2, 0, 1))[np.newaxis, ...]
    return tensor.astype(np.float32)


def _postprocess_output(output: np.ndarray) -> np.ndarray:
    face = output[0].transpose(1, 2, 0)
    face = face * 0.5 + 0.5
    face = np.clip(face, 0.0, 1.0)
    face = (face * 255.0).astype(np.uint8)
    return cv2.cvtColor(face, cv2.COLOR_RGB2BGR)
