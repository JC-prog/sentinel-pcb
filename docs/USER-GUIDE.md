# User Guide — how to use SentinelPCB

*How to run the app and use it, both through the UI and the API directly. For what's actually
implemented under the hood, see `docs/AGENT-DESIGN.md`. For environment setup and contributing,
see `DEVELOPMENT.md`.*

---

## 1. Getting it running

```bash
bash scripts/setup-dev.sh
```

creates `.env`, installs dependencies, and checks whether the ADC model is present. Then, in two
terminals:

```bash
uv run uvicorn app.main:app --reload   # API — http://localhost:8000
cd ui && npm start                     # UI  — http://localhost:4200
```

The UI needs the API and a Postgres instance with `pgvector` reachable (`DATABASE_URL` in
`.env`). If you don't already have one, `docker compose up db` starts a local one on `:5433`.
Open `http://localhost:4200` — it lands on the **Queue** page.

## 2. Submitting a PCB image for inspection

**From the UI (Queue page):** pick one or more image files (PNG/JPEG/BMP/TIFF), optionally fill
in Board ID / Component ID / Recipe ID (free text — purely descriptive, not validated against
anything), and click **Submit to queue**. Each file becomes its own workflow. The right-hand
panel shows everything currently in flight, polling every 2 seconds.

**From the API directly:**

```bash
curl -X POST http://localhost:8000/workflows \
  -F "image=@path/to/board.png" \
  -F "board_id=MB-2024-REV3" \
  -F "component_id=R47"
```

Returns `202` immediately with a `workflow_id` — the response doesn't wait for processing, which
happens as a background task.

**Headlessly, simulating a line feed:**

```bash
uv run python simulation/simulate_line.py --images-dir simulation/images --interval 5
```

Loops over a directory of images, posting one every `--interval` seconds (`--once` to send each
image exactly once and exit instead of looping forever). Useful for generating demo data.

## 3. Watching an inspection move through the pipeline

Every submitted image becomes a `Workflow` that moves through a fixed sequence of states:

```
RECEIVED → PREPARING → QUALITY_CHECK → INFERENCE → POLICY_DECISION → ACCEPTED → LEARNING_QUEUE
                              │                                    └─(escalate)→ EXPLANATION → HUMAN_REVIEW
                              └─(fails quality)→ REJECTED_QUALITY
```

Click **Trace →** next to any in-flight workflow (Queue page) to open a live view: it streams
each state transition as it happens (Server-Sent Events, ~0.5s poll interval under the hood) and
shows a "done" marker once the workflow reaches a terminal state
(`LEARNING_QUEUE` / `HUMAN_REVIEW` / `REJECTED_QUALITY`). Reconnecting or opening the link fresh
always replays the full history from the start, so you won't miss anything.

If you want transitions paced out for a demo instead of completing in well under 100ms, set
`demo_phase_delay_seconds` in `.env` (default `0`, i.e. off).

## 4. Reading a result

Open a workflow's **Board Information** page (`/board/:id`, e.g. via the Trace page's link, or
by navigating directly) to see:

- Board / Component / Recipe ID and current status
- **Decision** (`auto_accept` or `escalate_to_human`), the **defect label**, and both confidence
  numbers — *defect confidence* (from the defect classifier, what actually gates the decision)
  and *feature confidence* (from the structural feature detector). A large gap between the two is
  itself a reason for escalation, independent of either number alone (see §6).
- A one-line **rationale** for the decision.
- The uploaded image with bounding-box overlays for each detected structural feature
  (`MountingHole`, `ComponentBody`, `SolderJoint`, `Lead`).

If the workflow hasn't finished yet, this page just shows what's known so far plus a link back to
the trace.

### Escalated cases: the full report

If a workflow was escalated to human review, its Board Information page shows a **View full
report →** link (`/report/:id`) with:

- **Evidence** — each claim in the report, individually marked as grounded (✓) or stripped as
  unsupported (✗), with a reference back to the underlying detection/decision it's based on.
- **Similar past cases** — auto-accepted workflows retrieved by embedding similarity (empty
  early on; grows as more cases get auto-accepted).
- **Retrieved guidance** — remediation guidance for this defect label, pulled from a seeded
  reference corpus.
- **Recommendation** — what to do next.

This report is built from a template over already-computed evidence, not a live LLM call — see
`docs/AGENT-DESIGN.md` §4 for why.

> **Known gap:** there's currently no way to action a `HUMAN_REVIEW` case from the UI (accept /
> reject / correct it) — it's a dead end for now. See `docs/AGENT-DESIGN.md` §5.

## 5. History and the observability dashboard

- **History** (`/history`) lists every workflow that's reached a terminal state — the queue's
  "done" pile. Click through to any row for its Board Information page.
- **Observability** (`/observability`) is a live-computed dashboard (`GET
  /observability/metrics`, no schedule/cron — recomputed on every visit): decision distribution,
  escalation rate, average feature/defect confidence, the feature/defect disagreement rate, and a
  count of policy-allowlist rejections. Threshold breaches show up as dashboard alerts (warning /
  critical) — there's no outbound notification (email/webhook) yet, so this page is the only
  place you'll see them.

## 6. Understanding auto-accept vs. escalate

A workflow escalates to human review if **either**:

- the defect classifier's confidence is below `auto_accept_confidence` (default **0.95**), or
- the gap between feature-detector confidence and defect-classifier confidence exceeds
  `confidence_disagreement_delta` (default **0.15**) — the two models disagreeing about the same
  image is treated as its own red flag, not just low confidence.

Otherwise it auto-accepts and moves straight to `LEARNING_QUEUE`, where its summary gets embedded
and becomes a retrievable precedent for future escalated reports (§4).

Both thresholds — plus a few Continuous Monitoring & Drift alert thresholds
(`max_escalation_rate`, `max_disagreement_rate`) — live in `app/policies/thresholds.py`, versioned
in git like everything else policy-related; there's no admin UI to tune them yet.

## 7. API reference

| Method & path | What it does |
|---|---|
| `POST /workflows` | Submit an image (`image` file + optional `board_id`/`component_id`/`recipe_id`/`metadata` form fields). Returns `202` + a workflow summary immediately. |
| `GET /workflows?terminal=&status=&limit=` | List workflows. `terminal=false` = Queue view, `terminal=true` = History view, `status=LEARNING_QUEUE` (etc.) for a specific state. |
| `GET /workflows/{id}` | Full detail for one workflow, including its explanation report if escalated. |
| `GET /workflows/{id}/image` | The originally uploaded image file. |
| `GET /workflows/{id}/trace` | Server-Sent Events stream of this workflow's state transitions, ending with a `done` event. |
| `GET /observability/metrics` | Current-state model + agent/policy health metrics (see §5). |
| `GET /health` | Liveness check. |

## 8. Troubleshooting

- **"Could not reach the API"** on any page — start the backend (`uv run uvicorn app.main:app
  --reload`) and confirm it's on `:8000`.
- **A workflow always lands in `REJECTED_QUALITY`** — Dataset Quality rejects images above a size
  ceiling, that PIL can't decode, below 64px in either dimension, or outside the PNG/JPEG/BMP/TIFF
  allowlist. Check the image itself first.
- **Every workflow shows `defect_label: null` / low confidence** — the real ONNX feature detector
  (`models/pcb_feature_detector.onnx`) isn't present. Everything else still works, but Multi-Modal
  Inference can't run — see `models/README.md` to generate it.
- **DB-backed pages are empty / API calls to `/workflows` fail** — Postgres isn't reachable at
  `DATABASE_URL`. Run `docker compose up db`.
