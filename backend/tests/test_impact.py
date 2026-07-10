"""
test_impact.py — Tests for the offline BFS-based impact_service.

All Neo4j references removed. The service now uses:
  - PostgreSQL doc_graph table (adjacency list BFS)
  - Python numpy cosine similarity
  - Redis for workspace management
"""
import pytest
from unittest.mock import patch, MagicMock, call
import json

from services.impact_service import (
    extract_citations,
    run_citation_extraction,
    classify_overlap_class,
    run_semantic_proximity_check,
    evaluate_impact,
    bfs_graph,
)


# ─── Citation Extraction ──────────────────────────────────────────────────────

def test_extract_citations_llm():
    """extract_citations calls vLLM and parses a JSON list of titles."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": '["Information Security Policy", "Access Control Guidelines"]'
                }
            }
        ]
    }

    with patch("requests.post", return_value=mock_response):
        citations = extract_citations(
            "Refer to Information Security Policy and Access Control Guidelines."
        )
    assert citations == ["Information Security Policy", "Access Control Guidelines"]


def test_extract_citations_fallback():
    """extract_citations returns [] when vLLM is unreachable."""
    with patch("requests.post", side_effect=Exception("Connection refused")):
        citations = extract_citations("Some policy text with no server.")
    assert citations == []


# ─── run_citation_extraction ──────────────────────────────────────────────────

def test_run_citation_extraction_inserts_edge():
    """
    run_citation_extraction should look up cited document in PG and
    INSERT a CITES edge into doc_graph.
    """
    chunks = [{"chunk_id": "c1", "content": "Refer to Policy X"}]

    mock_conn = MagicMock()
    mock_cur = MagicMock()
    # pg lookup returns a target doc
    mock_cur.fetchone.return_value = {"id": "target-uuid"}
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur

    with patch("services.impact_service.get_pg_connection", return_value=mock_conn), \
         patch("services.impact_service.release_pg_connection") as mock_release, \
         patch("services.impact_service.extract_citations", return_value=["Policy X"]):

        run_citation_extraction("source-uuid", 2, chunks)

    # Should have queried documents table (SELECT) and inserted edge (INSERT)
    assert mock_cur.execute.call_count == 2
    mock_cur.execute.assert_any_call(
        "SELECT id FROM documents WHERE title ILIKE %s LIMIT 1",
        ("%Policy X%",)
    )
    # Verify INSERT into doc_graph was attempted
    insert_call_args = [str(c) for c in mock_cur.execute.call_args_list]
    assert any("doc_graph" in a for a in insert_call_args)
    mock_release.assert_called_once_with(mock_conn)


def test_run_citation_extraction_no_match():
    """When the cited document is not found in PG, no edge is inserted."""
    chunks = [{"chunk_id": "c1", "content": "Refer to Unknown Policy"}]

    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_cur.fetchone.return_value = None  # not found
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur

    with patch("services.impact_service.get_pg_connection", return_value=mock_conn), \
         patch("services.impact_service.release_pg_connection"), \
         patch("services.impact_service.extract_citations", return_value=["Unknown Policy"]):

        run_citation_extraction("source-uuid", 1, chunks)

    # Still called execute for the SELECT, but no INSERT into doc_graph
    mock_cur.execute.assert_called_once()
    assert "SELECT id FROM documents" in mock_cur.execute.call_args[0][0]


# ─── classify_overlap_class ───────────────────────────────────────────────────

def test_classify_overlap_class_heuristics():
    """classify_overlap_class uses similarity thresholds (no LLM)."""
    assert classify_overlap_class("Text A", "Text B", 0.99) == "DUPLICATE"
    assert classify_overlap_class("Text A", "Text B", 0.95) == "SUPERSEDED"
    assert classify_overlap_class("Text A", "Text B", 0.90) == "PARTIAL_OVERLAP"


# ─── run_semantic_proximity_check ─────────────────────────────────────────────

def test_run_semantic_proximity_check_high_similarity():
    """
    When a candidate chunk from another doc has high cosine similarity,
    an overlap record and OVERLAPS_WITH edge should be inserted.
    """
    chunks = [
        {
            "chunk_id": "new-chunk-1",
            "content": "New chunk content.",
            "embedding": [0.1] * 1536,
        }
    ]

    mock_conn = MagicMock()
    mock_cur = MagicMock()
    # Candidate chunk from another doc — very close embedding
    other_emb = [0.1] * 1536  # similarity = 1.0
    mock_cur.fetchall.return_value = [
        {
            "chunk_id": "other-chunk-1",
            "content": "Existing similar content.",
            "other_doc_id": "other-doc-123",
            "other_version": 1,
            "embedding": other_emb,
        }
    ]
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur

    with patch("services.impact_service.get_pg_connection", return_value=mock_conn), \
         patch("services.impact_service.release_pg_connection"), \
         patch("services.impact_service.classify_overlap_class", return_value="DUPLICATE"):

        run_semantic_proximity_check("doc-uuid", 2, chunks)

    # Should have called fetchall for candidate chunks
    mock_cur.fetchall.assert_called_once()


def test_run_semantic_proximity_check_low_similarity():
    """
    When similarity is below the threshold, no overlap record is written.
    """
    chunks = [
        {
            "chunk_id": "new-chunk-1",
            "content": "New chunk content.",
            "embedding": [1.0] + [0.0] * 1535,
        }
    ]

    mock_conn = MagicMock()
    mock_cur = MagicMock()
    # Candidate chunk is completely orthogonal — similarity = 0.0
    other_emb = [0.0, 1.0] + [0.0] * 1534
    mock_cur.fetchall.return_value = [
        {
            "chunk_id": "other-chunk-2",
            "content": "Totally different content.",
            "other_doc_id": "other-doc-999",
            "other_version": 1,
            "embedding": other_emb,
        }
    ]
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur

    with patch("services.impact_service.get_pg_connection", return_value=mock_conn), \
         patch("services.impact_service.release_pg_connection"):

        run_semantic_proximity_check("doc-uuid", 1, chunks)

    # fetchall called, but no INSERT (execute called only once for the SELECT)
    assert mock_cur.execute.call_count == 1


# ─── bfs_graph ────────────────────────────────────────────────────────────────

def test_bfs_graph_single_hop():
    """bfs_graph returns direct neighbours at hop 1."""
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    # Direct neighbour of start_doc
    mock_cur.fetchall.return_value = [{"neighbor": "doc-b", "similarity": 0.9}]
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur

    with patch("services.impact_service.get_pg_connection", return_value=mock_conn), \
         patch("services.impact_service.release_pg_connection"):

        results = bfs_graph("doc-a", max_hops=1)

    assert len(results) == 1
    assert results[0]["doc_id"] == "doc-b"
    assert results[0]["hop_count"] == 1
    assert results[0]["path_similarity"] == pytest.approx(0.9, abs=1e-6)


def test_bfs_graph_excludes_start():
    """bfs_graph never includes the starting document in the results."""
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    # Edge loops back to start (should be filtered)
    mock_cur.fetchall.return_value = [{"neighbor": "doc-a", "similarity": 1.0}]
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur

    with patch("services.impact_service.get_pg_connection", return_value=mock_conn), \
         patch("services.impact_service.release_pg_connection"):

        results = bfs_graph("doc-a", max_hops=3)

    assert results == []


# ─── evaluate_impact ──────────────────────────────────────────────────────────

def test_evaluate_impact_below_class_2():
    """evaluate_impact returns [] for change_class < 2 without any queries."""
    results = evaluate_impact("doc-a", 1, change_class=1)
    assert results == []


def test_evaluate_impact_scoring():
    """
    evaluate_impact applies hop weights and change multipliers correctly.

    doc-b: hop=1, similarity=0.90, class=3 (mult=1.5)
           score = (1/1) * 0.90 * 1.5 = 1.35  → added_to_workspace=True (> 0.4)

    doc-c: hop=3, similarity=1.0, class=3 (mult=1.5)
           score = (1/3) * 1.0 * 1.5 = 0.50   → added_to_workspace=True (> 0.4)

    doc-d: hop=3, similarity=0.80, class=3 (mult=1.5)
           score = (1/3) * 0.80 * 1.5 = 0.40  → added_to_workspace=False (<= 0.4)
    """
    bfs_results = [
        {"doc_id": "doc-b", "hop_count": 1, "path_similarity": 0.90},
        {"doc_id": "doc-c", "hop_count": 3, "path_similarity": 1.00},
        {"doc_id": "doc-d", "hop_count": 3, "path_similarity": 0.80},
    ]

    mock_conn = MagicMock()
    mock_cur = MagicMock()
    # Return a title for every doc lookup
    mock_cur.fetchone.side_effect = [
        {"title": "Policy B"},
        {"title": "Policy C"},
        {"title": "Policy D"},
    ]
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur

    mock_redis = MagicMock()

    with patch("services.impact_service.bfs_graph", return_value=bfs_results), \
         patch("services.impact_service.get_pg_connection", return_value=mock_conn), \
         patch("services.impact_service.release_pg_connection"), \
         patch("services.impact_service.get_redis_client", return_value=mock_redis):

        results = evaluate_impact("doc-a", 2, change_class=3)

    # Results sorted by impact_score descending
    assert len(results) == 3

    assert results[0]["doc_id"] == "doc-b"
    assert results[0]["impact_score"] == pytest.approx(1.35, abs=1e-3)
    assert results[0]["added_to_workspace"] is True

    assert results[1]["doc_id"] == "doc-c"
    assert results[1]["impact_score"] == pytest.approx(0.50, abs=1e-3)
    assert results[1]["added_to_workspace"] is True

    assert results[2]["doc_id"] == "doc-d"
    assert results[2]["impact_score"] == pytest.approx(0.40, abs=1e-3)
    assert results[2]["added_to_workspace"] is False

    # Redis sadd called for doc-b and doc-c only
    assert mock_redis.sadd.call_count == 2
    mock_redis.sadd.assert_any_call("active_review_workspace", "doc-b")
    mock_redis.sadd.assert_any_call("active_review_workspace", "doc-c")
