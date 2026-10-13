"""Turns a decoded PIL image into the float tensor a model's ONNX graph expects, per its
ModelSpec (size, channel order, layout, normalization)."""

import numpy as np
from PIL import Image

from inference_service.manifest import ModelSpec


def preprocess(image: Image.Image, spec: ModelSpec) -> np.ndarray:
    height, width = spec.input_size
    resized = image.convert("RGB").resize((width, height), Image.Resampling.BILINEAR)

    arr = np.asarray(resized, dtype=np.float32) / 255.0  # H x W x C, RGB, 0..1
    if spec.color == "BGR":
        arr = arr[:, :, ::-1]

    mean = np.array(spec.mean, dtype=np.float32)
    std = np.array(spec.std, dtype=np.float32)
    arr = (arr - mean) / std

    if spec.layout == "NCHW":
        arr = np.transpose(arr, (2, 0, 1))

    return np.ascontiguousarray(arr[np.newaxis, ...], dtype=np.float32)
