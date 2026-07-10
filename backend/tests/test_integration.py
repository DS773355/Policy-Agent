import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from main import app
from services.auth import hash_password
import uuid

client = TestClient(app)

@patch("main.extract_text_from_file")
@patch("main.create_document_version")
def test_integration_flow(mock_create_version, mock_extract):
    """
    End-to-end integration test simulating the entire workflow.
    """
    
    # Setup unified mock connection and cursor
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    
    p_hash = hash_password("editor_pass")
    
    # Simulated user database state
    simulated_user = {
        "id": 1,
        "username": "editor_user",
        "password_hash": p_hash,
        "role": "editor"
    }
    
    def fetchone_side_effect():
        if not mock_cur.execute.call_args:
            return None
        query_str = mock_cur.execute.call_args[0][0]
        
        # Match user retrieval queries
        if "users" in query_str:
            if "password_hash" in query_str:
                return simulated_user
            elif "role" in query_str:
                return {
                    "id": simulated_user["id"],
                    "username": simulated_user["username"],
                    "role": simulated_user["role"]
                }
            else:
                return (simulated_user["id"],)
                
        # Match feedback query
        if "memory_episodic" in query_str and "RETURNING" in query_str:
            return ("What is the security policy?",)
            
        return None
        
    mock_cur.fetchone.side_effect = fetchone_side_effect
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur
    
    # Wrap entire integration flow under one global connection patch context
    with patch("database.get_pg_connection", return_value=mock_conn), \
         patch("main.get_pg_connection", return_value=mock_conn), \
         patch("services.auth.get_pg_connection", return_value=mock_conn), \
         patch("main.release_pg_connection"), \
         patch("services.auth.verify_password", return_value=True):
         
        # 1. Authenticate (Login)
        login_res = client.post("/api/auth/login", json={
            "username": "editor_user",
            "password": "editor_pass"
        })
        assert login_res.status_code == 200
        auth_data = login_res.json()
        assert "access_token" in auth_data
        token = auth_data["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # 2. Upload Document
        mock_extract.return_value = "Core compliance security policy text."
        doc_id = str(uuid.uuid4())
        mock_create_version.return_value = {
            "doc_id": doc_id,
            "version_number": 1,
            "change_class": 0,
            "summary": "Initial ingestion"
        }
        
        files = {"file": ("policy.txt", b"Core compliance security policy text.", "text/plain")}
        data = {"title": "Security Policy", "owner": "sec-team"}
        upload_res = client.post(
            "/api/documents/upload",
            files=files,
            data=data,
            headers=headers
        )
        assert upload_res.status_code == 200
        assert upload_res.json()["success"] is True
        assert upload_res.json()["data"]["doc_id"] == doc_id

        # 3. Retrieve Documents List & Suggestions
        mock_cur.fetchall.return_value = [
            {"id": doc_id, "title": "Security Policy", "owner": "sec-team", "created_at": "2026-06-25T15:00:00Z", "latest_version": 1, "last_change_class": 0}
        ]
        
        docs_res = client.get("/api/documents", headers=headers)
        assert docs_res.status_code == 200
        assert docs_res.json()["success"] is True
        assert len(docs_res.json()["documents"]) == 1
        
        sugg_res = client.get("/api/workspace/suggestions", headers=headers)
        assert sugg_res.status_code == 200
        # Should return suggestions list
        assert "suggestions" in sugg_res.json()

        # 4. Chat Query (Viewer / Editor Role)
        def mock_stream(session_id, query_text):
            yield "Query answer tokens."
            
        with patch("main.generate_rag_response", side_effect=mock_stream):
            chat_payload = {
                "session_id": "session-xyz",
                "query_text": "What is the security policy?"
            }
            chat_res = client.post("/api/chat/query", json=chat_payload, headers=headers)
            assert chat_res.status_code == 200
            assert chat_res.text == "Query answer tokens."

        # 5. Feedback Corrected Re-Run
        def mock_corrected_stream(session_id, query_text):
            yield "Corrected response answer."
            
        with patch("main.generate_rag_response", side_effect=mock_corrected_stream):
            feedback_payload = {
                "session_id": "session-xyz",
                "query_id": 42,
                "star_rating": 1,
                "correction_note": "Please use version 1"
            }
            feedback_res = client.post("/api/chat/feedback", json=feedback_payload, headers=headers)
            assert feedback_res.status_code == 200
            assert "Corrected response answer." in feedback_res.text

        # 6. Trigger Admin Consolidation (Expect 403 Forbidden for editor)
        admin_res = client.post("/api/admin/consolidate", headers=headers)
        assert admin_res.status_code == 403
            
        # Simulate admin login by updating simulated user state
        admin_p_hash = hash_password("admin_pass")
        simulated_user.clear()
        simulated_user.update({
            "id": 2,
            "username": "admin_user",
            "password_hash": admin_p_hash,
            "role": "admin"
        })
        
        admin_login_res = client.post("/api/auth/login", json={
            "username": "admin_user",
            "password": "admin_pass"
        })
        admin_token = admin_login_res.json()["access_token"]
        admin_headers = {"Authorization": f"Bearer {admin_token}"}
            
        # Trigger consolidation with admin token
        with patch("main.consolidate_memories") as mock_consolidate:
            admin_res = client.post("/api/admin/consolidate", headers=admin_headers)
            assert admin_res.status_code == 200
            assert admin_res.json()["success"] is True
            mock_consolidate.assert_called_once()
