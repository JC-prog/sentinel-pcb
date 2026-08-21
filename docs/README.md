# docs

Code-level/repo documentation (setup notes, ADRs, API docs) lives here.

- **`USER-GUIDE.md`** — how to run the app and use it (UI + API), for someone who just wants to
  submit an inspection and read the result.
- **`AGENT-DESIGN.md`** — as-built architecture: what's actually implemented vs. still planned.
- **`MODEL-TRAINING.md`** — how to train and swap in a real defect classifier.

Project-level planning — requirements, agent design docs, effort estimates, methodology — lives in
`planning/` (gitignored — course-proposal and PRD/scenario material, not shipped with the repo).
This folder is for documentation that's tied to *this* codebase specifically and would go stale if
kept anywhere else.
