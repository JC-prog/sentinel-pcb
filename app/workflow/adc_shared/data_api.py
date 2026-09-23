"""Run with one worker: python -m uvicorn adc_shared.data_api:app --port 8000"""
from contextlib import asynccontextmanager
import os
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from qdrant_client import QdrantClient
from .repository import Repository, Conflict, RUNS, REVIEWS

class Workflow(BaseModel):
    result: dict

class Review(BaseModel):
    result: dict


def create_app(repository=None):
    @asynccontextmanager
    async def lifespan(app):
        if repository is None:
            client = QdrantClient(url=os.getenv('QDRANT_URL', 'http://127.0.0.1:6333'),
                                  api_key=os.getenv('QDRANT_API_KEY'), timeout=30)
            try:
                app.state.repo = Repository(client)
                yield
            finally:
                client.close()
        else:
            app.state.repo = repository
            yield

    api = FastAPI(title='ADC Shared Data API', lifespan=lifespan)

    @api.exception_handler(KeyError)
    async def missing(request, exc):
        return JSONResponse(status_code=404, content={'detail': str(exc)})

    @api.exception_handler(Conflict)
    async def conflict(request, exc):
        return JSONResponse(status_code=409, content={'detail': str(exc)})

    @api.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse(status_code=422, content={'detail': str(exc)})

    @api.get('/health')
    def health():
        api.state.repo.client.get_collections()
        return {'status': 'ok', 'database': 'qdrant'}

    @api.put('/runs/{run_id}')
    def save(run_id: str, body: Workflow):
        return api.state.repo.save_run(run_id, body.result)

    @api.get('/runs')
    def runs(limit: int = Query(100, ge=1, le=500), cursor: str | None = None):
        items, nxt = api.state.repo.page(RUNS, limit, cursor)
        return {'items': [Repository.summary(x) for x in items], 'next_cursor': nxt}

    @api.get('/runs/{run_id}')
    def run(run_id: str):
        return api.state.repo.ready_run(run_id)['result']

    @api.get('/runs/{run_id}/samples/{sample_id}')
    def sample(run_id: str, sample_id: str):
        return api.state.repo.sample(run_id, sample_id)

    @api.get('/runs/{run_id}/review-cases')
    def cases(run_id: str, limit: int = Query(100, ge=1, le=500), cursor: str | None = None):
        return api.state.repo.review_cases(run_id, limit, cursor)

    @api.put('/runs/{run_id}/reviews/{sample_id}')
    def save_review(run_id: str, sample_id: str, body: Review):
        return api.state.repo.save_review(run_id, sample_id, body.result)

    @api.get('/runs/{run_id}/reviews/{sample_id}')
    def get_review(run_id: str, sample_id: str):
        api.state.repo.ready_run(run_id)
        return api.state.repo.get(REVIEWS, run_id, sample_id)

    return api

app = create_app()
