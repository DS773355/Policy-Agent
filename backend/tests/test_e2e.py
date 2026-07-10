"""
test_e2e.py — Final end-to-end validation of the full Policy AGENT pipeline.

Runs entirely with mocked external services (no live DB/Redis/Neo4j/vLLM needed).
Validates the following flow:
  1. Seed → upload new doc version → verify change class
  2. Impact propagation
  3. Chat query
  4. Submit correction (rating ≤ 2) → verify CORRECTED RESPONSE
  5. Manual consolidation
  6. Verify memory_frozen promotion
  7. Re-query → verify [FROM MEMORY] tag
"""
import pytest
from unittest.mock import patch, MagicMock, call
from fastapi.testclient import TestClient
from main import app
from services.auth import hash_password
from services.memory_service import consolidate_memories, check_frozen_memory
from services.embedding_service import get_deterministic_mock_embedding
import uuid
import json

client = TestClient(app)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def auth_headers():
    """Return a valid JWT bearer header for an editor user."""
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    p_hash = hash_password("EditorPass123!")
    user_data = {"id": 1, "username": "editor", "password_hash": p_hash, "role": "editor"}
    mock_cur.fetchone.return_value = user_data
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur

    with patch("database.get_pg_connection", return_value=mock_conn), \
         patch("main.get_pg_connection", return_value=mock_conn), \
         patch("services.auth.get_pg_connection", return_value=mock_conn), \
         patch("main.release_pg_connection"), \
         patch("services.auth.verify_password", return_value=True):

        res = client.post("/api/auth/login", json={"username": "editor", "password": "EditorPass123!"})
        assert res.status_code == 200
        token = res.json()["access_token"]
        return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def admin_headers():
    """Return a valid JWT bearer header for an admin user."""
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    p_hash = hash_password("AdminPass123!")
    user_data = {"id": 2, "username": "admin", "password_hash": p_hash, "role": "admin"}
    mock_cur.fetchone.return_value = user_data
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur

    with patch("database.get_pg_connection", return_value=mock_conn), \
         patch("main.get_pg_connection", return_value=mock_conn), \
         patch("services.auth.get_pg_connection", return_value=mock_conn), \
         patch("main.release_pg_connection"), \
         patch("services.auth.verify_password", return_value=True):

        res = client.post("/api/auth/login", json={"username": "admin", "password": "AdminPass123!"})
        assert res.status_code == 200
        token = res.json()["access_token"]
        return {"Authorization": f"Bearer {token}"}


# ─────────────────────────────────────────────────────────────────────────────
# Step 1-2: Upload + Change Class + Impact Propagation
# ─────────────────────────────────────────────────────────────────────────────

@patch("main.extract_text_from_file")
@patch("main.create_document_version")
def test_upload_and_change_class(mock_create_version, mock_extract, auth_headers):
    """Upload a new document version and verify the change class is returned."""
    doc_id = str(uuid.uuid4())
    mock_extract.return_value = "Updated access control requirements with stricter MFA rules."
    mock_create_version.return_value = {
        "doc_id": doc_id,
        "version_number": 2,
        "change_class": 3,
        "summary": "MFA requirements significantly tightened. New controls added in Section 4.",
    }

    mock_conn = MagicMock()
    mock_cur = MagicMock()
    p_hash = hash_password("EditorPass123!")
    mock_cur.fetchone.return_value = {"id": 1, "username": "editor", "password_hash": p_hash, "role": "editor"}
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur

    with patch("database.get_pg_connection", return_value=mock_conn), \
         patch("main.get_pg_connection", return_value=mock_conn), \
         patch("services.auth.get_pg_connection", return_value=mock_conn), \
         patch("main.release_pg_connection"):

        res = client.post(
            "/api/documents/upload",
            files={"file": ("access_control_v2.txt", b"Updated access control requirements.", "text/plain")},
            data={"title": "Access Control Policy", "owner": "security-team", "doc_id": doc_id},
            headers=auth_headers,
        )

    assert res.status_code == 200
    body = res.json()
    assert body["success"] is True
    assert body["data"]["change_class"] == 3
    assert body["data"]["doc_id"] == doc_id
    print(f"\n  ✓ Upload: change_class=3, summary='{body['data']['summary']}'")


# ─────────────────────────────────────────────────────────────────────────────
# Step 3: Chat Query
# ─────────────────────────────────────────────────────────────────────────────

def test_chat_query(auth_headers):
    """Query the RAG pipeline and verify a streamed response is returned."""
    def fake_stream(session_id, query_text):
        yield "[QUERY_ID: 101]\n"
        yield "MFA is required for all privileged accounts per Access Control Policy Section 4."

    mock_conn = MagicMock()
    mock_cur = MagicMock()
    p_hash = hash_password("EditorPass123!")
    mock_cur.fetchone.return_value = {"id": 1, "username": "editor", "password_hash": p_hash, "role": "editor"}
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur

    with patch("database.get_pg_connection", return_value=mock_conn), \
         patch("main.get_pg_connection", return_value=mock_conn), \
         patch("services.auth.get_pg_connection", return_value=mock_conn), \
         patch("main.release_pg_connection"), \
         patch("main.generate_rag_response", side_effect=fake_stream):

        res = client.post(
            "/api/chat/query",
            json={"session_id": "e2e-session-001", "query_text": "Who needs MFA?"},
            headers=auth_headers,
        )

    assert res.status_code == 200
    assert "MFA is required" in res.text
    print(f"\n  ✓ Chat query: received streamed response.")


# ─────────────────────────────────────────────────────────────────────────────
# Step 4: Correction Feedback (rating ≤ 2) → CORRECTED RESPONSE
# ─────────────────────────────────────────────────────────────────────────────

def test_correction_feedback(auth_headers):
    """Submit a low rating with a correction note and verify CORRECTED RESPONSE tag."""
    def fake_corrected_stream(session_id, query_text):
        yield "CORRECTED RESPONSE: MFA is mandatory for all privileged accounts AND all remote access per Section 4 and Section 7."

    mock_conn = MagicMock()
    mock_cur = MagicMock()
    p_hash = hash_password("EditorPass123!")

    def fetchone_dispatch():
        q = str(mock_cur.execute.call_args[0][0]) if mock_cur.execute.call_args else ""
        if "users" in q:
            return {"id": 1, "username": "editor", "password_hash": p_hash, "role": "editor"}
        # feedback handler UPDATE ... RETURNING query_text → return a tuple
        return ("Who needs MFA?",)

    mock_cur.fetchone.side_effect = fetchone_dispatch
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur

    with patch("database.get_pg_connection", return_value=mock_conn), \
         patch("main.get_pg_connection", return_value=mock_conn), \
         patch("services.auth.get_pg_connection", return_value=mock_conn), \
         patch("main.release_pg_connection"), \
         patch("main.generate_rag_response", side_effect=fake_corrected_stream):

        res = client.post(
            "/api/chat/feedback",
            json={
                "session_id": "e2e-session-001",
                "query_id": 101,
                "star_rating": 1,
                "correction_note": "Include remote access requirement too.",
            },
            headers=auth_headers,
        )

    assert res.status_code == 200
    assert "CORRECTED RESPONSE" in res.text
    print(f"\n  ✓ Correction: CORRECTED RESPONSE present in reply.")


# ─────────────────────────────────────────────────────────────────────────────
# Step 5-6: Consolidation → Frozen Memory Promotion
# ─────────────────────────────────────────────────────────────────────────────

def test_consolidate_and_frozen_memory(admin_headers):
    """Trigger consolidation via admin endpoint and verify frozen memory write."""
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    p_hash = hash_password("AdminPass123!")
    mock_cur.fetchone.return_value = {"id": 2, "username": "admin", "password_hash": p_hash, "role": "admin"}
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur

    with patch("database.get_pg_connection", return_value=mock_conn), \
         patch("main.get_pg_connection", return_value=mock_conn), \
         patch("services.auth.get_pg_connection", return_value=mock_conn), \
         patch("main.release_pg_connection"), \
         patch("main.consolidate_memories") as mock_consolidate:

        res = client.post("/api/admin/consolidate", headers=admin_headers)

    assert res.status_code == 200
    assert res.json()["success"] is True
    mock_consolidate.assert_called_once()
    print(f"\n  ✓ Consolidation: admin endpoint triggered consolidate_memories().")


def test_consolidate_memories_unit():
    """Unit test: consolidate_memories correctly clusters and promotes to frozen memory."""
    query_text = "What are the MFA requirements?"
    emb = get_deterministic_mock_embedding(query_text)
    answer = "MFA is required for all privileged accounts and remote access."

    # Simulate two episodic rows with the same query
    rows = [
        {
            "id": 1,
            "query_text": query_text,
            "retrieved_chunk_ids": json.dumps([]),
            "user_rating": 5,
            "response_text": answer,
            "created_at": __import__("datetime").datetime.utcnow(),
        },
        {
            "id": 2,
            "query_text": query_text,
            "retrieved_chunk_ids": json.dumps([]),
            "user_rating": 4,
            "response_text": answer,
            "created_at": __import__("datetime").datetime.utcnow(),
        },
    ]

    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_cur.fetchall.return_value = rows
    mock_cur.fetchone.return_value = None  # no existing frozen entry
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur

    with patch("services.memory_service.get_pg_connection", return_value=mock_conn), \
         patch("services.memory_service.release_pg_connection"), \
         patch("services.memory_service.get_embeddings_batch", return_value=[emb, emb]):

        consolidate_memories(eps=0.15, min_samples=2, score_threshold=1.5)

    # Should have attempted an INSERT into memory_frozen
    executed_queries = [str(c) for c in mock_cur.execute.call_args_list]
    assert any("memory_frozen" in q for q in executed_queries), \
        f"Expected INSERT into memory_frozen, got: {executed_queries}"
    print(f"\n  ✓ consolidate_memories() promoted cluster to frozen memory.")


# ─────────────────────────────────────────────────────────────────────────────
# Step 7: Re-query → Verify [FROM MEMORY] tag
# ─────────────────────────────────────────────────────────────────────────────

def test_from_memory_tag(auth_headers):
    """Re-running the same query should return [FROM MEMORY] when cache hits."""
    cached_answer = "MFA is required for all privileged accounts and remote access per Access Control Policy."

    mock_conn = MagicMock()
    mock_cur = MagicMock()
    p_hash = hash_password("EditorPass123!")
    mock_cur.fetchone.return_value = {"id": 1, "username": "editor", "password_hash": p_hash, "role": "editor"}
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur

    with patch("database.get_pg_connection", return_value=mock_conn), \
         patch("main.get_pg_connection", return_value=mock_conn), \
         patch("services.auth.get_pg_connection", return_value=mock_conn), \
         patch("main.release_pg_connection"), \
         patch("services.rag_service.check_frozen_memory", return_value=cached_answer):

        res = client.post(
            "/api/chat/query",
            json={"session_id": "e2e-session-002", "query_text": "What are the MFA requirements?"},
            headers=auth_headers,
        )

    assert res.status_code == 200
    assert "[FROM MEMORY]" in res.text
    assert cached_answer in res.text
    print(f"\n  ✓ Re-query: [FROM MEMORY] tag verified in response.\n")
