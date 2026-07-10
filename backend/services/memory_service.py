import json
import math
import datetime
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row

from database import (
    get_pg_connection,
    release_pg_connection,
    get_redis_client
)
from services.embedding_service import get_embeddings_batch, calculate_cosine_similarity
from config import settings

def dbscan_custom(embeddings: list[list[float]], eps: float, min_samples: int) -> list[int]:
    """
    Computes DBSCAN clustering on normalized embeddings using cosine distance.
    Returns a list of cluster IDs for each embedding (-1 for noise).
    """
    n = len(embeddings)
    if n == 0:
        return []
        
    labels = [-1] * n
    
    # Calculate cosine distance matrix (1.0 - cosine_similarity)
    # Cosine similarity is the dot product because our vectors are normalized
    dist_matrix = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i, n):
            if i == j:
                dist_matrix[i][j] = 0.0
            else:
                dot = sum(a * b for a, b in zip(embeddings[i], embeddings[j]))
                dist = max(0.0, 1.0 - dot)
                dist_matrix[i][j] = dist
                dist_matrix[j][i] = dist
                
    def get_neighbors(p_idx):
        return [i for i, d in enumerate(dist_matrix[p_idx]) if d <= eps]
        
    cluster_id = 0
    for i in range(n):
        if labels[i] != -1:
            continue
            
        neighbors = get_neighbors(i)
        if len(neighbors) < min_samples:
            labels[i] = -1
        else:
            labels[i] = cluster_id
            queue = [nb for nb in neighbors if nb != i]
            
            idx = 0
            while idx < len(queue):
                q_idx = queue[idx]
                if labels[q_idx] == -1: # Was noise or unvisited
                    labels[q_idx] = cluster_id
                elif labels[q_idx] >= 0:
                    idx += 1
                    continue
                    
                q_neighbors = get_neighbors(q_idx)
                if len(q_neighbors) >= min_samples:
                    for qn in q_neighbors:
                        if qn not in queue and qn != i:
                            queue.append(qn)
                idx += 1
            cluster_id += 1
            
    return labels


def calculate_recency_decay(dates: list[datetime.datetime]) -> float:
    """
    Calculates recency decay based on the average age of the queries in days.
    """
    if not dates:
        return 1.0
    now = datetime.datetime.now(datetime.timezone.utc)
    ages = [(now - d.replace(tzinfo=datetime.timezone.utc) if d.tzinfo else now - d.replace(tzinfo=datetime.timezone.utc)).days for d in dates]
    avg_age = sum(ages) / len(ages)
    # Decay formula: 1 / (1 + 0.05 * avg_age)
    return 1.0 / (1.0 + 0.05 * avg_age)


def consolidate_memories(eps: float = None, min_samples: int = None, score_threshold: float = 1.5):
    """
    Nightly consolidation job that clusters episodic memories and promotes to frozen core memory.
    """
    if eps is None:
        eps = settings.dbscan_eps
    if min_samples is None:
        min_samples = settings.consolidation_min_samples
    conn = get_pg_connection()
    try:
        # Fetch episodic memories with user ratings
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT id, query_text, retrieved_chunk_ids, user_rating, response_text, created_at
                FROM memory_episodic
                WHERE user_rating IS NOT NULL AND response_text IS NOT NULL
                """
            )
            rows = cur.fetchall()
            
        if len(rows) < min_samples:
            return
            
        queries = [r["query_text"] for r in rows]
        
        # Batch embed all queries
        try:
            embeddings = get_embeddings_batch(queries)
        except Exception:
            # If embedding fails, we cannot proceed
            return
            
        # DBSCAN clustering
        labels = dbscan_custom(embeddings, eps=eps, min_samples=min_samples)
        
        # Group by clusters
        clusters = {}
        for idx, label in enumerate(labels):
            if label == -1:
                continue
            if label not in clusters:
                clusters[label] = []
            clusters[label].append({
                "row": rows[idx],
                "embedding": embeddings[idx]
            })
            
        for label, cluster_items in clusters.items():
            ratings = [item["row"]["user_rating"] for item in cluster_items]
            avg_rating = sum(ratings) / len(ratings)
            frequency = len(cluster_items)
            dates = [item["row"]["created_at"] for item in cluster_items]
            
            decay = calculate_recency_decay(dates)
            consistency_score = frequency * avg_rating * decay
            
            if consistency_score >= score_threshold:
                # 1. Canonical query: choose the query with minimum average distance to others in cluster
                best_canonical_idx = 0
                min_avg_dist = float('inf')
                for i in range(len(cluster_items)):
                    dist_sum = 0.0
                    for j in range(len(cluster_items)):
                        if i != j:
                            dot = sum(a * b for a, b in zip(cluster_items[i]["embedding"], cluster_items[j]["embedding"]))
                            dist_sum += max(0.0, 1.0 - dot)
                    avg_dist = dist_sum / max(1, len(cluster_items) - 1)
                    if avg_dist < min_avg_dist:
                        min_avg_dist = avg_dist
                        best_canonical_idx = i
                        
                canonical = cluster_items[best_canonical_idx]
                canonical_query = canonical["row"]["query_text"]
                canonical_emb = canonical["embedding"]
                canonical_chunk_ids = canonical["row"]["retrieved_chunk_ids"]
                
                # 2. Best answer: answer with the highest star rating
                best_answer_row = max(cluster_items, key=lambda item: item["row"]["user_rating"])
                best_answer = best_answer_row["row"]["response_text"]
                
                # Insert or update memory_frozen
                with conn.cursor() as cur:
                    # Check if already exists to prevent duplicate entries
                    cur.execute(
                        "SELECT id FROM memory_frozen WHERE canonical_query = %s LIMIT 1",
                        (canonical_query,)
                    )
                    existing = cur.fetchone()
                    if existing:
                        cur.execute(
                            """
                            UPDATE memory_frozen
                            SET answer_text = %s, source_chunk_ids = %s, consistency_score = %s, embedding = %s::vector, promoted_at = CURRENT_TIMESTAMP
                            WHERE id = %s
                            """,
                            (best_answer, canonical_chunk_ids, consistency_score, str(canonical_emb), existing[0])
                        )
                    else:
                        cur.execute(
                            """
                            INSERT INTO memory_frozen (canonical_query, answer_text, source_chunk_ids, consistency_score, embedding)
                            VALUES (%s, %s, %s, %s, %s::vector)
                            """,
                            (canonical_query, best_answer, canonical_chunk_ids, consistency_score, str(canonical_emb))
                        )
                conn.commit()
    finally:
        release_pg_connection(conn)


def check_frozen_memory(query_text: str) -> str:
    """
    Checks memory_frozen for a matching canonical query (cosine similarity > 0.95).
    Returns the cached answer if found, otherwise None.
    """
    try:
        emb = get_embeddings_batch([query_text])[0]
    except Exception:
        return None
        
    conn = get_pg_connection()
    try:
        with conn.cursor(row_factory=dict_row) as cur:
            # We use pgvector <=> operator (cosine distance)
            # Cosine similarity = 1.0 - Cosine distance
            cur.execute(
                """
                SELECT answer_text, (1.0 - (embedding <=> %s::vector)) as similarity
                FROM memory_frozen
                ORDER BY embedding <=> %s::vector
                LIMIT 1
                """,
                (str(emb), str(emb))
            )
            row = cur.fetchone()
            if row and row["similarity"] > settings.frozen_memory_similarity_threshold:
                return row["answer_text"]
    except Exception:
        pass
    finally:
        release_pg_connection(conn)
    return None
