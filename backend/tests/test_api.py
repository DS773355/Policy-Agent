import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from main import app

client = TestClient(app)

def test_read_root():
    response = client.get("/")
    assert response.status_code == 200
    assert response.json() == {"status": "online", "message": "Policy Agent API is running."}


def test_get_versions_invalid_uuid():
    response = client.get("/api/documents/not-a-uuid/versions")
    assert response.status_code == 400
    assert "Invalid doc_id UUID format" in response.json()["detail"]


@patch("main.get_document_version_history")
def test_get_versions_success(mock_history):
    mock_history.return_value = [
        {"version_id": 1, "version_number": 1, "uploaded_at": "2026-06-25T15:00:00Z"}
    ]
    doc_id = "00000000-0000-0000-0000-000000000000"
    response = client.get(f"/api/documents/{doc_id}/versions")
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["versions"][0]["version_number"] == 1


def test_get_impact_invalid_uuid():
    response = client.get("/api/documents/not-a-uuid/impact")
    assert response.status_code == 400
    assert "Invalid doc_id UUID format" in response.json()["detail"]


@patch("main.get_latest_version")
@patch("main.get_impact_graph")
def test_get_impact_success(mock_impact, mock_latest):
    mock_latest.return_value = {"version_number": 2}
    mock_impact.return_value = {
        "nodes": [{"id": "doc-a_v2", "title": "Doc A"}],
        "edges": []
    }
    doc_id = "00000000-0000-0000-0000-000000000000"
    response = client.get(f"/api/documents/{doc_id}/impact")
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["graph"]["nodes"][0]["title"] == "Doc A"


@patch("main.get_all_overlaps_grouped")
def test_get_overlaps(mock_overlaps):
    mock_overlaps.return_value = {
        "DUPLICATE": [{"id": 1, "doc_id_a": "doc-1", "doc_id_b": "doc-2"}],
        "PARTIAL_OVERLAP": [],
        "CONFLICT": [],
        "SUPERSEDED": []
    }
    response = client.get("/api/documents/overlaps")
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert len(response.json()["overlaps"]["DUPLICATE"]) == 1
