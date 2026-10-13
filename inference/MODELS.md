# Model cards

Per-model documentation (architecture, training data, validation/test metrics, known
limitations) for the four classifiers `models.toml` deploys. This isn't needed to run the
service - `models.toml` plus `scripts/fetch_models.py` is the actual source of truth for what
gets loaded - but it's worth keeping close to the models it describes rather than only on
Hugging Face.

**Provenance:** consolidated from `pcb_agentic_inspector`'s own copy of these model cards
(`models/{feature,body,lead,text}/MODEL_README.md`), found there when that project was pulled
into `app/workflow/` for integration. Its `models/*.onnx` + `labels.json` files were the same
four models already deployed here (same Hugging Face repos, same labels, same input sizes - see
the `models.toml` name given under each heading below) loaded directly instead of through this
service, so those files were deleted as duplicates rather than kept; only the documentation was
worth carrying over. It has not been re-verified against the currently deployed `revision` of
each model - treat the metrics below as being for whatever revision the sibling repos' commit
history says they were, not necessarily `models.toml`'s current pin.

## `pcb_region` - PCBInspect-Region

Sibling repos:
[PCBInspect-Region](https://huggingface.co/JcProg/PCBInspect-Region),
[PCBInspect-BodyDefect](https://huggingface.co/JcProg/PCBInspect-BodyDefect),
[PCBInspect-LeadDefect](https://huggingface.co/JcProg/PCBInspect-LeadDefect),
[PCBInspect-TextDefect](https://huggingface.co/JcProg/PCBInspect-TextDefect).

### Role

First stage. Given a component ROI crop, predicts which physical region it is (`Body`, `Lead`,
`Text`) so the pipeline can dispatch to the matching defect classifier. Near-trivial task - the
three regions look visually distinct.

### Model

- Base: `yolo26n-cls` ([Ultralytics](https://docs.ultralytics.com/models/yolo26/)), classification
  head, fine-tuned on AOI component-ROI crops.
- Export: ONNX, opset 17, no NMS (classification only) - single input `images` `(1, 3, 224, 224)`
  RGB, normalized `/255`, NCHW; single output `output0` `(1, 3)` raw logits (apply softmax
  yourself for probabilities) - matches `models.toml`'s `pcb_region` entry.
- Classes (3), index order = labels: **Body, Lead, Text**.

### Data

Trained on a proprietary AOI dataset of SMT component-ROI crops (paired defect-free reference +
defective capture per physical site), not publicly released. Split is grouped by physical capture
site (never by raw image) so a component's reference and defect crop never straddle
train/val/test.

### Metrics

**val** (top-1 1.000, macro-F1 1.000, n=2004):

| class | precision | recall | f1 | support |
|---|---|---|---|---|
| Body | 0.999 | 1.000 | 1.000 | 1174 |
| Lead | 1.000 | 0.999 | 0.999 | 738 |
| Text | 1.000 | 1.000 | 1.000 | 92 |

**test** (top-1 1.000, macro-F1 1.000, n=2002):

| class | precision | recall | f1 | support |
|---|---|---|---|---|
| Body | 0.999 | 1.000 | 1.000 | 1164 |
| Lead | 1.000 | 0.999 | 0.999 | 736 |
| Text | 1.000 | 1.000 | 1.000 | 102 |

### Limitations

None observed; effectively solved on held-out data (n=2,002 test).

## `pcb_body_defect` - PCBInspect-BodyDefect

### Role

Defect classifier for crops routed as `Body` by the region classifier. The hardest of the three
specialists - six classes with real class imbalance.

### Model

- Base: `yolo26s-cls`, classification head, fine-tuned on AOI component-ROI crops.
- Export: ONNX, opset 17, no NMS - single input `images` `(1, 3, 640, 640)` RGB, normalized
  `/255`, NCHW; single output `output0` `(1, 6)` raw logits - matches `models.toml`'s
  `pcb_body_defect` entry.
- Classes (6), index order = labels: **ForeignMaterial, Golden, MissingPart, Shift, Tombstone,
  WrongPart**.

### Data

Same proprietary AOI dataset and physical-site-grouped split as `pcb_region` above.

### Metrics

**val** (top-1 0.948, macro-F1 0.875, n=784):

| class | precision | recall | f1 | support |
|---|---|---|---|---|
| ForeignMaterial | 0.996 | 0.975 | 0.985 | 276 |
| Golden | 1.000 | 1.000 | 1.000 | 200 |
| MissingPart | 0.706 | 0.706 | 0.706 | 17 |
| Shift | 0.929 | 0.897 | 0.913 | 146 |
| Tombstone | 0.773 | 0.739 | 0.756 | 23 |
| WrongPart | 0.851 | 0.934 | 0.891 | 122 |

**test** (top-1 0.941, macro-F1 0.871, n=780):

| class | precision | recall | f1 | support |
|---|---|---|---|---|
| ForeignMaterial | 0.996 | 0.963 | 0.979 | 267 |
| Golden | 0.995 | 1.000 | 0.998 | 200 |
| MissingPart | 1.000 | 0.500 | 0.667 | 22 |
| Shift | 0.901 | 0.938 | 0.919 | 146 |
| Tombstone | 0.833 | 0.769 | 0.800 | 26 |
| WrongPart | 0.813 | 0.916 | 0.862 | 119 |

### Limitations

`MissingPart` and `Tombstone` are the weak classes (94 and 131 raw examples total, concentrated on
12 and 15 distinct part-numbers respectively). Test: MissingPart precision 1.00 / recall 0.50
(conservative - misses about half, never false-alarms); Tombstone F1 0.80. More examples across
more board/package designs would improve both.

## `pcb_lead_defect` - PCBInspect-LeadDefect

### Role

Defect classifier for crops routed as `Lead`. Binary: defect-free vs insufficient solder. Classes
are naturally balanced (~1,846 each) - no rebalancing needed.

### Model

- Base: `yolo26s-cls`, classification head, fine-tuned on AOI component-ROI crops.
- Export: ONNX, opset 17, no NMS - single input `images` `(1, 3, 640, 640)` RGB, normalized
  `/255`, NCHW; single output `output0` `(1, 2)` raw logits - matches `models.toml`'s
  `pcb_lead_defect` entry.
- Classes (2), index order = labels: **Golden, SolderInsufficient**.

### Data

Same proprietary AOI dataset and physical-site-grouped split as `pcb_region` above.

### Metrics

**val** (top-1 0.996, macro-F1 0.996, n=736):

| class | precision | recall | f1 | support |
|---|---|---|---|---|
| Golden | 0.997 | 0.995 | 0.996 | 369 |
| SolderInsufficient | 0.995 | 0.997 | 0.996 | 367 |

**test** (top-1 0.996, macro-F1 0.996, n=754):

| class | precision | recall | f1 | support |
|---|---|---|---|---|
| Golden | 0.995 | 0.997 | 0.996 | 377 |
| SolderInsufficient | 0.997 | 0.995 | 0.996 | 377 |

### Limitations

None observed; test top-1/macro-F1 both 0.996 (n=754).

## `pcb_text_defect` - PCBInspect-TextDefect

### Role

Defect classifier for crops routed as `Text` (silkscreen). Binary: defect-free vs wrong part
printed.

### Model

- Base: `yolo26n-cls`, classification head, fine-tuned on AOI component-ROI crops.
- Export: ONNX, opset 17, no NMS - single input `images` `(1, 3, 480, 480)` RGB, normalized
  `/255`, NCHW; single output `output0` `(1, 2)` raw logits - matches `models.toml`'s
  `pcb_text_defect` entry.
- Classes (2), index order = labels: **Golden, WrongPart**.

### Data

Same proprietary AOI dataset and physical-site-grouped split as `pcb_region` above.

### Metrics

**val** (top-1 1.000, macro-F1 1.000, n=94):

| class | precision | recall | f1 | support |
|---|---|---|---|---|
| Golden | 1.000 | 1.000 | 1.000 | 53 |
| WrongPart | 1.000 | 1.000 | 1.000 | 41 |

**test** (top-1 1.000, macro-F1 1.000, n=97):

| class | precision | recall | f1 | support |
|---|---|---|---|---|
| Golden | 1.000 | 1.000 | 1.000 | 53 |
| WrongPart | 1.000 | 1.000 | 1.000 | 44 |

### Limitations

Smallest training set of the four (476 images total). Test top-1/macro-F1 both 1.000 (n=97), but
the small, low-diversity test set means this number should be treated cautiously rather than as a
tight confidence interval.
