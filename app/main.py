import json
from typing import Annotated

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from app.agents.orchestrator import WorkflowResult, run_workflow
from app.settings import settings

app = FastAPI(title="SentinelPCB")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allow_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/inspections")
async def create_inspection(
    image: Annotated[UploadFile, File()],
    metadata: Annotated[str, Form()] = "{}",
) -> WorkflowResult:
    """Ingest API: the "AOI Camera + PLC" edge in docs/ADC-Design-Doc-Team10-V2.md §5 posts here.
    Runs the deterministic backbone (Dataset Preparation -> Dataset Quality -> Multi-Modal
    Inference -> Orchestrator policy decision) and returns the result synchronously.
    """

    image_bytes = await image.read()
    parsed_metadata: dict[str, str] = json.loads(metadata) if metadata else {}
    return run_workflow(
        image=image_bytes,
        image_filename=image.filename or "unknown",
        metadata=parsed_metadata,
    )
