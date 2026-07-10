import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
import datetime
import json
from main import app
from services.memory_service import (
    dbscan_custom,
    calculate_recency_decay,
    check_frozen_memory,
    consolidate_memories
)

client = TestClient(app)

def test_dbscan_custom_clustering():
    # 4 vectors of 1536 dim (normalized)
    # v1 and v2 are very close (dot product 0.98, distance 0.02)
    # v3 and v4 are very close (dot product 0.99, distance 0.01)
    # distance between v1 and v3 is 0.5
    v1 = [1.0] + [0.0]*1535
    v2 = [0.98, 0.198997] + [0.0]*1534 # normalized
    v3 = [0.0, 1.0] + [0.0]*1534
    v4 = [0.0, 0.99, 0.141] + [0.0]*1533 # normalized
    
    embeddings = [v1, v2, v3, v4]
    
    # Run DBSCAN with eps = 0.1 (similarity > 0.90) and min_samples = 2
    labels = dbscan_custom(embeddings, eps=0.1, min_samples=2)
    
    assert len(labels) == 4
    # v1 and v2 should share cluster 0
    assert labels[0] == labels[1]
    # v3 and v4 should share cluster 1
    assert labels[2] == labels[3]
    assert labels[0] != labels[2]


def test_calculate_recency_decay():
    now = datetime.datetime.now(datetime.timezone.utc)
    dates_recent = [now, now - datetime.timedelta(hours=2)]
    dates_old = [now - datetime.timedelta(days=20), now - datetime.timedelta(days=30)]
    
    decay_recent = calculate_recency_decay(dates_recent)
    decay_old = calculate_recency_decay(dates_old)
    
    assert decay_recent > 0.95
    assert decay_old < 0.60
    assert decay_recent > decay_old


@patch("services.memory_service.get_embeddings_batch")
def test_check_frozen_memory_matches(mock_embed):
    mock_embed.return_value = [[0.1] * 1536]
    
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    # Mocking frozen memory hit
    mock_cur.fetchone.return_value = {
        "answer_text": "Frozen Answer",
        "similarity": 0.98
    }
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur
    
    with patch("services.memory_service.get_pg_connection", return_value=mock_conn), \
         patch("services.memory_service.release_pg_connection"):
         
        ans = check_frozen_memory("frequent query")
        assert ans == "Frozen Answer"


@patch("services.memory_service.get_embeddings_batch")
def test_check_frozen_memory_miss(mock_embed):
    mock_embed.return_value = [[0.1] * 1536]
    
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    # Mocking similarity below threshold
    mock_cur.fetchone.return_value = {
        "answer_text": "Frozen Answer",
        "similarity": 0.92
    }
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur
    
    with patch("services.memory_service.get_pg_connection", return_value=mock_conn), \
         patch("services.memory_service.release_pg_connection"):
         
        ans = check_frozen_memory("infrequent query")
        assert ans is None


@patch("services.memory_service.get_pg_connection")
@patch("services.memory_service.get_embeddings_batch")
def test_consolidate_memories_promotes(mock_embed, mock_get_conn):
    # Setup mock data for 2 query episodes in episodic memory
    now = datetime.datetime.now(datetime.timezone.utc)
    mock_rows = [
        {
            "id": 1,
            "query_text": "query A",
            "retrieved_chunk_ids": '["c1"]',
            "user_rating": 5,
            "response_text": "answer A",
            "created_at": now
        },
        {
            "id": 2,
            "query_text": "query B",
            "retrieved_chunk_ids": '["c2"]',
            "user_rating": 4,
            "response_text": "answer B",
            "created_at": now
        }
    ]
    
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_cur.fetchall.return_value = mock_rows
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur
    mock_get_conn.return_value = mock_conn
    
    # vA and vB are extremely close to cluster together
    vA = [1.0] + [0.0]*1535
    vB = [0.99] + [0.01]*1535
    mock_embed.return_value = [vA, vB]
    
    with patch("services.memory_service.release_pg_connection"):
        consolidate_memories(eps=0.1, min_samples=2, score_threshold=1.5)
        
        # Verify it ran a SELECT to check if canonical already exists, and then did an INSERT or UPDATE
        assert mock_cur.execute.call_count >= 3
        # Ensure we committed
        mock_conn.commit.assert_called()


def test_api_feedback_no_rerun():
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    # Returns original query
    mock_cur.fetchone.return_value = ("Original Query",)
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur
    
    with patch("main.get_pg_connection", return_value=mock_conn), \
         patch("main.release_pg_connection"):
         
        payload = {
            "session_id": "session-123",
            "query_id": 99,
            "star_rating": 5,
            "correction_note": None
        }
        response = client.post("/api/chat/feedback", json=payload)
        assert response.status_code == 200
        assert response.json()["success"] is True
        assert "Feedback submitted successfully" in response.json()["message"]


@patch("main.generate_rag_response")
def test_api_feedback_with_rerun(mock_gen):
    def dummy_generator(session_id, query_text):
        yield "[QUERY_ID: 101]\n"
        yield "Corrected response tokens."
        
    mock_gen.side_effect = dummy_generator
    
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    # Returns original query
    mock_cur.fetchone.return_value = ("Original Query",)
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur
    
    with patch("main.get_pg_connection", return_value=mock_conn), \
         patch("main.release_pg_connection"):
         
        payload = {
            "session_id": "session-123",
            "query_id": 99,
            "star_rating": 2,
            "correction_note": "refer to Policy X"
        }
        response = client.post("/api/chat/feedback", json=payload)
        assert response.status_code == 200
        # The query_id header [QUERY_ID: 101]\n should be filtered out
        # and it should start with [CORRECTED RESPONSE]\n
        assert response.text == "[CORRECTED RESPONSE]\nCorrected response tokens."
        mock_gen.assert_called_once_with("session-123", "Original Query [Correction: refer to Policy X]")
