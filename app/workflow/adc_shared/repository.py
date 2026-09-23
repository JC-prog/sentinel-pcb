"""Payload-only storage. All mutations go through one data API process."""
import hashlib
import json
from datetime import datetime, timezone
from threading import RLock
from uuid import NAMESPACE_URL, uuid5
from qdrant_client import models

RUNS = 'adc_orchestrator_runs'
SAMPLES = 'adc_inspection_results'
REVIEWS = 'adc_agent2_reviews'


def point_id(*parts):
    return str(uuid5(NAMESPACE_URL, json.dumps(parts)))


def now():
    return datetime.now(timezone.utc).isoformat()


class Conflict(ValueError):
    pass


class Repository:
    def __init__(self, client, indexes=True):
        self.client = client
        self.lock = RLock()
        for collection in (RUNS, SAMPLES, REVIEWS):
            if not client.collection_exists(collection):
                client.create_collection(collection, vectors_config={})
            if indexes:
                for field in ('run_id', 'sample_id', 'final_decision'):
                    client.create_payload_index(collection, field, models.PayloadSchemaType.KEYWORD, wait=True)

    def get(self, collection, *key):
        records = self.client.retrieve(collection, [point_id(*key)], with_payload=True)
        if not records:
            raise KeyError('/'.join(key))
        return records[0].payload

    def put(self, collection, key, payload):
        self.client.upsert(collection, [models.PointStruct(id=point_id(*key), vector={}, payload=payload)], wait=True)

    def save_run(self, run_id, result):
        if not isinstance(result.get('status'), str):
            raise ValueError('result.status must be a string')
        for field in ('prepared_samples', 'inference_results'):
            records = result.get(field)
            if not isinstance(records, list):
                raise ValueError(f'{field} must be a list')
            ids = [r.get('sample_id') if isinstance(r, dict) else None for r in records]
            if any(not isinstance(s, str) or not s for s in ids) or len(set(ids)) != len(ids):
                raise ValueError(f'{field} requires unique nonempty sample_id values')
        samples = {s['sample_id']: s for s in result['prepared_samples']}
        inference = {s['sample_id']: s for s in result['inference_results']}
        if set(inference) - set(samples):
            raise ValueError('Inference references unknown sample IDs')
        raw = json.dumps(result, sort_keys=True, ensure_ascii=False, allow_nan=False)
        digest = hashlib.sha256(raw.encode()).hexdigest()
        with self.lock:
            try:
                existing = self.get(RUNS, run_id)
            except KeyError:
                existing = None
            if existing and existing['digest'] != digest:
                raise Conflict('Run ID already contains different content; use a new run ID')
            if existing and existing['storage_status'] == 'READY':
                return self.summary(existing)
            run = {'run_id': run_id, 'digest': digest, 'status': result['status'],
                   'saved_at_utc': existing['saved_at_utc'] if existing else now(),
                   'storage_status': 'WRITING', 'result': result}
            self.put(RUNS, (run_id,), run)
            points = []
            for sid, sample in samples.items():
                pred = inference.get(sid)
                points.append(models.PointStruct(id=point_id(run_id, sid), vector={}, payload={
                    'run_id': run_id, 'sample_id': sid, 'sample': sample, 'inference': pred,
                    'final_decision': pred.get('final_decision') if pred else None}))
            for offset in range(0, len(points), 100):
                self.client.upsert(SAMPLES, points[offset:offset+100], wait=True)
            run['storage_status'] = 'READY'
            self.put(RUNS, (run_id,), run)
            return self.summary(run)

    @staticmethod
    def summary(run):
        return {k: run[k] for k in ('run_id', 'status', 'storage_status', 'saved_at_utc')}

    def ready_run(self, run_id):
        run = self.get(RUNS, run_id)
        if run['storage_status'] != 'READY':
            raise Conflict('Run write incomplete; retry PUT with the same run ID and JSON')
        return run

    def sample(self, run_id, sample_id):
        self.ready_run(run_id)
        return self.get(SAMPLES, run_id, sample_id)

    def page(self, collection, limit=100, cursor=None, conditions=None):
        records, next_offset = self.client.scroll(collection, limit=limit, offset=cursor,
            scroll_filter=models.Filter(must=conditions) if conditions else None, with_payload=True)
        return [r.payload for r in records], str(next_offset) if next_offset else None

    def review_cases(self, run_id, limit=100, cursor=None):
        self.ready_run(run_id)
        conditions = [models.FieldCondition(key=k, match=models.MatchValue(value=v))
                      for k, v in [('run_id', run_id), ('final_decision', 'REVIEW_REQUIRED')]]
        items, next_cursor = self.page(SAMPLES, limit, cursor, conditions)
        return {'items': items, 'next_cursor': next_cursor}

    def save_review(self, run_id, sample_id, result):
        self.sample(run_id, sample_id)
        # First result is immutable. Identical retries succeed; changed results conflict.
        with self.lock:
            try:
                old = self.get(REVIEWS, run_id, sample_id)
            except KeyError:
                old = None
            if old:
                if old['result'] != result:
                    raise Conflict('Review already exists with different content')
                return old
            record = {'run_id': run_id, 'sample_id': sample_id, 'saved_at_utc': now(), 'result': result}
            self.put(REVIEWS, (run_id, sample_id), record)
            return record
