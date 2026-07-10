import pytest
from unittest.mock import patch
from services.classifier import diff_and_embed_chunks, get_text_hash

def test_diff_and_embed_chunks_all_new():
    new_chunks = [
        {"chunk_index": 0, "content": "This is chunk number one."},
        {"chunk_index": 1, "content": "This is chunk number two."}
    ]
    old_chunks_db = [] # No previous version chunks
    
    mock_embeddings = [
        [0.1] * 1536,
        [0.2] * 1536
    ]
    
    with patch('services.classifier.get_embeddings_batch') as mock_batch:
        mock_batch.return_value = mock_embeddings
        
        result = diff_and_embed_chunks(new_chunks, old_chunks_db)
        
        # Verify get_embeddings_batch was called with both chunk contents
        mock_batch.assert_called_once_with([
            "This is chunk number one.",
            "This is chunk number two."
        ])
        
        assert len(result) == 2
        assert result[0]["embedding"] == [0.1] * 1536
        assert result[1]["embedding"] == [0.2] * 1536


def test_diff_and_embed_chunks_partial_reuse():
    new_chunks = [
        {"chunk_index": 0, "content": "This is chunk number one."}, # Same
        {"chunk_index": 1, "content": "This chunk is modified."}     # New/modified
    ]
    
    # Old chunks database contains the first chunk
    old_chunks_db = [
        {"content": "This is chunk number one.", "embedding": [0.9] * 1536}
    ]
    
    mock_embeddings = [
        [0.5] * 1536
    ]
    
    with patch('services.classifier.get_embeddings_batch') as mock_batch:
        mock_batch.return_value = mock_embeddings
        
        result = diff_and_embed_chunks(new_chunks, old_chunks_db)
        
        # Verify get_embeddings_batch was only called with the modified chunk content
        mock_batch.assert_called_once_with([
            "This chunk is modified."
        ])
        
        assert len(result) == 2
        # Check first chunk reused old embedding
        assert result[0]["embedding"] == [0.9] * 1536
        # Check second chunk got new embedding
        assert result[1]["embedding"] == [0.5] * 1536


def test_diff_and_embed_chunks_all_reuse():
    new_chunks = [
        {"chunk_index": 0, "content": "This is chunk number one."},
        {"chunk_index": 1, "content": "This is chunk number two."}
    ]
    
    # Old chunks database contains both
    old_chunks_db = [
        {"content": "This is chunk number one.", "embedding": [0.8] * 1536},
        {"content": "This is chunk number two.", "embedding": [0.7] * 1536}
    ]
    
    with patch('services.classifier.get_embeddings_batch') as mock_batch:
        result = diff_and_embed_chunks(new_chunks, old_chunks_db)
        
        # Verify get_embeddings_batch was never called
        mock_batch.assert_not_called()
        
        assert len(result) == 2
        assert result[0]["embedding"] == [0.8] * 1536
        assert result[1]["embedding"] == [0.7] * 1536
