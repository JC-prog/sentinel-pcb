import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from qdrant_client import QdrantClient
from adc_shared.repository import Repository, SAMPLES, RUNS
from adc_shared.data_api import create_app
from adc_shared.agent2_api import create_app as create_agent2

class Tests(unittest.TestCase):
    def setUp(self):
        self.db = QdrantClient(':memory:')
        self.repo = Repository(self.db, indexes=False)
        self.api = TestClient(create_app(self.repo))
        self.api.__enter__()
        self.addCleanup(self.api.__exit__, None, None, None)
        self.addCleanup(self.db.close)
        self.result = {'status': 'REVIEW_REQUIRED', 'prepared_samples': [
            {'sample_id': 'S1', 'component': 'U1', 'failed_inspections': {'AI2': {'value': '0'}}},
            {'sample_id': 'S2', 'preparation_status': 'FAILED'}],
            'inference_results': [{'sample_id': 'S1', 'final_decision': 'REVIEW_REQUIRED',
                                  'status': 'FEATURE_CLASSIFICATION_UNCERTAIN'}],
            'plan_history': [{'decision': 'finalize'}]}

    def save(self, rid='r1', result=None):
        return self.api.put(f'/runs/{rid}', json={'result': result or self.result})

    def test_lossless_join_and_missing(self):
        self.assertEqual(self.save().status_code, 200)
        self.assertEqual(self.api.get('/runs/r1').json(), self.result)
        cases = self.api.get('/runs/r1/review-cases').json()['items']
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0]['sample']['component'], 'U1')
        self.assertIsNone(self.api.get('/runs/r1/samples/S2').json()['inference'])
        self.assertEqual(self.api.get('/runs/nope').status_code, 404)

    def test_idempotency_and_conflict(self):
        self.save()
        self.save()
        self.assertEqual(self.db.count(SAMPLES).count, 2)
        changed = copy.deepcopy(self.result)
        changed['status'] = 'COMPLETED'
        self.assertEqual(self.save(result=changed).status_code, 409)
        self.save('r2')
        self.assertEqual(self.db.count(SAMPLES).count, 4)

    def test_incomplete_write_recoverable(self):
        original = self.db.upsert
        def interrupted(collection, *a, **kw):
            if collection == SAMPLES:
                raise RuntimeError('simulated storage interruption')
            return original(collection, *a, **kw)
        with patch.object(self.db, 'upsert', side_effect=interrupted):
            with self.assertRaises(RuntimeError):
                self.repo.save_run('r1', self.result)
        self.assertEqual(self.api.get('/runs/r1').status_code, 409)
        self.assertEqual(self.api.get('/runs/r1/review-cases').status_code, 409)
        self.assertEqual(self.save().status_code, 200)

    def test_bad_sample_ids(self):
        self.result['prepared_samples'].append(self.result['prepared_samples'][0])
        self.assertEqual(self.save().status_code, 422)
        self.assertEqual(self.db.count(RUNS).count, 0)

    def test_review_write_retries(self):
        self.save()
        body = {'result': {'diagnosis': 'unit test fixture'}}
        url = '/runs/r1/reviews/S1'
        self.assertEqual(self.api.put(url, json=body).status_code, 200)
        self.assertEqual(self.api.put(url, json=body).status_code, 200)
        self.assertEqual(self.api.get(url).json()['result'], body['result'])
        self.assertEqual(self.api.put(url, json={'result': {}}).status_code, 409)

    def test_pagination(self):
        self.save('r1'); self.save('r2')
        first = self.api.get('/runs?limit=1').json()
        second = self.api.get('/runs', params={'limit': 1, 'cursor': first['next_cursor']}).json()
        self.assertNotEqual(first['items'][0]['run_id'], second['items'][0]['run_id'])

    def test_agent2_disabled(self):
        with TestClient(create_agent2(enabled=False)) as api:
            self.assertEqual(api.post('/reviews', json={'run_id': 'r1', 'sample_id': 'S1'}).status_code, 503)

    def test_agent2_context_and_uncertain_rejection(self):
        self.save()
        data_api = self.api
        class Data:
            def request(self, method, path, **kw):
                r = data_api.request(method, path, **kw)
                r.raise_for_status()
                return r.json()
            def get_sample(self, rid, sid):
                return self.request('GET', f'/runs/{rid}/samples/{sid}')
        with TestClient(create_agent2(data=Data(), enabled=True)) as api:
            self.assertEqual(api.get('/context/r1/S1').json()['sample']['component'], 'U1')
            self.assertEqual(api.post('/reviews', json={'run_id': 'r1', 'sample_id': 'S1'}).status_code, 422)

    def test_agent2_completed_review_is_reused(self):
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / 'image.jpg'
            image.write_bytes(b'test-only')
            self.result['prepared_samples'][0].update(defect_image=str(image), golden_image=str(image))
            self.result['inference_results'][0]['details'] = {
                'defect_classification': {'prediction': 'WrongPart', 'confidence': 0.6},
                'feature_classification': {'prediction': 'Text', 'confidence': 0.9}}
            self.save()
            data_api = self.api
            class Data:
                def request(self, method, path, **kw):
                    r = data_api.request(method, path, **kw); r.raise_for_status(); return r.json()
                def get_sample(self, rid, sid):
                    return self.request('GET', f'/runs/{rid}/samples/{sid}')
            calls = []
            def reviewer(payload):
                calls.append(payload)
                return {'diagnosis': 'mock for contract test only'}
            with TestClient(create_agent2(data=Data(), reviewer=reviewer, enabled=True)) as api:
                for _ in range(2):
                    r = api.post('/reviews', json={'run_id': 'r1', 'sample_id': 'S1'})
                    self.assertEqual(r.status_code, 200)
                self.assertEqual(len(calls), 1)
                self.assertEqual(r.json()['result']['review_status'], 'GENERATED_UNVALIDATED')

if __name__ == '__main__':
    unittest.main()
