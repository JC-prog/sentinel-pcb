"""Agent 2 Explainability REST API Service. Pure HTTP REST (No A2A)."""
import os
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Optional
from urllib.parse import quote
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import httpx
from .client import DataClient


class ReviewRequest(BaseModel):
    run_id: Optional[str] = "standalone"
    sample_id: Optional[str] = "standalone"
    case: Optional[Dict[str, Any]] = None
    parameters: Optional[Dict[str, Any]] = None


def build_input(case: Dict[str, Any]) -> Dict[str, Any]:
    sample = case.get("sample", {})
    inference = case.get("inference") or {}
    details = inference.get("details") or {}
    defect = details.get("defect_classification") or {}
    feature = details.get("feature_classification") or {}

    preliminary_defect = defect.get("prediction") or sample.get("machine_defect", "NoDefect")
    clean_defect = preliminary_defect.split("_")[0] if isinstance(preliminary_defect, str) else "NoDefect"

    return {
        "board_id": sample.get("board") or sample.get("board_id", "UNKNOWN"),
        "component_ref": sample.get("component") or sample.get("component_id", "UNKNOWN"),
        "defect_image_path": sample.get("defect_image") or sample.get("defect_image_path"),
        "golden_image_path": sample.get("golden_image") or sample.get("golden_image_path"),
        "feature_type": feature.get("prediction") or sample.get("feature_type", "Body"),
        "preliminary_defect": clean_defect,
        "confidence": defect.get("confidence") or sample.get("baseline_confidence", 0.5),
        "aoi_measurements": sample.get("failed_inspections", {})
    }


def create_app(data=None, reviewer=None, enabled=None):
    api = FastAPI(title="ADC Agent 2 REST API (No A2A)")
    data = data or DataClient()
    lock = Lock()
    enabled = os.getenv("ADC_ENABLE_AGENT2") == "1" if enabled is None else enabled

    @api.get("/health")
    def health():
        return {"status": "ok", "service": "agent2_rest_api", "execution_enabled": enabled}

    @api.get("/context/{run_id}/{sample_id}")
    def context(run_id: str, sample_id: str):
        return data.get_sample(run_id, sample_id)

    @api.post("/reviews")
    def review(body: ReviewRequest):
        if not enabled:
            raise HTTPException(503, "Agent 2 execution disabled. Set ADC_ENABLE_AGENT2=1.")

        if not lock.acquire(blocking=False):
            raise HTTPException(409, "Agent 2 is busy; retry later")

        try:
            # 1. Resolve payload from REST parameters, raw case, or Shared DB
            if body.parameters:
                payload = body.parameters
            elif body.case:
                payload = build_input(body.case)
            elif body.run_id and body.sample_id:
                try:
                    case = data.get_sample(body.run_id, body.sample_id)
                    payload = build_input(case)
                except Exception as exc:
                    raise HTTPException(404, f"Could not find sample in database: {exc}")
            else:
                raise HTTPException(422, "Request must provide 'parameters', 'case', or 'run_id'+'sample_id'")

            # 2. Execute Explainability Review Pipeline
            execute = reviewer
            if execute is None:
                from src.agent2_explainability.pipeline.review_graph import execute_explainability_review
                execute = execute_explainability_review

            output = execute(payload)

            result = {
                "review_status": "GENERATED_UNVALIDATED",
                "source": "agent2_rest_pipeline",
                "output": output,
                "warning": "Validate evidence before promoting to approved verdict."
            }

            # 3. If tied to a database run, persist back to Shared DB
            if body.run_id and body.sample_id and body.run_id != "standalone":
                try:
                    path = f"/runs/{quote(body.run_id, safe='')}/reviews/{quote(body.sample_id, safe='')}"
                    data.request("PUT", path, json={"result": result})
                except Exception:
                    pass

            return {"status": "ok", "result": result, "output": output}

        except Exception as exc:
            raise HTTPException(502, f"Agent 2 review failed: {type(exc).__name__}: {exc}") from exc
        finally:
            lock.release()

    return api


app = create_app()
