import requests
import hashlib
import numpy as np
from config import settings

def get_deterministic_mock_embedding(text: str, dimension: int = 1536) -> list[float]:
    """
    Generates a deterministic pseudo-random normalized vector of the given dimension
    based on the SHA-256 hash of the input text.
    Ensures that identical text always yields the same vector.
    """
    # Create SHA-256 hash of text
    hash_bytes = hashlib.sha256(text.encode('utf-8')).digest()
    
    # Use the hash bytes to seed numpy random
    # We convert the first 4 bytes to an integer seed
    seed = int.from_bytes(hash_bytes[:4], byteorder='big')
    rng = np.random.default_rng(seed)
    
    # Generate random vector
    vector = rng.standard_normal(dimension)
    
    # Normalize vector to unit length (so dot product equals cosine similarity)
    norm = np.linalg.norm(vector)
    if norm > 0:
        vector = vector / norm
        
    return vector.tolist()


def get_embedding(text: str) -> list[float]:
    """
    Fetches the 1536-dimensional embedding vector for the text.
    Calls vLLM API, falling back to deterministic mock embedding if offline.
    """
    if not text:
        return [0.0] * 1536
        
    try:
        # Standard OpenAI-compatible embedding request
        # timeout=1 so offline mode fails fast instead of blocking per-chunk
        response = requests.post(
            f"{settings.vllm_api_url}/embeddings",
            json={
                "input": text,
                "model": "phi3"  # llama.cpp Phi-3 model alias
            },
            timeout=1
        )
        if response.status_code == 200:
            data = response.json()
            embedding = data['data'][0]['embedding']
            
            # Truncate or pad to exactly 1536 if vLLM returns a different dimension
            if len(embedding) > 1536:
                embedding = embedding[:1536]
            elif len(embedding) < 1536:
                embedding = embedding + [0.0] * (1536 - len(embedding))
                
            # Normalize just in case
            norm = np.linalg.norm(embedding)
            if norm > 0:
                embedding = (np.array(embedding) / norm).tolist()
                
            return embedding
        else:
            # Fallback on non-200 response
            return get_deterministic_mock_embedding(text)
    except Exception:
        # Fallback on connection error/timeout — fail fast
        return get_deterministic_mock_embedding(text)


def get_embeddings_batch(texts: list[str]) -> list[list[float]]:
    """
    Fetches embeddings for a list of texts in batch.
    Processes in parallel sub-batches of 32 chunks to maximize llama-server multi-slot performance
    and prevent timeout issues on large documents.
    """
    if not texts:
        return []

    from concurrent.futures import ThreadPoolExecutor
    
    batch_size = 32
    batches = []
    for i in range(0, len(texts), batch_size):
        batches.append((i, texts[i:i+batch_size]))
        
    results_map = {}
    
    def embed_sub_batch(batch_idx, batch_texts):
        try:
            # timeout=15s per batch of 32 is extremely generous and will never time out on GPU
            response = requests.post(
                f"{settings.vllm_api_url}/embeddings",
                json={
                    "input": batch_texts,
                    "model": "phi3"
                },
                timeout=15
            )
            if response.status_code == 200:
                data = response.json()
                embeddings = [item['embedding'] for item in data['data']]
                processed = []
                for emb in embeddings:
                    if len(emb) > 1536:
                        emb = emb[:1536]
                    elif len(emb) < 1536:
                        emb = emb + [0.0] * (1536 - len(emb))
                    norm = np.linalg.norm(emb)
                    if norm > 0:
                        emb = (np.array(emb) / norm).tolist()
                    processed.append(emb)
                return batch_idx, processed
        except Exception as e:
            print(f"[get_embeddings_batch] Batch at offset {batch_idx} failed: {e}")
        
        # Local fallback for this batch only
        fallback_embs = [get_deterministic_mock_embedding(t) for t in batch_texts]
        return batch_idx, fallback_embs

    # Process in parallel using up to 4 threads
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(embed_sub_batch, idx, b_texts) for idx, b_texts in batches]
        for f in futures:
            idx, processed_embs = f.result()
            results_map[idx] = processed_embs

    # Assemble final list in correct order
    final_embeddings = []
    for i in range(0, len(texts), batch_size):
        final_embeddings.extend(results_map.get(i, []))
        
    return final_embeddings


def calculate_cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """Calculates cosine similarity between two vectors."""
    a = np.array(vec_a)
    b = np.array(vec_b)
    dot_product = np.dot(a, b)
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(dot_product / (norm_a * norm_b))
