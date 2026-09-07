"""Builds a minimal but real ONNX classifier for tests, so the suite exercises a genuine
onnxruntime session rather than a mock.

Graph: input float32[1,3,H,W] -> GlobalAveragePool -> Flatten -> Gemm(W[3,K], b[K]) -> [1,K].
The Gemm weights are fixed, so a given image produces a deterministic prediction.
"""

from pathlib import Path

import numpy as np
import onnx
from onnx import TensorProto, helper


def build_tiny_onnx(path: str | Path, num_classes: int = 3) -> None:
    inp = helper.make_tensor_value_info("input", TensorProto.FLOAT, [1, 3, "H", "W"])
    out = helper.make_tensor_value_info("output", TensorProto.FLOAT, [1, num_classes])

    weight = np.linspace(-1.0, 1.0, 3 * num_classes, dtype=np.float32).reshape(3, num_classes)
    bias = np.zeros(num_classes, dtype=np.float32)

    nodes = [
        helper.make_node("GlobalAveragePool", ["input"], ["pooled"]),
        helper.make_node("Flatten", ["pooled"], ["flat"], axis=1),
        helper.make_node("Gemm", ["flat", "W", "b"], ["output"]),
    ]
    initializers = [
        helper.make_tensor("W", TensorProto.FLOAT, [3, num_classes], weight.flatten().tolist()),
        helper.make_tensor("b", TensorProto.FLOAT, [num_classes], bias.tolist()),
    ]

    graph = helper.make_graph(nodes, "tiny-classifier", [inp], [out], initializers)
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    model.ir_version = 9
    onnx.checker.check_model(model)
    onnx.save(model, str(path))
