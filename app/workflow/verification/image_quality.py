import cv2
import numpy as np

def image_quality(path: str):
    image = cv2.imread(path, cv2.IMREAD_COLOR)
    if image is None:
        return {"readable": False}
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return {
        "readable": True,
        "width": int(image.shape[1]),
        "height": int(image.shape[0]),
        "channels": int(image.shape[2]),
        "brightness": float(gray.mean()),
        "contrast": float(gray.std()),
        "blur_score": float(cv2.Laplacian(gray, cv2.CV_64F).var()),
    }
