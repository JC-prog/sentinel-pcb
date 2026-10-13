# ADC: REST API + shared Qdrant

This add-on plugs into the supplied pcb_agentic_inspector project. Agent 1 saves
through a shared data API; Agent 2 reads the same evidence and saves reviews through
that API. Qdrant runs as a server. No A2A, SQLite, embeddings, or API key is needed
for persistence. Existing IPC vector collections are not modified.

## Architecture

- Agent 1 UI -> Shared Data API (port 8000) -> Qdrant server (port 6333).
- Agent 1 or an operator -> Agent 2 REST API (port 8001), when enabled.
- Agent 2 REST API -> Shared Data API to read evidence and store review output.
- Image files stay on disk. Both agents need readable paths, or a later shared
  image/object-storage endpoint. Image bytes are not uploaded to Qdrant.

The data API centralizes schema and database access. Agents do not open database
folders. The Agent 2 wrapper uses your existing review graph, not a new reasoning
implementation. Its execution is disabled by default, while context reads work.

## 1. Install into your existing project

Extract the contents of this package into:
`D:\zwang\Project\pcb_agentic_inspector`

Keep `adc_shared/`, `adc_rest.py`, `integrate_ui.py`, `requirements-rest.txt`, and
`compose.qdrant.yaml` at the project root, beside your existing main.py.
The add-on does not replace your main.py, original Agent 2 files, or models.

In PyCharm PowerShell:

```powershell
cd "D:\zwang\Project\pcb_agentic_inspector"
.\.venv\Scripts\python.exe -m pip install -r requirements-rest.txt
```

If you use a different Python interpreter, replace `.\.venv\Scripts\python.exe`
with that interpreter (or `py` when that is your project's configured interpreter).
The original orchestrator still needs its original requirements.txt dependencies.

## 2. Start shared Qdrant

With Docker Desktop running:

```powershell
docker compose -f compose.qdrant.yaml up -d
```

The supplied Compose file uses a persistent Docker named volume, a localhost-only
port, and the current Qdrant image. The dashboard is http://127.0.0.1:6333/dashboard.
For reproducible deployment, pin the image tag after your local verification.

If you ALREADY have a Qdrant server on port 6333, skip this step and reuse that
server. Do not start a second container on the same port. Set QDRANT_URL to the
existing server URL if different.

The embedded Python-client folder `qdrant_db/` is not automatically migrated into
the server. Do not mount that folder as server storage. Leave it unchanged; this
package imports result JSON into new server collections. Migrating existing
embedded vector collections is a separate export/import task if you need them.
The original Agent 2 graph's precedent retrieval is still hardcoded; this add-on
supplies persisted inspection context, not a new semantic RAG implementation.

## 3. Start the shared data API (terminal 1)

```powershell
$env:QDRANT_URL = "http://127.0.0.1:6333"
.\.venv\Scripts\python.exe -m uvicorn adc_shared.data_api:app --host 127.0.0.1 --port 8000 --workers 1
```

Keep this terminal open. Open http://127.0.0.1:8000/docs for interactive API calls.
The service must connect to Qdrant successfully at startup.

Use ONE data API process/worker. Import serialization uses an in-process lock;
Qdrant is not a relational transaction manager. Do not run extra copies of this
API against these collections. Qdrant itself may still serve other collections.

## 4. Import your existing result (terminal 2)

The uploaded result is included as example_result.json for a reproducible test:

```powershell
.\.venv\Scripts\python.exe adc_rest.py import .\example_result.json --run-id uploaded-result-001
.\.venv\Scripts\python.exe adc_rest.py runs
.\.venv\Scripts\python.exe adc_rest.py reviews uploaded-result-001
```

Expected: 48 sample records and one review case, S000001. The result is preserved
as a complete JSON payload, including plan/tool history, errors, and counters.
For your next actual run:

```powershell
.\.venv\Scripts\python.exe adc_rest.py import .\outputs\result.json --run-id my-test-002
```

Use a NEW ID for a new run. Retrying identical content with the same ID is safe;
a different result with an existing ID returns HTTP 409. If you changed the output
location in the UI, use that actual JSON path.

## 5. Automatically save future orchestrator UI runs

The patcher makes a backup, fixes project/import paths, keeps Agent 2 and old
embedded Qdrant indexing disabled, and inserts REST persistence after JSON output.
It checks the expected source structure before writing. It can be applied twice
without duplicating the integration.

```powershell
.\.venv\Scripts\python.exe integrate_ui.py
$env:ADC_DATA_URL = "http://127.0.0.1:8000"
.\.venv\Scripts\python.exe src/agent1_orchestrator/ui.py
```

The UI log reports the saved run ID. A sibling `result.run_id.txt` file records that
ID before upload. If the API is unavailable, JSON stays on disk and the UI log
reports the database failure. Once the service is back, retry the upload:

```powershell
$runId = (Get-Content .\outputs\result.run_id.txt -Raw).Trim()
.\.venv\Scripts\python.exe adc_rest.py import .\outputs\result.json --run-id $runId
```

Use your chosen UI output folder for both files. Preserve the JSON/ID pair before
another run overwrites those local output files. The original completion dialog
still reports the JSON output; the UI log is the database-save confirmation.
Runs that return REVIEW_REQUIRED or ABORTED are saved too; exceptions before a
workflow state is returned are not automatically captured by this UI insertion.

If your UI has changed enough that the patcher refuses it, keep your original file
and manually add `from adc_shared.client import DataClient`, then call
`DataClient().save_run(asdict(state), run_id=...)` after its JSON save. The generated
backup lets you restore the exact pre-patch UI.

## 6. Agent 2 reads data without executing models (optional terminal 3)

```powershell
$env:ADC_DATA_URL = "http://127.0.0.1:8000"
$env:ADC_ENABLE_AGENT2 = "0"
.\.venv\Scripts\python.exe -m uvicorn adc_shared.agent2_api:app --host 127.0.0.1 --port 8001 --workers 1
```

Open http://127.0.0.1:8001/docs and call:
`GET /context/uploaded-result-001/S000001`.
This verifies Agent 2 can retrieve the image paths, measurements and inference
from shared Qdrant through REST without running LLMs or starting the A2A server.
Do not run the original A2A server simultaneously on port 8001.

You can also read from Python in either agent:

```python
from adc_shared.client import DataClient
client = DataClient()
case = client.get_sample('uploaded-result-001', 'S000001')
for item in client.review_cases('uploaded-result-001'):
    print(item['sample_id'], item['inference']['status'])
```

## 7. Enable real Agent 2 reviews later

Stop only the Agent 2 REST process, then set `ADC_ENABLE_AGENT2=1` and restart it.
Install your original project dependencies, start its required model services,
and provide any required credentials to that process. Do not put keys in code.

Send `POST http://127.0.0.1:8001/reviews` with:

```json
{"run_id": "your-run-id", "sample_id": "your-sample-id"}
```

The endpoint synchronously loads the sample, calls the existing review graph,
and stores its output in shared Qdrant. It returns an existing saved review on
identical repeated requests, and returns 409 when busy. Use an appropriate HTTP
timeout for model latency. It is not a durable queue: a crash between model work
and saving can require recomputation. Review revisions are not implemented;
changed content for an already saved review is rejected rather than overwritten.

IMPORTANT: your supplied sample has FEATURE_CLASSIFICATION_UNCERTAIN and no defect
classification. Its context can be read, but this existing-pipeline adapter returns
422 if you try to execute a review. Feature uncertainty needs a routing or
reclassification path before the existing baseline-defect review pipeline. The
adapter does not invent a defect label or substitute a feature label for one.

The existing graph can use mock IPC precedents, default telemetry and fallback
visual evidence. This wrapper labels generated output GENERATED_UNVALIDATED,
even if the graph's self_check_passed flag is true. It does not auto-accept it.
Evidence validation and handling missing measurements remain Agent 2 work.
Your Windows image paths must be readable by the Agent 2 process.

## Data model and endpoints

| Collection | Key | Content |
|---|---|---|
| adc_orchestrator_runs | run_id | Complete workflow JSON and persistence state |
| adc_inspection_results | run_id + sample_id | Sample plus optional inference |
| adc_agent2_reviews | run_id + sample_id | Separate Agent 2 output |

Collections use payload-only points (`vectors_config={}`, `vector={}`), with no
pseudo-embeddings. Point IDs are stable UUIDs derived from keys. Agent 1 originals
remain separate from Agent 2 output. Records without inference remain null;
preparation/verification failures are retained, not mislabelled as review verdicts.

| Service | Method/path | Purpose |
|---|---|---|
| Data :8000 | GET /health | Check database reachability |
| Data :8000 | PUT /runs/{run_id} | Retry-safe workflow import |
| Data :8000 | GET /runs | Paginated run summaries |
| Data :8000 | GET /runs/{run_id} | Complete original result |
| Data :8000 | GET /runs/{run_id}/samples/{sample_id} | Joined evidence and inference |
| Data :8000 | GET /runs/{run_id}/review-cases | Paginated REVIEW_REQUIRED records |
| Data :8000 | PUT/GET /runs/{run_id}/reviews/{sample_id} | Persist/read review |
| Agent 2 :8001 | GET /context/{run_id}/{sample_id} | Fetch stored context |
| Agent 2 :8001 | POST /reviews | Explicit, optional synchronous review |

Run and review-case listings return `items` and `next_cursor`; pass the cursor on
the next call. Records are ordered by point ID, not by time. The CLI review command
handles all pages; the CLI runs command shows the first page (default 100).

A run is written as WRITING, its samples are upserted in batches, then it is marked
READY. The API blocks reads of incomplete runs; retrying the same input completes
an interrupted write. This is recoverable application logic, not an atomic
cross-collection database transaction. Direct Qdrant readers can see intermediate
points, so agents should use this API. Full run JSON is also one Qdrant payload;
very large runs may need object storage and a manifest instead.

This is a localhost development setup without application authentication. For
other machines, configure API authentication/TLS and a shared image location before
exposing the endpoints. A queue/worker layer is the later step for durable reviews.

## Validation performed

- Nine automated tests passed: JSON round-trip, review evidence joins, missing
  records, retries, overwrite conflicts, incomplete-write recovery, pagination,
  disabled Agent 2, uncertain-feature rejection, and mock review/retry behavior.
- Uploaded result tested through FastAPI TestClient with an actual Qdrant local
  engine, then the database was closed/reopened to verify persistence. JSON matched
  exactly; 48 samples and one review case were retained.
- UI patch tested on the supplied project source; second application was a no-op.
- No Docker runtime was available here, so the Qdrant Docker/HTTP deployment and
  actual LLM/ONNX/Agent 2 execution were NOT tested in this environment.
- Tested Python 3.12; qdrant-client 1.19.1, FastAPI 0.141.1, httpx 0.28.1. A
  Starlette TestClient deprecation warning appeared; tests passed.

```powershell
.\.venv\Scripts\python.exe -m unittest test_rest -v
```

Official references:
- https://qdrant.tech/documentation/quickstart/
- https://qdrant.tech/documentation/manage-data/collections/
- https://qdrant.tech/documentation/search/filtering/
