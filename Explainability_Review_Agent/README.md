# 🔍 Explainability Review Agent for PCB Defect Inspection

An autonomous, multi-modal review agent built with **LangGraph** designed to inspect Printed Circuit Boards (PCBs) for assembly and component defects. 

The agent merges **Visual Evidence** (Local Vision-Language Models & PCB Detectors) with **Physical Measurements** (3D AOI height profiles and In-Circuit Testing electrical telemetry), **IPC-A-610 Manufacturing Standards**, and **Historical Defect Precedents** (Vector RAG) to provide grounded, explainable root-cause diagnoses.

---

## 🏗 System Architecture

The pipeline routes each inspected component through a 4-stage **LangGraph** state graph:

```text
       [START]
          │
          ▼
┌─────────────────────────────────────────┐
│ Tool 1: Context Retrieval (MCP Client)   │
│ - Qdrant Vector Search (Historical RAG) │
│ - IPC-A-610 Reference Standards         │
└──────────────────┬──────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────┐
│ Tool 2: Visual Evidence Extraction      │
│ - PCB Object & Feature Detector         │
│ - LLaVA Vision-Language Inspection (VLM)│
└──────────────────┬──────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────┐
│ Tool 3: Physical & Electrical Telemetry │
│ - Fast lookup from pre-computed metrics │
│ - 3D AOI Laser Profile (Height & Tilt)  │
│ - ICT In-Circuit Testing (R / C / Bias) │
│ - Fallback to MCP Telemetry Server      │
└──────────────────┬──────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────┐
│ Tool 4: Grounding & Reasoning (OpenAI)  │
│ - Multi-modal Cross-Verification        │
│ - Contradiction Detection & Self-Check  │
│ - Standardized JSON Root-Cause Output   │
└──────────────────┬──────────────────────┘
                   │
                   ▼
                 [END]
```

### Contradiction Detection & Physical Grounding
A core strength of the system is its **self-check mechanism**:
* If visual evidence reports a minor solder anomaly, but ICT telemetry reports an **infinite resistance ($>10\text{ M}\Omega$) / open circuit**, the agent flags the discrepancy, fails the self-check, and correctly diagnoses a **`missing part`** or **`tombstone`**.
* If a component has excessive **side overhang ($>50\%$)**, the agent references **IPC-A-610 Class 2** criteria to classify it as **`shifted`**.

---

## 📂 Directory Layout

```text
Explainability_Review_Agent/
├── inputs/                                # Hierarchical raw PCB inspection dataset
│   └── 18-010309-AAA-RV3/                 # Board Part ID
│       ├── Body/
│       │   ├── Passed/                    # Passed reference crops
│       │   │   └── Board1_R131_Body_...jpg
│       │   └── Failed/                    # Defect crops (Tombstone, Shift, etc.)
│       └── Text/
├── outputs/                               # Generated telemetry & analysis results
│   ├── synthetic_telemetry.json           # Batch list of all component measurements
│   ├── telemetry_by_image.json            # O(1) instant lookup table keyed by filename
│   └── inspection_results.json            # Final agent diagnostic reports
├── generate_telemetry.py                  # Generates physical 3D AOI & ICT telemetry
├── agent.py                               # LangGraph inspection graph, State, & Prompts
├── main.py                                # Batch execution entrypoint
└── src/
    ├── models/
    │   └── model_registry.py              # LLaVA (Ollama), Detectors & OpenAI models
    └── mcp/
        └── pcb_mcp_server.py              # Model Context Protocol (MCP) server & client
```

---

## 🏷 Supported Defect Taxonomy

The agent standardizes all findings into 7 IPC-aligned categories:
1. `missing part` (Bare pads, open ICT circuit, $\approx 0\,\mu\text{m}$ height profile)
2. `shifted` (IPC Class 2 violation: side overhang $>50\%$)
3. `foreign material` (Solder splatters, flux residues, or non-board debris)
4. `tombstone` (One terminal detached, elevated laser profile, open circuit)
5. `solder insufficient` (Wetting fillet below minimum standard)
6. `wrong part` (Component value out-of-tolerance via ICT)
7. `no defect` (Component passes both visual and electrical tolerance bands)

---

## 🚀 Setup & Installation

### 1. Prerequisites
* **Python 3.10+**
* [Ollama](https://ollama.com/) (for local visual feature extraction)
* An **OpenAI API Key** (for Stage 4 multimodal reasoning)

### 2. Install Python Dependencies
```bash
pip install langgraph qdrant-client ollama pillow openai numpy
```

### 3. Setup Local Vision Model
Ensure Ollama is running and pull the LLaVA vision model:
```bash
ollama pull llava
```

### 4. Configure API Keys
Set your OpenAI API key in your terminal or `.env` file:
```bash
# Windows Command Prompt (cmd)
set OPENAI_API_KEY=your_actual_openai_key_here

# Windows PowerShell
$env:OPENAI_API_KEY="your_actual_openai_key_here"

# Linux / macOS
export OPENAI_API_KEY="your_actual_openai_key_here"
```

---

## ⚡ Execution Workflow

### Step 1: Synthesize AOI & ICT Telemetry
Before running the agent, parse all PCB images in `inputs/` and generate the physical 3D AOI (height/overhang) and ICT (resistance/capacitance) telemetry dataset:

```bash
python generate_telemetry.py
```
*Outputs generated:*
* `outputs/synthetic_telemetry.json` (Array of all telemetry records)
* `outputs/telemetry_by_image.json` (Key-value map indexed by image filename for fast lookup)

### Step 2: Run the Inspection Agent
Execute the review agent across your PCB images:

```bash
python main.py
```

---

## 📊 Telemetry Data & Inspection Output

### 1. Telemetry Lookup Sample (`outputs/telemetry_by_image.json`)
```json
{
  "Board1_R131_Body_18-010309-AAA-RV3_20260822_082401620_Shift_4.jpg": {
    "board_id": "18-010309-AAA-RV3",
    "component_ref": "R131",
    "condition_label": "NORMAL",
    "nominal_value": 100.0,
    "measured_value": 99.824,
    "unit": "Ohms",
    "ict_status": "PASS",
    "laser_profile_height_um": 44.78,
    "side_overhang_percent": 18.2,
    "coplanarity_um": 1.12,
    "aoi_status": "PASS",
    "overall_status": "PASS",
    "filename": "Board1_R131_Body_18-010309-AAA-RV3_20260822_082401620_Shift_4.jpg"
  }
}
```

### 2. Final Diagnostic Output (`outputs/inspection_results.json`)
```json
[
  {
    "board_id": "18-010309-AAA-RV3",
    "component_ref": "R131",
    "image_name": "Board1_R131_Body_18-010309-AAA-RV3_20260822_082401620_Shift_4.jpg",
    "defect_category": "no defect",
    "defect_location": {
      "landmark": "center land pattern",
      "bounding_box": [320, 410, 680, 590]
    },
    "explanation": "Component body is centered on pads with 18.2% overhang, safely compliant with IPC-A-610 Class 2 limits (<50%). Electrical resistance measured 99.82 Ohms against a 100.0 Ohm nominal value. Visual and measurement evidence align with zero contradictions.",
    "contradictions_found": "None. Visual presence matches normal resistance profile.",
    "confidence_score": 0.98,
    "self_check_passed": true,
    "errors": []
  }
]
```

---

## 🛠 Advanced Configuration

* **Tuning IPC Tolerances:** Modify tolerances and component packages in `generate_telemetry.py` under `self.default_specs` to represent custom SMD sizes (e.g., 0402, 0603, 0805, QFP).
* **Direct MCP Telemetry:** If connecting to live physical testers, set `outputs/telemetry_by_image.json` aside or configure `tool3_measurement_evidence_node` in `agent.py` to stream directly from your live factory Model Context Protocol (MCP) server.