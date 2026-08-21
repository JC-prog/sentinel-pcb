# Model Training Guide — SentinelPCB

*What actually needs training, where to get data for it, and how to produce something
`app/agents/multi_modal_inference/` can load. Companion to `docs/AGENT-DESIGN.md`.*

---

## 1. What needs training (and what doesn't)

| Model | Status | Needs training? |
|---|---|---|
| Feature detector (`models/pcb_feature_detector.onnx`) | Already trained, in use | No — retrain only if you want better feature detection |
| **Defect classifier** (`app/agents/multi_modal_inference/defect_classifier.py`) | **Deterministic placeholder today** | **Yes — this is the one this doc is about** |
| Embedding model (`sentence-transformers/all-MiniLM-L6-v2`, RAG) | Pretrained, downloaded on first use | No — never trained by us, used as-is |

The defect classifier's placeholder derives a fake "defect confidence" from the feature
detector's own output (see `docs/AGENT-DESIGN.md` / the implementation plan) purely so the rest
of the pipeline — the accept/escalate decision, Explainability's report, Learning Queue — can be
built and proven without a real model. Replacing it with a real one is a self-contained,
swap-in change; nothing else needs to know it happened.

---

## 2. Picking a dataset — mind the domain

There are two genuinely different kinds of "PCB defect" data. Getting this wrong means training
a model that's confidently answering the wrong question — same mistake already made once with
the feature detector (trained on structural features, not defects at all).

- **Bare-board fabrication defects** — `missing_hole`, `mouse_bite`, `open_circuit`, `short`,
  `spur`, `spurious_copper`. Inspects the unpopulated board itself, before any component is
  soldered on. This is the famous, huge, easy-to-find benchmark (HRIPCB/DeepPCB family).
- **SMT assembly / solder defects** — bridging, insufficient/excess solder, tombstoning,
  misplaced component, cold solder. Inspects the board *after* components are placed and
  soldered — this is the domain `planning/docs/SCENARIOS.md`'s own illustrative labels
  (`Tombstone`, `Solder Bridge`, `Foreign Material`, `Misalignment`) actually describe.

**Recommendation: pick from the SMT/assembly domain** so the trained model's classes actually
match what the product narrative — and the placeholder's label set — already assume.

### Recommended (SMT/assembly domain)

| Dataset | Where | Notes |
|---|---|---|
| **SolDef_AI** | [Kaggle](https://www.kaggle.com/datasets/mauriziocalabrese/soldef-ai-pcb-dataset-for-defect-detection) | 1,150 soldered SMT component images, defect-free + defective; covers incorrect component positioning and excess/insufficient solder. Closest domain match found. |
| **Roboflow Universe — solder defect search** | [universe.roboflow.com/search?q=class:solder](https://universe.roboflow.com/search?q=class:solder) | Multiple community datasets with `cold solder`, `solder bridge`, `insufficient solder`, `missing component`, `solder ball`, `solder crack`. Same export workflow already used for `simulation/images/seed/`'s source dataset. |

### Also available (bare-board domain — wrong domain for this product's narrative, but larger/easier if that trade-off is acceptable)

| Dataset | Where | Notes |
|---|---|---|
| **keremberke/pcb-defect-segmentation** | [Hugging Face](https://huggingface.co/datasets/keremberke/pcb-defect-segmentation) | HuggingFace-native, no export step. |
| **PCB DEFECTS (HRIPCB family)** | [Roboflow Universe](https://universe.roboflow.com/pcbdefects-sini6/pcb-defects-2soi7) | The classic 6-class benchmark; many re-uploads of the same underlying set exist. |
| **DsPCBSD+** | [Nature Scientific Data, 2024](https://www.nature.com/articles/s41597-024-03656-8) | Newer, 9 categories, 20k+ annotated defects — larger and more diverse than the classic set. |

---

## 3. Training workflow

The existing feature detector is a YOLOv12-Medium model exported to ONNX with NMS baked in
(`app/agents/multi_modal_inference/detector.py`'s docstring: exported from
`https://huggingface.co/JcProg/PCBInspect-AI`). Follow the same toolchain (Ultralytics YOLO) so
the export shape matches what `PCBFeatureDetector`/the new classifier's loader already expects —
a `(1, 300, 6)` output of `[x1, y1, x2, y2, conf, cls_id]` rows, no separate NMS step needed at
inference time.

### 3.1 Get the data into YOLO format

Roboflow Universe datasets export directly to YOLO format (pick "YOLOv11" or "YOLOv12" export —
the label `.txt` format hasn't changed across recent YOLO versions, so either works):

1. Open the dataset page → **Download Dataset** → format **YOLOv11** → this repo's convention.
2. Unzip into a working directory with the standard Ultralytics layout:
   ```
   dataset/
     train/images/, train/labels/
     valid/images/, valid/labels/
     test/images/,  test/labels/
     data.yaml
   ```
3. Kaggle's SolDef_AI isn't pre-split into YOLO format — if you use it, you'll need to convert its
   annotations to YOLO `.txt` (one line per box: `class x_center y_center width height`,
   normalized 0–1) yourself, or re-annotate a subset via Roboflow's own upload+annotate flow
   (free tier is enough for a project this size).

### 3.2 Train

```bash
pip install ultralytics
yolo train \
  model=yolo12m.pt \
  data=dataset/data.yaml \
  imgsz=640 \
  epochs=100 \
  batch=16 \
  name=pcb-defect-classifier
```

- `imgsz=640` matches `settings.adc_input_size` — keep these in sync so both models share the
  same letterboxing/preprocessing assumptions in `detector.py`'s `_letterbox()`.
- `yolo12m.pt` (Medium) matches the existing model's size class — a reasonable default for a
  project this size; drop to `yolo12s.pt` (Small) if training is too slow on available hardware.
- Watch `runs/detect/pcb-defect-classifier/results.png` for the loss curves; stop early if
  validation loss plateaus well before 100 epochs.

### 3.3 Export to ONNX with NMS baked in

```bash
yolo export \
  model=runs/detect/pcb-defect-classifier/weights/best.pt \
  format=onnx \
  nms=True \
  imgsz=640
```

`nms=True` is what produces the `(1, 300, 6)` fixed-shape output `detector.py` parses directly —
without it, the export needs a separate NMS post-processing step this codebase doesn't have.

### 3.4 Produce the labels file

`detector.py::_labels_path()` derives the labels file by replacing the model's `.onnx` suffix
with `.labels.json` — e.g. `pcb_defect_classifier.onnx` → `pcb_defect_classifier.labels.json`.
Format: a JSON object mapping class index (as a string) to label name, matching your `data.yaml`'s
`names:` list order:

```json
{"0": "Solder Bridge", "1": "Insufficient Solder", "2": "Tombstone", "3": "Misplaced Component"}
```

### 3.5 Drop the files in and point settings at them

Place both files under `models/` (matching `pcb_feature_detector.onnx`'s convention — gitignored
via `models/*.onnx`, see `models/README.md`). A real defect classifier needs its own settings
field analogous to `adc_model_path` — e.g. `defect_model_path: str = "models/pcb_defect_classifier.onnx"`
— added to `app/settings.py` when this is actually wired in.

---

## 4. Swapping it into the pipeline

`app/agents/multi_modal_inference/defect_classifier.py`'s placeholder `predict()` method is the
only thing that needs to change. Once a real model exists:

1. Follow `detector.py::PCBFeatureDetector`'s exact pattern — load the ONNX session once via a
   `@lru_cache`-wrapped `get_defect_classifier()`, same letterboxing/preprocessing, same
   `Detection`-shaped output parsing.
2. Keep the `DefectPrediction{label, confidence, basis}` contract unchanged — everything
   downstream (the Orchestrator's policy decision, Explainability's report, Learning Queue) was
   built against that contract, not against how the confidence number is produced.
3. Delete the placeholder's feature→defect label-mapping heuristic — the real model predicts its
   own labels directly.

No other file needs to change. This is the entire point of keeping the placeholder in its own
module instead of inlining its logic into the Orchestrator.
