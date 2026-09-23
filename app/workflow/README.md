# 🔬 Unified Multi-Agent PCB Defect Inspection & Explainability System

An industrial-grade, multi-agent inspection system for Printed Circuit Boards (PCBs) conforming to **IPC-A-610 Class 2/3** standards.

The architecture decouples fast edge classification from deep multimodal explainability:
* **Agent 1 (Orchestrator & Baseline ADC):** Parses AOI XML/CSV metadata, verifies image pairs, automatically populates the vector database, and runs fast ONNX classifiers (Feature classifier $\rightarrow$ Dynamic routing to Body, Lead, or Text models).
* **Agent 2 (Multimodal Explainability & Review):** Escalation agent running as an independent HTTP microservice on port `8001`. Governed by **LangGraph**, **Ollama LLaVA VLM**, **3D Laser/ICT Telemetry**, and **OpenAI GPT-4o Grounding** to detect physical contradictions and cite IPC standard clauses.
* **Inter-Agent Communication:** Standard **Agent2Agent (A2A)** Protocol (JSON-RPC 2.0 / HTTP with Agent Card discovery at `/.well-known/agent.json`).
* **Persistent Vector Memory:** Local embedded **Qdrant** database (`qdrant_db/`) indexing physical telemetry, defect precedents, and IPC clauses.

---

## 🏛️ System Architecture

```text
               AOI Inspection XML + CSV Metadata + Image Folders
                                      │
                                      ▼
 ┌─────────────────────────────────────────────────────────────────────────────┐
 │                      AGENT 1: CORE ADC & ORCHESTRATOR                       │
 │                                                                             │
 │  1. Auto-Discovery & Ingestion: Recursively scans 120+ boards in data/inputs│
 │  2. Vector Indexer: Embeds & stores inspection records in local Qdrant DB   │
 │  3. Image Verification & Smart Golden-Pairing: Sibling Golden/ matching     │
 │  4. Stage 1: Feature Classifier (224x224 ONNX) ──► Body, Lead, or Text      │
 │  5. Dynamic Routing: Body (640x640), Lead (640x640), or Text (480x480)      │
 │  6. Deterministic Policy Gate: Evaluates confidence & metadata consistency  │
 └──────────────────────────────────────┬──────────────────────────────────────┘
                                        │
        ┌───────────────────────────────┴───────────────────────────────┐
        │                                                               │
   [Confidence >= Threshold]                                     [REVIEW_REQUIRED]
        │                                                               │
        ▼                                                               │ A2A Protocol (HTTP POST :8001)
   AUTO-ACCEPTED                                                        │ Task: pcb.explainability.audit
   (Status: COMPLETED)                                                  ▼
 ┌─────────────────────────────────────────────────────────────────────────────┐
 │                 AGENT 2: MULTIMODAL EXPLAINABILITY & REVIEW                 │
 │                           (A2A Server on Port 8001)                         │
 │                                                                             │
 │   Receives A2A Task ──► Executes LangGraph Pipeline with MCP Tools:         │
 │                                                                             │
 │   ┌───────────────────────┐   ┌───────────────────────┐   ┌─────────────┐   │
 │   │  1. Vector DB RAG     │   │  2. Local LLaVA VLM   │   │ 3. Telemetry│   │
 │   │  Qdrant IPC-A-610-C2  │   │  Ollama Visual Inspect│   │ 3D Laser/ICT│   │
 │   └───────────┬───────────┘   └───────────┬───────────┘   └──────┬──────┘   │
 │               └─────────────────────┬─────┴──────────────────────┘          │
 │                                     ▼                                       │
 │                       ┌───────────────────────────┐                         │
 │                       │ 4. Grounding Self-Check   │                         │
 │                       │ GPT-4o Physics Arbitrator │                         │
 │                       └─────────────┬─────────────┘                         │
 └─────────────────────────────────────┼───────────────────────────────────────┘
                                       │
                                       ▼
                              Unified Audit JSON
       (Resolved Verdict, IPC Citations, Contradiction Flags, Diagnosis)
```

---

## 🏷️ Normalized IPC-A-610 Defect Taxonomy

All predictions from both agents are normalized into 7 standard industrial categories:

| Defect Class | Visual Criteria | Physical Telemetry Ground Truth |
|---|---|---|
| **`missing part`** | Empty land pattern / bare solder pads | ICT Open Circuit ($R > 10\text{ M}\Omega$); Laser Height $\approx 0\,\mu\text{m}$ |
| **`shifted`** | Component rotated or displaced | Side overhang $> 50\%$ (IPC Class 2 violation) |
| **`foreign material`**| Extraneous solder balls or bridging debris | Surface coplanarity disruption; non-standard conductive path |
| **`tombstone`** | Part detached, standing vertically | Open circuit with elevated laser profile height ($>100\,\mu\text{m}$) |
| **`solder insufficient`**| Poor wetting fillet; incomplete pad coverage | Laser fillet thickness below minimum IPC Class 2 threshold |
| **`wrong part`** | Mismatched component package or markings | Measured capacitance / resistance out of tolerance band |
| **`no defect`** | Optimal solder fillet and alignment | All electrical and dimensional tolerances satisfied |

---

## 📁 Project Structure

```text
pcb_agentic_inspection_system/
├── config/
│   ├── models.yaml                      # ONNX model specs (224x224, 640x640, 480x480)
│   ├── policy.yaml                      # Confidence thresholds & loop safety limits
│   └── agent2_config.yaml               # Qdrant, Ollama, & IPC tolerance thresholds
├── data/
│   ├── inputs/                          # 122+ board assembly folders (e.g., 06-200036-02/...)
│   │   └── 06-200036-02/
│   │       └── Body/
│   │           ├── Passed/              # Defect image instances
│   │           └── Golden/              # Reference Golden images
│   └── sample_data/                     # dataset.csv and inspection.xml
├── models/                              # Baseline Single-Image RGB ONNX Classifiers
│   ├── feature/feature_classifier.onnx  (224x224)
│   ├── body/body_classifier.onnx        (640x640)
│   ├── lead/lead_classifier.onnx        (640x640)
│   └── text/text_classifier.onnx        (480x480)
├── outputs/
│   ├── telemetry_by_image.json          # Fast O(1) physical telemetry lookup index
│   └── result.json                      # Unified final diagnostic audit output
├── qdrant_db/                           # Persistent local vector store for IPC precedents
├── src/
│   ├── agent1_orchestrator/             # AGENT 1: BASELINE & CONTROL
│   │   ├── agents/
│   │   │   ├── orchestrator.py          # Workflow loop & A2A escalation caller
│   │   │   └── a2a_dispatcher.py        # A2A client connecting to Agent 2
│   │   ├── services/
│   │   │   ├── dataset_preparation.py   # XML/CSV measurement extraction
│   │   │   ├── dataset_verification.py  # Image quality & alignment checker
│   │   │   └── model_lifecycle.py       # ONNX Runtime session cache
│   │   ├── policy/policy_engine.py      # Deterministic gating engine
│   │   └── ui.py                        # Tkinter Operator Desktop UI
│   ├── agent2_explainability/           # AGENT 2: MULTIMODAL REVIEW & EXPLAINABILITY
│   │   ├── a2a/
│   │   │   ├── protocol.py              # A2A Task & AgentCard Pydantic schemas
│   │   │   └── agent2_a2a_server.py     # FastAPI A2A Server (Port 8001)
│   │   ├── pipeline/
│   │   │   └── review_graph.py          # LangGraph Multimodal State Machine
│   │   └── generate_telemetry.py        # 3D AOI & ICT profile builder
│   └── data/
│       └── qdrant_indexer.py            # Local vector DB embedding & upsert pipeline
├── tests/
│   └── test_agent1_fast_path.py         # Zero-dependency standard library test suite
├── main.py                              # Unified CLI runner (Batch, Slicing & Qdrant flags)
├── requirements.txt
└── .env                                 # Local API keys (OPENAI_API_KEY)
```

---

## ⚡ Setup & Installation

### 1. Environment Setup
Python 3.10+ is recommended:

```bash
python -m venv .venv
# Windows (PowerShell):
.\.venv\Scripts\Activate.ps1
# Linux/macOS:
source .venv/bin/activate

pip install --upgrade pip
pip install -r requirements.txt
```

### 2. Pull the Local Vision Model (Ollama)
Ensure [Ollama](https://ollama.com/) is installed and running:
```bash
ollama pull llava
```

### 3. Configure `.env`
Create or edit `.env` in the project root:
```env
OPENAI_API_KEY=sk-proj-yourActualKeyHere
OPENAI_MODEL=gpt-4o
```

---

## 🗄️ Populating the Qdrant Vector Database

Agent 1 can index all discovered samples and physical telemetry directly into the persistent local vector store (`qdrant_db/`).

You can populate the database using the standalone indexer:
```bash
python -m src.data.qdrant_indexer
```
*or automatically during your inspection run using the CLI flag:*
```bash
python main.py --populate-vector-db --limit 20
```

To verify that the vector database is populated:
```cmd
dir qdrant_db
```
You will see the generated collection directories (`collection/`, `meta.json`).

---

## 🚀 Two-Terminal Execution Workflow

Agent 1 and Agent 2 run as decoupled services communicating over HTTP via the A2A protocol.

### Terminal 1: Start Agent 2 (The Explainability Server)
```bash
python -m src.agent2_explainability.a2a.agent2_a2a_server
```
Wait until the server starts:
```text
INFO: Application startup complete.
INFO: Uvicorn running on http://127.0.0.1:8001 (Press CTRL+C to quit)
```

---

### Terminal 2: Run Agent 1 (Unified Runner)

#### Option A: Headless Command-Line Runner (Automatic Image Discovery & Batching)
```bash
python main.py --limit 10 --batch-size 5
```

#### Option B: Real XML & CSV Inspection Run
```bash
python main.py \
  --dataset data/sample_data/dataset.csv \
  --xml data/sample_data/inspection.xml \
  --image-root data/inputs \
  --limit 20 \
  --batch-size 5
```

#### Option C: Operator Desktop UI (Tkinter)
```bash
python src/agent1_orchestrator/ui.py
# On Windows, you can also double-click: run_ui.bat
```

---

## 📦 Batch Execution & Command-Line Arguments

`main.py` provides flags to control dataset exploration, memory chunking, vector indexing, and confidence gating:

| Flag | Default | Description |
|---|---|---|
| `--limit N` | `None` (All) | Restricts execution to the first $N$ samples (ideal for smoke testing). |
| `--batch-size B` | `5` | Splits samples into mini-batches of size $B$ to prevent timeouts. |
| `--confidence-threshold` | `0.85` | Baseline confidence gate threshold; below this triggers Agent 2 review. |
| `--populate-vector-db` | `False` | Indexes all loaded samples into the local Qdrant database before inference. |
| `--qdrant-path` | `qdrant_db` | Target directory for the local embedded Qdrant vector database. |
| `--image-root` | `data/inputs` | Base folder used for recursive image discovery and path remapping. |
| `--dataset` | `data/sample_data/dataset.csv` | Path to inspection metadata CSV. |
| `--xml` | `data/sample_data/inspection.xml` | Path to AOI inspection XML. |
| `--agent2-url` | `http://127.0.0.1:8001` | A2A server URL for explainability delegation. |
| `--output` | `outputs/result.json` | Destination path for the unified diagnostic JSON report. |

### Common CLI Examples

```bash
# 1. Quick test run with 10 images in batches of 5
python main.py --limit 10 --batch-size 5

# 2. Populate Qdrant vector database and run 25 samples
python main.py --populate-vector-db --limit 25 --batch-size 5

# 3. High-confidence production run across all board folders
python main.py --confidence-threshold 0.90 --batch-size 20 --output outputs/prod_run.json
```

---

## 🔍 Robust Image Discovery & Path Resolution

The runner automatically crawls the image filesystem without needing rigid manual configurations:

1. **Auto-Exploration Across Boards:** Recursively searches `data/inputs` across all 122+ board assembly folders (`06-200036-02`, etc.), automatically parsing component references and defect labels from filenames and directory structures.
2. **Smart Golden-Pairing:** Automatically identifies the sibling `Golden/` directory relative to any defect image, locating the corresponding reference image by component ID.
3. **Legacy Windows Path Remapping:** Dynamically recovers relocated images by matching subpaths below `usi` or searching recursively by basename.
4. **Canonical Absolute Path Transmission:** Relative paths are resolved to absolute canonical paths before being dispatched over A2A HTTP, preventing missing-image errors between independent terminal sessions.

---

## 🧪 Running Unit Tests

The test suite runs using Python's built-in `unittest` module without requiring external packages like `pytest`:

```bash
python tests/test_agent1_fast_path.py
```

*Expected output:*
```text
test_agent1_escalates_when_confidence_below_threshold (__main__.TestAgent1FastPath) ...  -> test_agent1_escalates_when_confidence_below_threshold: PASSED (Escalated to Agent 2)
ok
test_agent1_fast_path_high_confidence (__main__.TestAgent1FastPath) ... 
 -> test_agent1_fast_path_high_confidence: PASSED (Agent 2 call_count == 0)
ok

----------------------------------------------------------------------
Ran 2 tests in 0.001s

OK
```

---

## 📊 Sample Unified Output (`outputs/result.json`)

```json
{
  "summary": {
    "total_samples": 3,
    "agent1_auto_accepted": 1,
    "escalated_to_agent2": 2,
    "agent2_resolved": 2,
    "human_review_required": 0
  },
  "results": [
    {
      "sample_id": "06-200036-02_C636_Board1_C636_Body_06-200036-02_MissingPart_3",
      "board_id": "06-200036-02",
      "component_id": "C636",
      "resolved_image_path": "C:/Users/.../data/inputs/06-200036-02/Body/Passed/Board1_C636_Body_...MissingPart_3.jpg",
      "resolved_golden_path": "C:/Users/.../data/inputs/06-200036-02/Body/Golden/Board1_C636_Body_...Golden.jpg",
      "baseline_inference": {
        "feature_class": "Body",
        "defect_class": "MissingPart",
        "confidence": 0.65
      },
      "gate_decision": "REVIEW_REQUIRED",
      "agent2_review": {
        "predicted_defect": "missing part",
        "confidence": 0.98,
        "self_check_passed": true,
        "contradiction_detected": false,
        "diagnosis": "Physical open circuit (ICT FAIL) and laser height (0.80 µm) confirm missing part.",
        "ipc_citations": [
          "IPC-A-610 Class 2 Section 8.3"
        ],
        "visual_evidence": "Rectangular solder land pattern is bare silver. Ceramic component body is absent."
      },
      "final_verdict": "missing part",
      "workflow_status": "COMPLETED"
    },
    {
      "sample_id": "06-200036-02_C978_Board1_C978_Body_06-200036-02_Shift_4",
      "board_id": "06-200036-02",
      "component_id": "C978",
      "resolved_image_path": "C:/Users/.../data/inputs/06-200036-02/Body/Passed/Board1_C978_Body_...Shift_4.jpg",
      "resolved_golden_path": "C:/Users/.../data/inputs/06-200036-02/Body/Golden/Board1_C978_Body_...Golden.jpg",
      "baseline_inference": {
        "feature_class": "Body",
        "defect_class": "Shift",
        "confidence": 0.70
      },
      "gate_decision": "REVIEW_REQUIRED",
      "agent2_review": {
        "predicted_defect": "shifted",
        "confidence": 0.95,
        "self_check_passed": true,
        "contradiction_detected": false,
        "diagnosis": "Side overhang (62.0%) exceeds IPC-A-610 Class 2 maximum 50% limit.",
        "ipc_citations": [
          "IPC-A-610 Class 2 Section 8.3.2"
        ],
        "visual_evidence": "Component body is misaligned laterally past the termination pad edge."
      },
      "final_verdict": "shifted",
      "workflow_status": "COMPLETED"
    }
  ]
}
```

---

## 🛠️ Production Extensibility

* **Human-in-the-Loop (HITL):** If Agent 2 detects an irreconcilable visual/physical contradiction or if `self_check_passed == False`, the workflow status becomes `HUMAN_QA_REQUIRED` and routes the case to the QA review queue.
* **Continuous Learning:** Confirmed escalated edge cases can be indexed back into the local `qdrant_db` collection to expand historical retrieval accuracy for future runs.
* **Industrial SMT Line Integration:** In `generate_telemetry.py` or `dataset_preparation.py`, direct SECS/GEM or OPC-UA protocols can be hooked up to stream real-time measurements from inline 3D AOI and ICT equipment.
