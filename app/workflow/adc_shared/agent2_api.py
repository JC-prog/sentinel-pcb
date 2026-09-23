"""Optional synchronous Agent 2 REST wrapper. Disabled until ADC_ENABLE_AGENT2=1.

One process/worker only. This is not a durable execution queue.
"""
import os
from pathlib import Path
from threading import Lock
from urllib.parse import quote
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import httpx
from .client import DataClient

class ReviewRequest(BaseModel):
    run_id: str
    sample_id: str


def build_input(case):
    sample = case['sample']
    inference = case.get('inference') or {}
    details = inference.get('details') or {}
    defect = details.get('defect_classification') or {}
    feature = details.get('feature_classification') or {}
    if inference.get('final_decision') != 'REVIEW_REQUIRED':
        raise ValueError('Sample is not marked REVIEW_REQUIRED')
    if not defect.get('prediction'):
        raise ValueError('No defect classification available. Feature uncertainty needs routing/reclassification before this existing Agent 2 pipeline can review it.')
    for field in ('defect_image', 'golden_image'):
        if not sample.get(field) or not Path(sample[field]).is_file():
            raise ValueError(f'{field} is not accessible on the Agent 2 host: {sample.get(field)}')
    return {'board_id': sample.get('board'), 'component_ref': sample.get('component'),
            'defect_image_path': sample['defect_image'], 'golden_image_path': sample['golden_image'],
            'feature_type': feature.get('prediction'), 'preliminary_defect': defect['prediction'],
            'confidence': defect.get('confidence', 0),
            'aoi_measurements': sample.get('failed_inspections', {})}


def create_app(data=None, reviewer=None, enabled=None):
    api = FastAPI(title='ADC Agent 2 REST API')
    data = data or DataClient()
    lock = Lock()
    enabled = os.getenv('ADC_ENABLE_AGENT2') == '1' if enabled is None else enabled

    @api.exception_handler(httpx.HTTPStatusError)
    async def upstream_status(request, exc):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=exc.response.status_code,
                            content={'detail': exc.response.text})

    @api.exception_handler(httpx.RequestError)
    async def upstream_unavailable(request, exc):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=503, content={'detail': 'Shared data API unavailable'})

    @api.get('/health')
    def health():
        return {'status': 'ok', 'execution_enabled': enabled}

    @api.get('/context/{run_id}/{sample_id}')
    def context(run_id: str, sample_id: str):
        return data.get_sample(run_id, sample_id)

    @api.post('/reviews')
    def review(body: ReviewRequest):
        if not enabled:
            raise HTTPException(503, 'Agent 2 execution disabled. Context retrieval is available.')
        path = f'/runs/{quote(body.run_id, safe="")}/reviews/{quote(body.sample_id, safe="")}'
        # Serialize synchronous reviews; another call can retry after the active one.
        if not lock.acquire(blocking=False):
            raise HTTPException(409, 'Agent 2 is busy; retry later')
        try:
            try:
                return data.request('GET', path)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code != 404:
                    raise
            case = data.get_sample(body.run_id, body.sample_id)
            try:
                payload = build_input(case)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            try:
                execute = reviewer
                if execute is None:
                    from src.agent2_explainability.pipeline.review_graph import execute_explainability_review
                    execute = execute_explainability_review
                output = execute(payload)
            except Exception as exc:
                raise HTTPException(502, f'Agent 2 execution failed: {type(exc).__name__}: {exc}') from exc
            # Preserve original agent behavior but do not promote its heuristic output
            # to an approved verdict. An explicit evidence validation step is still needed.
            result = {'review_status': 'GENERATED_UNVALIDATED',
                      'source': 'existing_agent2_pipeline', 'output': output,
                      'warning': 'Existing pipeline can use mock precedents and fallback evidence. Validate evidence before accepting this review.'}
            return data.request('PUT', path, json={'result': result})
        finally:
            lock.release()
    return api

app = create_app()
