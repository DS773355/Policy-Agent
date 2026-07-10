import pytest
from unittest.mock import patch, MagicMock
from services.classifier import (
    compute_doc_embedding,
    classify_change_severity_heuristic,
    classify_change_severity
)

def test_compute_doc_embedding_empty():
    assert compute_doc_embedding([]) == [0.0] * 1536


def test_compute_doc_embedding_average():
    # Define simple mock embeddings of 3 dimensions for easy debugging (normally 1536)
    # But compute_doc_embedding uses np.mean and norm, so let's check with 1536
    chunks = [
        {"embedding": [1.0] + [0.0] * 1535},
        {"embedding": [0.0] + [1.0] + [0.0] * 1534}
    ]
    
    avg_emb = compute_doc_embedding(chunks)
    
    # Average of [1, 0, ...] and [0, 1, ...] is [0.5, 0.5, ...]
    # Normalized is [1/sqrt(2), 1/sqrt(2), 0, ...] = [0.7071, 0.7071, 0, ...]
    assert len(avg_emb) == 1536
    pytest.approx(avg_emb[0], 0.7071)
    pytest.approx(avg_emb[1], 0.7071)
    assert avg_emb[2] == 0.0


def test_classify_change_severity_heuristic_identical():
    text = "# Policy Title\nThis is the content."
    chunks = [{"embedding": [1.0] + [0.0] * 1535}]
    
    change_class, summary = classify_change_severity_heuristic(
        text, text, chunks, chunks
    )
    
    assert change_class == 0
    assert "identical" in summary.lower()


def test_classify_change_severity_heuristic_minor():
    old_text = "# Policy Title\nThis is the content."
    new_text = "# Policy Title\nThis is the modified content."
    
    old_chunks = [{"embedding": [1.0] + [0.0] * 1535}]
    # High cosine similarity (small distance)
    # Cosine distance = 1 - 0.99 = 0.01
    new_chunks = [{"embedding": [0.99] + [0.141] + [0.0] * 1534}]
    
    change_class, summary = classify_change_severity_heuristic(
        old_text, new_text, old_chunks, new_chunks
    )
    
    # Cosine distance ~ 0.01 -> Class 1 or 0 depending on structure
    # With 0 heading changes, it is Class 1 (Minor updates)
    assert change_class == 1
    assert "minor" in summary.lower() or "trivial" in summary.lower()


def test_classify_change_severity_heuristic_critical():
    old_text = "# Policy Title\nThis is the old content."
    new_text = "# Completely New Title\nEntirely rewritten text from scratch."
    
    old_chunks = [{"embedding": [1.0] + [0.0] * 1535}]
    # Very different vector (cosine similarity ~ 0.5, distance ~ 0.5)
    new_chunks = [{"embedding": [0.5] + [0.866] + [0.0] * 1534}]
    
    change_class, summary = classify_change_severity_heuristic(
        old_text, new_text, old_chunks, new_chunks
    )
    
    # Distance is 0.5 >= 0.2 -> Class 4
    assert change_class == 4
    assert "critical" in summary.lower() or "revamp" in summary.lower()


def test_classify_change_severity_llm_fallback():
    # Test that classify_change_severity falls back to heuristic if requests fails
    old_text = "# Title\nOld"
    new_text = "# Title\nOld"
    old_chunks = [{"embedding": [1.0] + [0.0] * 1535}]
    new_chunks = [{"embedding": [1.0] + [0.0] * 1535}]
    
    with patch('requests.post', side_effect=Exception("Connection error")):
        change_class, summary = classify_change_severity(
            old_text, new_text, old_chunks, new_chunks
        )
        # Should execute heuristic
        assert change_class == 0
        assert "identical" in summary.lower()
