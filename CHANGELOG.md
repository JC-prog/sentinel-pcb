# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). This project
follows [Semantic Versioning](https://semver.org/); before 1.0.0, a minor version bump may still
include breaking changes.

## [Unreleased]

### Added

- Placeholder defect classifier (`app/agents/multi_modal_inference/defect_classifier.py`) - real
  accept/escalate decisions are now driven by an actual (if deterministic-placeholder) defect
  confidence, not the feature detector's own confidence used as a proxy.
- Explainability & Review (`app/agents/explainability_review/`) - templated report generation
  with a RAG-backed Case Context tool: local embeddings (`sentence-transformers`) + pgvector,
  retrieving from a synthetic remediation-guidance corpus and the system's own accumulating case
  history. Resolves the previously-open "Case Context: tool vs. subagent" design question.
- Continuous Monitoring & Drift / Observability (`app/agents/continuous_monitoring_drift/`) -
  on-demand model and agent-health metrics with dashboard-only threshold alerts, exposed at
  `GET /observability/metrics`.
- Learning Queue - auto-accepted workflows now advance automatically and get embedded as future
  retrieval precedents for later escalated cases.
- New workflow phases: `POLICY_DECISION` (the Orchestrator's own decision, split out from
  `INFERENCE`), `LEARNING_QUEUE`, `EXPLANATION`; `COMPLETED` renamed to `ACCEPTED`.
- New frontend routes: Report (`/report/:id`) and Observability (`/observability`); Board detail
  now shows the defect label and both confidence scores.
- `docs/MODEL-TRAINING.md` - datasets and training/export steps for replacing the placeholder
  defect classifier with a real trained model.
- `scripts/seed_remediation_docs.py` - hand-authored synthetic remediation-guidance corpus (16
  documents across four illustrative defect classes).

### Fixed

- Every audit event was incorrectly attributed to `"orchestrator"` regardless of which component
  actually performed the work; each phase now logs its real actor.
- A test-teardown fixture was truncating the seeded remediation-docs corpus after every `pytest`
  run, silently emptying the RAG's knowledge base.
- `PolicyViolation` exceptions previously crashed the background task with no audit trace; now
  logged (`actor="policy"`) before re-raising, so the violation isn't lost.

### Changed

- `docs/AGENT-DESIGN.md` rewritten to match the current as-built state.

## [0.1.0] - 2026-08-16

### Added

- Persisted workflow lifecycle backed by Postgres (SQLAlchemy async) - `Workflow`/`AuditEvent`
  tables, a FastAPI background-task runner replacing the original single synchronous call.
- `POST`/`GET /workflows` API with Server-Sent Events live tracing (`GET /workflows/{id}/trace`).
- Angular frontend: Queue, Board, Trace, and History views with routing.
- Seed data: 7 real PCB images from a Roboflow dataset; `simulation/simulate_line.py` extended to
  post board/component/recipe info alongside each image.
- Initial deterministic pipeline: Dataset Preparation, Dataset Quality, Multi-Modal Inference
  (ONNX feature detector), and an Orchestrator policy gate.

[Unreleased]: https://github.com/JC-prog/sentinel-pcb/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/JC-prog/sentinel-pcb/releases/tag/v0.1.0
