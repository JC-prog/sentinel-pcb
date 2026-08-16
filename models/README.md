# models/

`pcb_feature_detector.onnx` is not committed (80 MB binary, .gitignore'd) - regenerate it with
the `pcb-inspect-ai` repo's export script:

```bash
cd ../pcb-inspect-ai
python -m venv .venv-export && source .venv-export/Scripts/activate  # or bin/activate on Linux/Mac
pip install --index-url https://download.pytorch.org/whl/cpu torch torchvision
pip install "ultralytics==8.3.197" huggingface_hub onnxscript
python scripts/export_onnx.py
cp demo/checkpoint/YoloV12-Medium-160-FineTuned.onnx ../sentinel-pcb/models/pcb_feature_detector.onnx
cp demo/checkpoint/labels.json ../sentinel-pcb/models/pcb_feature_detector.labels.json
```

`pcb_feature_detector.labels.json` **is** committed - it's the class-index -> name mapping the
ONNX graph itself doesn't carry, and it's small/deterministic enough to version like code.

See `docs/ADC-Design-Doc-Team10-V2.md` §8 for why ONNX (platform-agnostic, no torch/CUDA/
ultralytics dependency chain in the deployed monolith - see `pyproject.toml`: only `onnxruntime`
+ `pillow` + `numpy`, not the full `pcb-inspect-ai/demo/requirements.txt` stack).
