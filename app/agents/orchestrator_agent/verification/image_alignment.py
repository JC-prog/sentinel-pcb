"""Ported verbatim from orchestrator-agent/adc_agentic_project's verification/image_alignment.py."""

import cv2
import numpy as np

def estimate_translation(golden_path: str, defect_path: str):
    g = cv2.imread(golden_path, cv2.IMREAD_GRAYSCALE)
    d = cv2.imread(defect_path, cv2.IMREAD_GRAYSCALE)
    if g is None or d is None:
        return {"success": False, "reason": "IMAGE_UNREADABLE"}

    h = min(g.shape[0], d.shape[0])
    w = min(g.shape[1], d.shape[1])
    if h < 8 or w < 8:
        return {"success": False, "reason": "IMAGE_TOO_SMALL"}

    g = cv2.resize(g, (w, h)).astype(np.float32)
    d = cv2.resize(d, (w, h)).astype(np.float32)
    g = (g - g.mean()) / (g.std() + 1e-6)
    d = (d - d.mean()) / (d.std() + 1e-6)

    (dx, dy), response = cv2.phaseCorrelate(g, d)
    return {
        "success": True,
        "dx": float(dx),
        "dy": float(dy),
        "shift_pixels": float((dx*dx + dy*dy) ** 0.5),
        "response": float(response),
    }
