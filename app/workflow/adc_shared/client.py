"""Small shared REST client; importing this does not connect to Qdrant."""
import os
from urllib.parse import quote
import uuid
import httpx

class DataClient:
    def __init__(self, url=None):
        self.url = (url or os.getenv('ADC_DATA_URL', 'http://127.0.0.1:8000')).rstrip('/')

    def request(self, method, path, **kwargs):
        response = httpx.request(method, self.url + path, timeout=60, **kwargs)
        response.raise_for_status()
        return response.json()

    def save_run(self, result, run_id=None):
        run_id = run_id or str(uuid.uuid4())
        return self.request('PUT', '/runs/' + quote(run_id, safe=''), json={'result': result})

    def get_sample(self, run_id, sample_id):
        return self.request('GET', f'/runs/{quote(run_id, safe="")}/samples/{quote(sample_id, safe="")}')

    def review_cases(self, run_id):
        cursor = None
        while True:
            page = self.request('GET', f'/runs/{quote(run_id, safe="")}/review-cases',
                                params={'cursor': cursor} if cursor else {})
            yield from page['items']
            cursor = page['next_cursor']
            if not cursor:
                return

    def get_review(self, run_id, sample_id):
        return self.request('GET', f'/runs/{quote(run_id, safe="")}/reviews/{quote(sample_id, safe="")}')

    def reviews(self, run_id):
        return self.request('GET', f'/runs/{quote(run_id, safe="")}/reviews')['items']

    def save_decision(self, run_id, sample_id, selected_source, final_result, operator_notes=None, machine_result=None, ai_result=None):
        path = f'/runs/{quote(run_id, safe="")}/reviews/{quote(sample_id, safe="")}/decision'
        return self.request('PUT', path, json={
            'selected_source': selected_source, 'final_result': final_result,
            'operator_notes': operator_notes, 'machine_result': machine_result, 'ai_result': ai_result
        })
