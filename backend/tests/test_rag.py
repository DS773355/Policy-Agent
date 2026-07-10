import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from main import app
from services.rag_service import (
    rrf_blend,
    local_rerank,
    context_tagger
)

client = TestClient(app)

def test_rrf_blend():
    semantic_results = [
        {"chunk_id": "c1", "content": "one"},
        {"chunk_id": "c2", "content": "two"},
        {"chunk_id": "c3", "content": "three"}
    ]
    keyword_results = [
        {"chunk_id": "c2", "content": "two"},
        {"chunk_id": "c1", "content": "one"},
        {"chunk_id": "c4", "content": "four"}
    ]
    
    blended = rrf_blend(semantic_results, keyword_results, k=60, limit=3)
    
    assert len(blended) == 3
    # c1 and c2 should have higher score than c3 and c4 because they appear in both
    # c1 score: 1/(60+1) + 1/(60+2) = 0.01639 + 0.01613 = 0.03252
    # c2 score: 1/(60+2) + 1/(60+1) = 0.03252
    # c3 score: 1/(60+3) = 0.01587
    # c4 score: 1/(60+3) = 0.01587
    assert blended[0]["chunk_id"] in ["c1", "c2"]
    assert blended[1]["chunk_id"] in ["c1", "c2"]
    assert blended[2]["chunk_id"] in ["c3", "c4"]
    assert blended[0]["rrf_score"] == pytest.approx(0.03252, abs=1e-4)


def test_local_rerank_fallback():
    chunks = [
        {"chunk_id": "c1", "content": "one"},
        {"chunk_id": "c2", "content": "two"}
    ]
    
    # Mocking a post failure to force fallback
    with patch('requests.post', side_effect=Exception("Timeout")):
        reranked = local_rerank("query", chunks, limit=1)
        assert len(reranked) == 1
        assert reranked[0]["chunk_id"] == "c1"


def test_context_tagger_rules():
    chunks = [
        {"chunk_id": "c-changed", "doc_id": "doc-changed", "content": "changed content"},
        {"chunk_id": "c-affected", "doc_id": "doc-affected", "content": "affected content"},
        {"chunk_id": "c-overlap", "doc_id": "doc-overlap", "content": "overlapping content"},
        {"chunk_id": "c-source", "doc_id": "doc-source", "content": "source content"}
    ]
    
    # Redis mock returns doc-affected as in the active workspace
    mock_redis = MagicMock()
    mock_redis.smembers.return_value = {b"doc-affected"}
    
    # Postgres mock returns:
    # doc-changed has change events (has_changes = True)
    # doc-overlap has active overlaps (has_overlap = True)
    # other documents do not
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    
    # We mock fetchone returns
    def fetchone_side_effect():
        # Context tagger executes two queries per chunk:
        # 1. Overlap query: SELECT 1 FROM overlap_records WHERE matched_chunk_ids @> ...
        # 2. Changed query: SELECT 1 FROM change_events WHERE doc_id = ...
        # We track state to mock correctly.
        query_text = mock_cur.execute.call_args[0][0]
        args = mock_cur.execute.call_args[0][1]
        
        if "overlap_records" in query_text:
            if "c-overlap" in args[0]:
                return (1,)
            return None
        elif "change_events" in query_text:
            if "doc-changed" in args[0]:
                return (1,)
            return None
        return None
        
    mock_cur.fetchone.side_effect = fetchone_side_effect
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur
    
    with patch('services.rag_service.get_redis_client', return_value=mock_redis), \
         patch('services.rag_service.get_pg_connection', return_value=mock_conn), \
         patch('services.rag_service.release_pg_connection'):
         
        tagged = context_tagger(chunks)
        
        assert tagged[0]["tag"] == "[Changed Content]"
        assert tagged[1]["tag"] == "[Affected Content]"
        assert tagged[2]["tag"] == "[Overlapping Content]"
        assert tagged[3]["tag"] == "[Source Content]"


@patch("main.generate_rag_response")
def test_api_chat_query(mock_generator):
    def dummy_generator(session_id, query_text):
        yield "Hello "
        yield "world!"
        
    mock_generator.side_effect = dummy_generator
    
    payload = {
        "session_id": "session-123",
        "query_text": "Is there a code of conduct policy?"
    }
    
    response = client.post("/api/chat/query", json=payload)
    assert response.status_code == 200
    assert response.text == "Hello world!"
