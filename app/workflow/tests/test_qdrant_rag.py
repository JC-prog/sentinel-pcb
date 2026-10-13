"""Unit tests for the Qdrant Vector RAG query pipeline.

Ported from pcb_agentic_inspector and adapted: upstream patched `get_embedder` (FastEmbed); this app's
reader embeds through `embed_texts` (OpenAI) and reads the shared Qdrant, so that is what is patched
here. The two upstream cases are kept as they were; the last two cover what the adaptation added -
a minimum score, and no invented vector when no embedding can be made. The fuller suite (ingest,
chunking, an in-memory Qdrant) is tests/workflow/test_ipc_precedents.py in the main run.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import src.data.qdrant_store as qdrant_store
from app.shared.config.settings import settings


def _client_with(points):
    mock_client = MagicMock()
    mock_client.collection_exists.return_value = True
    mock_response = MagicMock()
    mock_response.points = points
    mock_client.query_points.return_value = mock_response
    return mock_client


def _point(score, **payload):
    point = MagicMock()
    point.score = score
    point.payload = payload
    return point


class TestQdrantRAG(unittest.TestCase):

    @patch("src.data.qdrant_store.get_qdrant_client")
    @patch("src.data.qdrant_store.embed_texts")
    def test_query_defect_precedents_handles_hits(self, mock_embed, mock_client_func):
        mock_client_func.return_value = _client_with(
            [
                _point(
                    0.8765,
                    text="Tombstoning is caused by wetting force imbalance.",
                    source_file="PCB Tombstoning.pdf",
                    page=17,
                )
            ]
        )
        mock_embed.return_value = [[0.1] * 1536]

        results = qdrant_store.query_defect_precedents("tombstone causes", top_k=1)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["score"], 0.8765)
        self.assertEqual(results[0]["source"], "PCB Tombstoning.pdf")
        self.assertEqual(results[0]["page"], 17)

    @patch("src.data.qdrant_store.get_qdrant_client")
    @patch("src.data.qdrant_store.embed_texts")
    def test_query_precedents_handles_missing_collection(self, mock_embed, mock_client_func):
        mock_client = MagicMock()
        mock_client.collection_exists.return_value = False
        mock_client_func.return_value = mock_client
        mock_embed.return_value = [[0.1] * 1536]

        results = qdrant_store.query_defect_precedents("test query")
        self.assertEqual(results, [])

    @patch("src.data.qdrant_store.get_qdrant_client")
    @patch("src.data.qdrant_store.embed_texts")
    def test_a_hit_below_the_minimum_score_is_not_returned(self, mock_embed, mock_client_func):
        mock_client_func.return_value = _client_with(
            [
                _point(0.62, text="relevant", source_file="a.pdf", page=1),
                _point(0.12, text="unrelated", source_file="b.pdf", page=2),
            ]
        )
        mock_embed.return_value = [[0.1] * 1536]

        with patch.object(settings, "ipc_precedent_min_score", 0.3):
            results = qdrant_store.query_defect_precedents("anything", top_k=2)

        self.assertEqual([r["text"] for r in results], ["relevant"])

    @patch("src.data.qdrant_store.get_qdrant_client")
    @patch("src.data.qdrant_store.embed_texts", side_effect=RuntimeError("no key"))
    def test_no_embedding_means_no_search_and_no_invented_vector(self, mock_embed, mock_client_func):
        results = qdrant_store.query_defect_precedents("tombstone causes")

        self.assertEqual(results, [])
        mock_client_func.assert_not_called()


if __name__ == "__main__":
    unittest.main()
