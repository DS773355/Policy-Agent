"""
impact_service.py — Offline version.

Replaces:
  • Neo4j  → doc_graph PostgreSQL table (adjacency list + BFS)
  • pgvector <=> operator → Python numpy cosine similarity
"""
import json
import numpy as np
import requests
from psycopg.rows import dict_row

from database import get_pg_connection, release_pg_connection, get_redis_client
from services.embedding_service import calculate_cosine_similarity
from config import settings


# ─── Citation Extraction ─────────────────────────────────────────────────────

def extract_citations(chunk_text: str) -> list[str]:
    """
    Calls the vLLM chat completions API to identify referenced policy documents.
    Falls back to empty list if unreachable or no references found.
    """
    try:
        prompt = f"""You are an AI assistant that reads policy text and identifies explicit citations/references to OTHER policy documents.
Identify the exact titles/names of the referenced documents. Ignore internal section references of the same policy.
Return your answer as a JSON array of strings.
Example: ["Information Security Policy", "Data Privacy Regulation"]
If no references are found, return [].

Policy Text:
\"\"\"
{chunk_text}
\"\"\"

Respond ONLY with the JSON array, no explanation, no markdown block.
"""
        response = requests.post(
            f"{settings.vllm_api_url}/chat/completions",
            json={
                "model": "phi3",
                "messages": [
                    {"role": "system", "content": "You are a policy parser that outputs JSON lists."},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.0,
                "max_tokens": 100
            },
            timeout=8
        )
        if response.status_code == 200:
            content = response.json()['choices'][0]['message']['content'].strip()
            if content.startswith("```"):
                content = content.replace("```json", "").replace("```", "").strip()
            titles = json.loads(content)
            if isinstance(titles, list):
                return [str(t).strip() for t in titles]
    except Exception:
        pass
    return []


def run_citation_extraction(doc_id: str, version_number: int, chunks: list[dict]):
    """
    For each chunk, extract citations and store CITES edges in doc_graph.
    """
    conn = get_pg_connection()
    try:
        for chunk in chunks:
            chunk_text = chunk.get("content", "")
            cited_titles = extract_citations(chunk_text)

            for title in cited_titles:
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute(
                        "SELECT id FROM documents WHERE title ILIKE %s LIMIT 1",
                        (f"%{title}%",)
                    )
                    row = cur.fetchone()
                    if row:
                        target_doc_id = str(row["id"])
                        try:
                            with conn.cursor() as wcur:
                                wcur.execute(
                                    """
                                    INSERT INTO doc_graph (src_doc_id, tgt_doc_id, edge_type, similarity)
                                    VALUES (%s, %s, 'CITES', 1.0)
                                    ON CONFLICT (src_doc_id, tgt_doc_id, edge_type) DO NOTHING
                                    """,
                                    (doc_id, target_doc_id)
                                )
                            conn.commit()
                        except Exception:
                            conn.rollback()
    finally:
        release_pg_connection(conn)


# ─── Semantic Proximity ───────────────────────────────────────────────────────

def classify_overlap_class(chunk_a: str, chunk_b: str, similarity: float) -> str:
    """Heuristic overlap classification — no LLM needed in offline mode."""
    if similarity >= 0.98:
        return "DUPLICATE"
    elif similarity >= 0.94:
        return "SUPERSEDED"
    else:
        return "PARTIAL_OVERLAP"


def run_semantic_proximity_check(doc_id: str, version_number: int, chunks: list[dict]):
    """
    Finds chunks in OTHER documents with cosine similarity > threshold.
    Uses Python-side numpy comparison instead of pgvector <=> operator.
    """
    conn = get_pg_connection()
    try:
        for chunk in chunks:
            chunk_id = chunk.get("chunk_id")
            embedding = chunk.get("embedding")
            content = chunk.get("content", "")
            if not embedding:
                continue

            # Fetch candidate chunks from other documents
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    SELECT
                        c.chunk_id,
                        c.content,
                        c.embedding,
                        dv.doc_id AS other_doc_id,
                        dv.version_number AS other_version
                    FROM chunks c
                    JOIN document_versions dv ON c.version_id = dv.version_id
                    WHERE dv.doc_id != %s
                    LIMIT 200
                    """,
                    (doc_id,)
                )
                candidates = cur.fetchall()

            for row in candidates:
                other_emb = row["embedding"]
                if isinstance(other_emb, str):
                    try:
                        other_emb = json.loads(other_emb)
                    except Exception:
                        continue
                if not other_emb:
                    continue

                similarity = calculate_cosine_similarity(embedding, other_emb)

                if similarity > settings.overlap_similarity_threshold:
                    other_chunk_id = str(row["chunk_id"])
                    other_doc_id = str(row["other_doc_id"])
                    other_content = row["content"]
                    overlap_class = classify_overlap_class(content, other_content, similarity)

                    try:
                        with conn.cursor() as cur:
                            cur.execute(
                                """
                                INSERT INTO overlap_records
                                    (doc_id_a, doc_id_b, overlap_class, matched_chunk_ids)
                                VALUES (%s, %s, %s, %s)
                                """,
                                (doc_id, other_doc_id, overlap_class,
                                 json.dumps([chunk_id, other_chunk_id]))
                            )
                            # Add OVERLAPS_WITH edge in doc_graph
                            cur.execute(
                                """
                                INSERT INTO doc_graph (src_doc_id, tgt_doc_id, edge_type, similarity)
                                VALUES (%s, %s, 'OVERLAPS_WITH', %s)
                                ON CONFLICT (src_doc_id, tgt_doc_id, edge_type)
                                DO UPDATE SET similarity = GREATEST(doc_graph.similarity, EXCLUDED.similarity)
                                """,
                                (doc_id, other_doc_id, float(similarity))
                            )
                        conn.commit()
                    except Exception:
                        conn.rollback()
    finally:
        release_pg_connection(conn)


# ─── Graph BFS (replaces Neo4j traversal) ────────────────────────────────────

def bfs_graph(start_doc_id: str, max_hops: int = 3) -> list[dict]:
    """
    BFS traversal of doc_graph from start_doc_id up to max_hops.
    Returns [{doc_id, hop, similarity}, ...] for all reachable nodes.
    """
    conn = get_pg_connection()
    visited = {start_doc_id: 0}
    queue = [(start_doc_id, 0, 1.0)]  # (doc_id, hop, cumulative_similarity)
    results = []

    try:
        while queue:
            current_id, hop, cum_sim = queue.pop(0)
            if hop >= max_hops:
                continue

            with conn.cursor(row_factory=dict_row) as cur:
                # Traverse both directions
                cur.execute(
                    """
                    SELECT tgt_doc_id AS neighbor, similarity FROM doc_graph WHERE src_doc_id = %s
                    UNION
                    SELECT src_doc_id AS neighbor, similarity FROM doc_graph WHERE tgt_doc_id = %s
                    """,
                    (current_id, current_id)
                )
                edges = cur.fetchall()

            for edge in edges:
                neighbor = str(edge["neighbor"])
                sim = float(edge["similarity"] or 1.0)
                if neighbor == start_doc_id:
                    continue
                if neighbor not in visited:
                    visited[neighbor] = hop + 1
                    results.append({
                        "doc_id": neighbor,
                        "hop_count": hop + 1,
                        "path_similarity": min(cum_sim, sim)
                    })
                    queue.append((neighbor, hop + 1, min(cum_sim, sim)))
    finally:
        release_pg_connection(conn)

    return results


# ─── Impact Engine ────────────────────────────────────────────────────────────

def evaluate_impact(doc_id: str, version_number: int, change_class: int) -> list[dict]:
    """
    Triggered when change_class >= 2.
    Traverses doc_graph up to 3 hops from the changed document.
    Calculates impact scores and adds high-scoring docs to the Redis review workspace.
    """
    if change_class < 2:
        return []

    redis_client = get_redis_client()
    connected_docs = bfs_graph(doc_id, max_hops=3)

    class_multipliers = {2: 1.0, 3: 1.5, 4: 2.0}
    change_multiplier = class_multipliers.get(change_class, 1.0)

    impacted_docs = []
    conn = get_pg_connection()
    try:
        for item in connected_docs:
            target_id = item["doc_id"]
            hop_count = item["hop_count"]
            path_similarity = item["path_similarity"]

            hop_weight = 1.0 / hop_count
            impact_score = hop_weight * path_similarity * change_multiplier

            # Fetch title
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    "SELECT title FROM documents WHERE id = %s LIMIT 1",
                    (target_id,)
                )
                row = cur.fetchone()
                title = row["title"] if row else "Unknown"

            in_workspace = False
            if impact_score > settings.impact_score_threshold:
                try:
                    redis_client.sadd("active_review_workspace", target_id)
                    redis_client.hset(
                        "active_review_workspace_scores",
                        target_id,
                        str(round(impact_score, 3))
                    )
                    in_workspace = True
                except Exception:
                    pass

            impacted_docs.append({
                "doc_id": target_id,
                "title": title,
                "hop_count": hop_count,
                "impact_score": round(impact_score, 3),
                "added_to_workspace": in_workspace,
            })
    finally:
        release_pg_connection(conn)

    impacted_docs.sort(key=lambda x: x["impact_score"], reverse=True)
    return impacted_docs


def get_impact_graph(doc_id: str, version: int = None) -> dict:
    """Returns impact graph data for API consumption."""
    nodes = []
    edges = []

    conn = get_pg_connection()
    try:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT id, title FROM documents WHERE id = %s", (doc_id,))
            origin = cur.fetchone()
            if origin:
                nodes.append({"id": str(origin["id"]), "title": origin["title"], "is_origin": True})

            cur.execute(
                """
                SELECT g.src_doc_id, g.tgt_doc_id, g.edge_type, g.similarity,
                       d1.title AS src_title, d2.title AS tgt_title
                FROM doc_graph g
                JOIN documents d1 ON g.src_doc_id = d1.id
                JOIN documents d2 ON g.tgt_doc_id = d2.id
                WHERE g.src_doc_id = %s OR g.tgt_doc_id = %s
                """,
                (doc_id, doc_id)
            )
            graph_edges = cur.fetchall()

        seen_nodes = {doc_id}
        for edge in graph_edges:
            src = str(edge["src_doc_id"])
            tgt = str(edge["tgt_doc_id"])
            for nid, title in [(src, edge["src_title"]), (tgt, edge["tgt_title"])]:
                if nid not in seen_nodes:
                    nodes.append({"id": nid, "title": title, "is_origin": False})
                    seen_nodes.add(nid)
            edges.append({
                "source": src,
                "target": tgt,
                "type": edge["edge_type"],
                "similarity": float(edge["similarity"] or 1.0)
            })
    finally:
        release_pg_connection(conn)

    return {"nodes": nodes, "edges": edges}
