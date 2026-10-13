# Agent 2 evals

Four scripts the teammate sent with the Agent 2 unit tests (Oct 13 2026), ported as they were with
only the paths adapted. They are **manual**: not run by pytest or CI, because they call the real Agent
2 graph (an OpenAI vision call and a GPT-4o reasoning call per case, and a GPT-4o judge for the rubric)
and need the teammate's inspection images. They cost tokens.

| Script | What it measures |
| --- | --- |
| `eval_classification_metrics.py` | Accuracy, per-class precision/recall/F1 and a confusion matrix over `*/Passed/*.jpg` |
| `eval_contradiction_grounding.py` | Three fixed scenarios where the image and the telemetry disagree; does the graph side with the right evidence |
| `eval_ipc_citation_fidelity.py` | Whether the explanation cites IPC-A-610 clauses (a regex over its text) |
| `eval_explainability_rubric.py` | An LLM judge scoring the explanation on four 1-5 criteria |

## Running

From the repo root, with `OPENAI_API_KEY` set (these use the environment, like the ported graph):

```bash
export AGENT2_EVAL_INPUTS=data/workflow/data/inputs      # the default; holds <board>/Body/Passed/*.jpg
PYTHONPATH=.:app/workflow uv run python app/workflow/evals/eval_classification_metrics.py --limit 10
PYTHONPATH=.:app/workflow uv run python app/workflow/evals/eval_ipc_citation_fidelity.py --limit 5
PYTHONPATH=.:app/workflow uv run python app/workflow/evals/eval_explainability_rubric.py --limit 3
PYTHONPATH=.:app/workflow uv run python app/workflow/evals/eval_contradiction_grounding.py   # no flags
```

**`eval_contradiction_grounding.py` has no argument parser: running it, even with `--help`, runs the
benchmark and calls OpenAI.** Results from the first script are written to
`data/workflow/eval_outputs/` (gitignored).

## What was changed from the teammate's copies

Only the block marked "Adapted for this repo" at the top of each script: where the images are
(`AGENT2_EVAL_INPUTS`), where results go, and putting the repo root on `sys.path` so the app's
settings import. `eval_contradiction_grounding.py` also resolves its `inputs/...` sample paths under
`AGENT2_EVAL_INPUTS`. The graph under test is the repo's adapted `review_graph.py` (OpenAI vision, no
invented telemetry), not the pre-port one they were written against, so numbers are not comparable
with runs on the teammate's machine.

## Read these results with care

- **`eval_classification_metrics.py` is seeded with the answer.** Each case is built with
  `preliminary_defect = ground_truth` (parsed from the filename), so its accuracy measures how often
  Agent 2 keeps or overturns that seed, not independent classification. Treat a high accuracy as "it
  usually agrees with the label it was given".
- **Precedents.** The cases pass `retrieved_precedents: []`, and `review_graph.py` fills that from its
  own fixed IPC rules, not from Qdrant. These evals do not exercise the precedent search.
- **`eval_ipc_citation_fidelity.py` is a keyword check.** `Section \d+\.\d+`, `overhang`, `fillet`...
  match plausible text as readily as correct text.
- **The judge is the same family of model** that wrote the explanation, with no ground truth.
