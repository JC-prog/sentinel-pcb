# 🔍 Multi-Agent PCB Defect Inspection & Explainability System

An industrial-grade, multi-agent inspection system for Printed Circuit Boards (PCBs) built using **LangGraph**, the **Agent2Agent (A2A) Protocol**, and the **Model Context Protocol (MCP)**. 

The system couples **Visual Evidence** (Local LLaVA Vision-Language Model), **Physical Telemetry** (3D AOI laser height profiles and In-Circuit Testing electrical measurements), **IPC-A-610 Class 2/3 Standards**, and **Historical Defect Precedents** (Qdrant Vector RAG) to deliver grounded, explainable root-cause diagnoses with contradiction detection.

---

## 🏛️ System Architecture

The system decouples agent collaboration from tool execution:
* **Inter-Agent Communication (Horizontal):** Governed strictly by the **A2A Protocol** (HTTP / JSON-RPC 2.0 with Agent Card discovery and task lifecycle management).
* **Intra-Agent Tooling (Vertical):** Governed by **MCP (Model Context Protocol)** servers to isolate databases, telemetry lookups, and AI models.

```text
 ┌────────────────────────────────────────────────────────────────────────┐
 │                        AGENT 1: ORCHESTRATOR                          │
 │                                                                        │
 │   1. Ingest AOI Inspection Event                                       │
 │   2. Run Core ADC Services (Fast Baseline Inference)                   │
 │   3. Query Agent 1 MCP Tools (Audit Log, Confidence Decision Gate)     │
 └───────────────────┬────────────────────────────────┬───────────────────┘
                     │ MCP (Agent 1 ➔ Tools)          │
       ┌─────────────▼──────────────┐                 │
       │     Agent 1 MCP Server     │                 │
       │ • Audit Logging Tool       │                 │
       │ • Confidence Decision Tool │                 │
       └────────────────────────────┘                 │
                                                      │ A2A Protocol
                                                      │ (Task Delegation over HTTP)
                                                      │ • Discover: /.well-known/agent.json
                                                      │ • Task: pcb.explainability.audit
                                                      │
 ┌────────────────────────────────────────────────────▼───────────────────┐
 │                   AGENT 2: EXPLAINABILITY & REVIEW                     │
 │                   (A2A Server on Port 8001)                            │
 │                                                                        │
 │   Receives A2A Task ➔ Executes LangGraph Pipeline ➔ Returns A2A Artifact│
 └───────────────────┬────────────────────────────────────────────────────┘
                     │ MCP (Agent 2 ➔ Tools)
       ┌─────────────▼──────────────────────────────────────┐
       │                Agent 2 MCP Server                  │
       │  1. Case Context Retrieval Tool (Qdrant Vector DB) │
       │  2. Visual Evidence Tool (Local LLaVA VLM)         │
       │  3. Measurement Evidence Tool (ICT / 3D Laser)     │
       │  4. Grounding and Self-Check Tool (OpenAI GPT-4o)  │
       └────────────────────────────────────────────────────┘
```

### Contradiction Detection & Physical Grounding
A core strength of the system is its **grounding self-check mechanism**:
* **Missing Part vs. Solder Starvation:** If visual inspection detects minor solder pad discoloration, but ICT telemetry reports an **infinite resistance ($>10\text{ M}\Omega$) / open circuit** and laser height is $\approx 0\,\mu\text{m}$, the agent catches the contradiction and classifies it as **`missing part`**.
* **Shifted:** If a component has excessive **side overhang ($>50\%$)**, the agent applies **IPC-A-610 Class 2** tolerance limits to confirm **`shifted`**.

---

## 🏷️ Supported Defect Taxonomy

The system classifies anomalies into 7 IPC-aligned categories:
1. **`missing part`**: Pad empty; open ICT circuit ($R > 10\text{ M}\Omega$); laser height profile $\approx 0\,\mu\text{m}$.
2. **`shifted`**: Component misaligned; IPC Class 2 violation (side overhang $>50\%$).
3. **`foreign material`**: Unintended solder splatters, loose balls, flux residue, or debris.
4. **`tombstone`**: Component partially detached, standing on one end; open circuit with elevated height profile.
5. **`solder insufficient`**: Solder wetting fillet below minimum IPC volume/height.
6. **`wrong part`**: Incorrect component size/package or measured electrical value out of tolerance band.
7. **`no defect`**: Component and solder joint satisfy all visual and electrical criteria.

---

## 📂 Directory Layout

```text
Explainability_Review_Agent/
├── .env                                   # Environment variables (OPENAI_API_KEY)
├── requirements.txt                       # Project dependencies
├── inputs/                                # Hierarchical raw PCB inspection dataset
│   └── 06-200036-02/                      # Board Assembly ID
│       └── Body/
│           ├── Passed/                    # Defect image instances (MissingPart, Shift, etc.)
│           └── Golden/                    # Paired defect-free reference images
├── outputs/                               # Telemetry & diagnostic reports
│   ├── synthetic_telemetry.json           # Array of all physical & electrical metrics
│   ├── telemetry_by_image.json            # O(1) key-value lookup map keyed by filename
│   └── inspection_results.json            # Final diagnostic output logs
├── qdrant_db/                             # Persistent local Qdrant vector database files
├── generate_telemetry.py                  # Generates physical 3D AOI & ICT telemetry
├── agent.py                               # Agent 2 LangGraph state machine & MCP tool caller
├── orchestrator_agent.py                  # Agent 1 logic (Core ADC, Decision Gate, A2A Client)
├── run_pipeline.py                        # Single-case trigger for the end-to-end pipeline
├── main.py                                # Batch execution & ground truth evaluation script
└── src/
    ├── a2a/
    │   ├── protocol.py                    # A2A data schemas (AgentCard, A2ATaskRequest/Response)
    │   └── agent2_a2a_server.py           # Agent 2 FastAPI-based A2A server endpoint
    ├── mcp/
    │   ├── agent1_mcp_server.py           # Agent 1 MCP tools (Audit log, Confidence decision)
    │   └── agent2_mcp_server.py           # Agent 2 MCP tools (Qdrant, LLaVA, ICT, OpenAI)
    ├── models/
    │   └── model_registry.py              # Model wrappers (Local LLaVA, OpenAI GPT-4o)
    └── data/
        └── qdrant_store.py                # Qdrant client & historical defect indexer
```

---

## 🚀 Setup & Installation

### 1. Prerequisites
* **Python 3.10+** (in an Anaconda or virtual environment)
* [Ollama](https://ollama.com/) (running on local machine)
* An **OpenAI API Key**

### 2. Pull the Local Vision Model
Ensure Ollama is running, then pull LLaVA:
```bash
ollama pull llava
```

### 3. Install Python Dependencies
```bash
pip install -r requirements.txt
```

### 4. Configure Environment Variables
Create or edit your `.env` file in the project root:
```env
OPENAI_API_KEY=sk-proj-yourActualOpenAIKeyHere
```

---

## ⚡ Execution Workflow

### Step 1: Synthesize Physical & Electrical Telemetry
Before running inspections, compute the physical 3D AOI (height/overhang) and ICT (resistance/capacitance) telemetry dataset from the raw images:

```bash
python generate_telemetry.py
```
*Outputs generated:*
* `outputs/synthetic_telemetry.json` (Full telemetry dataset)
* `outputs/telemetry_by_image.json` (Indexed dictionary for $O(1)$ fast lookup during inspection)

---

### Step 2: Run the Multi-Agent Inspection System (Two-Terminal Workflow)

Because this is a true **Agent-to-Agent (A2A)** architecture, Agent 1 and Agent 2 run as independent processes communicating over HTTP.

#### Terminal 1: Start Agent 2 (The Explainability Server)
Open your first terminal, navigate to the directory, and start Agent 2:
```bash
python -m src.a2a.agent2_a2a_server
```
Wait until you see:
```text
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8001 (Press CTRL+C to quit)
```
*(Leave this terminal running in the background).*

---

#### Terminal 2: Trigger Agent 1 (The Orchestrator)
Open a **second terminal** and trigger a test inspection:
```bash
python run_pipeline.py
```

Or run the **full batch evaluation** over your dataset:
```bash
python main.py
```

---

## 📊 Sample Output Data

### 1. Telemetry Lookup Sample (`outputs/telemetry_by_image.json`)
```json
{
  "Board1_C636_Body_06-200036-02_20260824_193317036_MissingPart_3.jpg": {
    "board_id": "06-200036-02",
    "component_ref": "C636",
    "condition_label": "MISSING",
    "nominal_value": 0.1,
    "measured_value": 0.0,
    "unit": "uF",
    "ict_status": "FAIL",
    "laser_profile_height_um": 0.8,
    "side_overhang_percent": 0.0,
    "coplanarity_um": 0.0,
    "aoi_status": "FAIL",
    "overall_status": "FAIL",
    "filename": "Board1_C636_Body_06-200036-02_20260824_193317036_MissingPart_3.jpg"
  }
}
```

### 2. Final Diagnostic Output (`outputs/inspection_results.json`)
```json
[
  {
    "file_name": "Board1_C636_Body_06-200036-02_20260824_193317036_MissingPart_3.jpg",
    "board_id": "06-200036-02",
    "component_ref": "C636",
    "ground_truth": "missing part",
    "predicted_defect": "missing part",
    "is_correct": true,
    "confidence": 0.97,
    "diagnosis": "Component C636 is entirely absent from its target land pattern. Physical telemetry reveals an open circuit with zero capacitance (0.0 uF) and near-zero laser profile height (0.8 um vs expected 45 um). Visual absence aligns with electrical measurements, passing the self-check.",
    "self_check_passed": true,
    "errors": []
  }
]
```

---

## 🛠️ Production Extensibility

* **Human-in-the-Loop (HITL):** In `orchestrator_agent.py`, any diagnosis where `self_check_passed == False` or confidence falls below the acceptance threshold is automatically flagged and routed to the QA engineer's dashboard for verification.
* **Continuous Learning:** When an engineer validates an escalated mismatch, the verified embedding and diagnosis can be seeded back into `qdrant_db`, improving future retrieval accuracy.
* **Connecting Live Equipment:** In `src/mcp/agent2_mcp_server.py`, replace `measurement_evidence_tool` mock handlers with standard industrial SECS/GEM or REST endpoints to stream telemetry directly from your SMT line's physical AOI and ICT machines.
