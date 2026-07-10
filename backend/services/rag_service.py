import json
import numpy as np
import requests
from psycopg.rows import dict_row

from database import (
    get_pg_connection,
    release_pg_connection,
    get_redis_client
)
from services.embedding_service import get_embeddings_batch
from services.memory_service import check_frozen_memory
from config import settings

def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Fast cosine similarity between two lists."""
    va = np.array(a, dtype=np.float32)
    vb = np.array(b, dtype=np.float32)
    na, nb = np.linalg.norm(va), np.linalg.norm(vb)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(va, vb) / (na * nb))


def _is_latin_readable(text: str, max_non_latin_ratio: float = 0.40) -> bool:
    """
    Returns True if the chunk is primarily Latin/ASCII text (English-readable).
    Filters out chunks from documents like Hindi PDFs where the content is
    predominantly Devanagari or other non-Latin scripts — which Phi-3-mini
    cannot process and causes garbled hallucinated output.
    A chunk is kept if its non-Latin character ratio is below max_non_latin_ratio.
    """
    if not text:
        return False
    # Count characters outside the Latin + common punctuation/digit range
    non_latin = sum(
        1 for ch in text
        if ord(ch) > 0x036F  # Beyond Latin Extended, Greek, Cyrillic ranges
        and not ch.isspace()
        and not ch.isdigit()
        and ch not in '!"#$%&\'()*+,-./:;<=>?@[\\]^_`{|}~'
    )
    total_alpha = sum(1 for ch in text if not ch.isspace())
    if total_alpha == 0:
        return False
    return (non_latin / total_alpha) < max_non_latin_ratio


def semantic_retrieval(query_text: str, limit: int = 20) -> list[dict]:
    """
    Embeds the query text and retrieves top chunks based on Python-side cosine
    similarity against JSONB-stored embeddings (no pgvector required).
    """
    try:
        query_emb = get_embeddings_batch([query_text])[0]
    except Exception:
        query_emb = [0.0] * 1536

    conn = get_pg_connection()
    try:
        # Fetch all chunks with their embeddings
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT 
                    c.chunk_id, 
                    c.version_id, 
                    c.chunk_index, 
                    c.content, 
                    c.embedding,
                    dv.doc_id, 
                    d.title
                FROM chunks c
                JOIN document_versions dv ON c.version_id = dv.version_id
                JOIN documents d ON dv.doc_id = d.id
                """
            )
            rows = cur.fetchall()

        # Score every chunk in Python, filtering out non-Latin (e.g. Hindi) content
        scored = []
        for row in rows:
            emb = row["embedding"]
            if isinstance(emb, str):
                try:
                    emb = json.loads(emb)
                except Exception:
                    continue
            if not emb:
                continue
            # Skip chunks that are predominantly non-Latin script (Hindi PDFs, etc.)
            if not _is_latin_readable(row["content"]):
                continue
            sim = _cosine_similarity(query_emb, emb)
            item = dict(row)
            item["similarity"] = sim
            item.pop("embedding", None)  # don't carry large vectors forward
            scored.append(item)

        # Return top-limit by similarity
        scored.sort(key=lambda x: x["similarity"], reverse=True)
        return scored[:limit]
    finally:
        release_pg_connection(conn)


def keyword_retrieval(query_text: str, limit: int = 20) -> list[dict]:
    """
    Performs PostgreSQL full-text search on chunk content.
    """
    conn = get_pg_connection()
    try:
        with conn.cursor(row_factory=dict_row) as cur:
            # We use plainto_tsquery for simple keyword matching
            cur.execute(
                """
                SELECT 
                    c.chunk_id, 
                    c.version_id, 
                    c.chunk_index, 
                    c.content, 
                    dv.doc_id, 
                    d.title,
                    ts_rank(to_tsvector('english', c.content), plainto_tsquery('english', %s)) as rank
                FROM chunks c
                JOIN document_versions dv ON c.version_id = dv.version_id
                JOIN documents d ON dv.doc_id = d.id
                WHERE to_tsvector('english', c.content) @@ plainto_tsquery('english', %s)
                ORDER BY rank DESC
                LIMIT %s
                """,
                (query_text, query_text, limit)
            )
            rows = cur.fetchall()
            # Filter out predominantly non-Latin chunks (Hindi PDFs, etc.)
            return [dict(r) for r in rows if _is_latin_readable(r["content"])]
    finally:
        release_pg_connection(conn)


def rrf_blend(semantic_results: list[dict], keyword_results: list[dict], k: int = 60, limit: int = 30) -> list[dict]:
    """
    Merges and ranks chunk results from Semantic and Keyword searches using Reciprocal Rank Fusion.
    """
    rrf_scores = {}
    chunk_map = {}
    
    # Process semantic results
    for rank, chunk in enumerate(semantic_results, start=1):
        cid = str(chunk["chunk_id"])
        chunk_map[cid] = chunk
        rrf_scores[cid] = rrf_scores.get(cid, 0.0) + (1.0 / (k + rank))
        
    # Process keyword results
    for rank, chunk in enumerate(keyword_results, start=1):
        cid = str(chunk["chunk_id"])
        chunk_map[cid] = chunk
        rrf_scores[cid] = rrf_scores.get(cid, 0.0) + (1.0 / (k + rank))
        
    # Sort chunks by score
    sorted_ids = sorted(rrf_scores.keys(), key=lambda cid: rrf_scores[cid], reverse=True)
    
    blended = []
    for cid in sorted_ids[:limit]:
        item = dict(chunk_map[cid])
        item["rrf_score"] = round(rrf_scores[cid], 5)
        blended.append(item)
        
    return blended


def local_rerank(query_text: str, chunks: list[dict], limit: int = 15) -> list[dict]:
    """
    Sends chunks to a local cross-encoder model to re-rank.
    Falls back to original order if endpoint is unavailable or fails.
    """
    if not chunks:
        return []
        
    try:
        documents = [c["content"] for c in chunks]
        response = requests.post(
            f"{settings.vllm_api_url}/rerank", # Or standard Cross-Encoder endpoint
            json={
                "query": query_text,
                "documents": documents,
                "top_n": limit
            },
            timeout=5
        )
        if response.status_code == 200:
            results = response.json().get("results", [])
            # Map index and sort
            reranked_chunks = []
            for r in results:
                idx = r["index"]
                score = r["relevance_score"]
                item = dict(chunks[idx])
                item["rerank_score"] = score
                reranked_chunks.append(item)
            return reranked_chunks
    except Exception:
        pass
        
    # Fallback: slice top limit from RRF list directly
    return chunks[:limit]
def context_tagger(chunks: list[dict]) -> list[dict]:
    """
    Assigns tags based on active status: [Changed Content], [Affected Content], [Overlapping Content], or [Source Content].
    """
    if not chunks:
        return []

    redis_client = get_redis_client()

    # 1. Fetch active review workspace document IDs
    active_workspace = set()
    try:
        workspace_members = redis_client.smembers("active_review_workspace")
        if workspace_members:
            active_workspace = {m.decode('utf-8') if isinstance(m, bytes) else str(m) for m in workspace_members}
    except Exception:
        pass

    tagged_chunks = []
    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            for chunk in chunks:
                chunk_id = str(chunk["chunk_id"])
                doc_id   = str(chunk["doc_id"])

                has_changes = False
                cur.execute(
                    "SELECT 1 FROM change_events WHERE doc_id = %s LIMIT 1",
                    (doc_id,)
                )
                if cur.fetchone():
                    has_changes = True

                has_overlap = False
                cur.execute(
                    "SELECT 1 FROM overlap_records WHERE matched_chunk_ids @> %s::jsonb LIMIT 1",
                    (json.dumps([chunk_id]),)
                )
                if cur.fetchone():
                    has_overlap = True

                if has_changes:
                    tag = "[Changed Content]"
                elif doc_id in active_workspace:
                    tag = "[Affected Content]"
                elif has_overlap:
                    tag = "[Overlapping Content]"
                else:
                    tag = "[Source Content]"

                item = dict(chunk)
                item["tag"] = tag
                tagged_chunks.append(item)
    except Exception as e:
        print(f"[context_tagger] DB query failed: {e}")
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        release_pg_connection(conn)

    return tagged_chunks


def generate_rag_response(session_id: str, query_text: str):
    """
    Runs the full RAG pipeline and yields stream output chunks.
    Integrates Working Memory, Episodic Memory, and Frozen Core Memory.
    """
    # 1. Check Frozen Core Memory (similarity > 0.95)
    cached_answer = check_frozen_memory(query_text)
    if cached_answer:
        yield f"[FROM MEMORY]\n{cached_answer}"
        return

    redis_client = get_redis_client()

    # 2. Retrieve Working Memory (conversational context)
    working_memory = []
    try:
        redis_val = redis_client.get(f"working_memory:{session_id}")
        if redis_val:
            working_memory = json.loads(redis_val)
    except Exception:
        pass

    # 3. Hybrid Retrieval
    semantic_res = semantic_retrieval(query_text, limit=20)
    keyword_res = keyword_retrieval(query_text, limit=20)
    
    # 4. RRF Blend
    blended = rrf_blend(semantic_res, keyword_res, limit=30)
    
    # 5. Local Re-Ranker
    reranked = local_rerank(query_text, blended, limit=settings.rerank_top_k)
    
    # 6. Context Tagging
    tagged = context_tagger(reranked)
    
    # 7. Initial write to Episodic Memory to get serial ID (query_id)
    conn = get_pg_connection()
    query_id = None
    try:
        retrieved_ids = [str(c["chunk_id"]) for c in tagged]
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO memory_episodic (query_text, retrieved_chunk_ids, session_id)
                VALUES (%s, %s, %s)
                RETURNING id
                """,
                (query_text, json.dumps(retrieved_ids), session_id)
            )
            row = cur.fetchone()
            if row:
                query_id = row[0]
        conn.commit()
    except Exception as e:
        print(f"Failed to log initial episodic memory: {e}")
    finally:
        release_pg_connection(conn)

    # Yield the query_id header for feedback mapping
    if query_id:
        yield f"[QUERY_ID: {query_id}]\n"
        
    # 8. Format LLM Context Prompt
    if not tagged:
        # No relevant chunks found — tell the user cleanly without hallucinating
        no_doc_msg = (
            "I could not find any relevant policy documents in the knowledge base "
            "that match your query. Please upload policy documents first via the "
            "Documents section, or rephrase your question."
        )
        yield no_doc_msg
        _save_to_memories(session_id, query_text, no_doc_msg, query_id, working_memory)
        return

    context_str = ""
    # Truncate each chunk to 800 chars — keeps prompt within Phi-3's 4096-token window
    MAX_CHUNK_CHARS = 800
    for idx, c in enumerate(tagged, start=1):
        status_note = ""
        if c["tag"] == "[Changed Content]":
            status_note = " (recently modified)"
        elif c["tag"] == "[Affected Content]":
            status_note = " (under active review)"
        elif c["tag"] == "[Overlapping Content]":
            status_note = " (overlaps with another policy)"
        content = c['content']
        if len(content) > MAX_CHUNK_CHARS:
            content = content[:MAX_CHUNK_CHARS] + "..."
        context_str += f"--- Policy Excerpt {idx}{status_note} ---\n"
        context_str += f"Source: {c['title']}\n"
        context_str += f"{content}\n\n"

    system_prompt = (
        "You are an expert policy assistant. "
        "Answer the user's question using ONLY the policy excerpts provided below. "
        "Your response MUST follow this exact structure:\n"
        "1. Explanation: A clear, direct explanation answering the user's query.\n"
        "2. Source Details: Specify in detail exactly WHERE (document title, page, paragraph/section) "
        "and WHY you retrieved and used this information. Under this section, you MUST quote the relevant "
        "paragraph(s) or sentences directly from the excerpts so the user can see the exact text used.\n\n"
        "If the provided excerpts do not contain enough information to answer fully, say so — "
        "do NOT invent or assume any policy rules."
    )

    # Build messages with working memory (conversational context) - limit to last 4 messages
    messages = [{"role": "system", "content": system_prompt}]
    for msg in working_memory[-4:]:
        messages.append(msg)

    # Append current user query
    user_prompt = f"""Policy Excerpts:
{context_str}
Question: {query_text}
Answer:"""
    # Safety guard: hard-truncate the context section if still too large for Phi-3's 4k limit
    MAX_PROMPT_CHARS = 3000
    if len(user_prompt) > MAX_PROMPT_CHARS:
        truncated_context = context_str[:MAX_PROMPT_CHARS - 200]
        user_prompt = f"""Policy Excerpts (truncated for context limit):
{truncated_context}
...
Question: {query_text}
Answer:"""
    messages.append({"role": "user", "content": user_prompt})

    full_response = ""
    
    # 9. Call vLLM chat completions streaming API
    try:
        print("--- MESSAGES SENT TO LLM ---")
        print(json.dumps(messages, indent=2))
        print("----------------------------")
        response = requests.post(
            f"{settings.vllm_api_url}/chat/completions",
            json={
                "model": "phi3",
                "messages": messages,
                "temperature": 0.0,
                "max_tokens": 1024,
                "stream": True
            },
            stream=True,
            timeout=360
        )
        
        if response.status_code == 200:
            for line in response.iter_lines():
                if line:
                    decoded = line.decode('utf-8')
                    if decoded.startswith("data: "):
                        data_str = decoded[6:].strip()
                        if data_str == "[DONE]":
                            break
                        try:
                            data_json = json.loads(data_str)
                            token = data_json['choices'][0]['delta'].get('content', '')
                            if token:
                                # Strip Unicode replacement character (U+FFFD) that
                                # Phi-3 special tokens can produce on Windows terminals
                                token = token.replace('\ufffd', '')
                                full_response += token
                                yield token
                        except Exception:
                            pass
                            
            # Save response to Episodic & Working memory on successful stream completion
            if full_response.strip():
                _save_to_memories(session_id, query_text, full_response, query_id, working_memory)
            return
        else:
            err_body = response.text[:300] if response.text else "(no body)"
            print(f"[RAG] llama.cpp returned status {response.status_code}: {err_body}")
            # If context exceeded token limit, llama.cpp returns 400
            if response.status_code == 400:
                yield "I found relevant policy excerpts but the context was too large for the AI model. Please try a more specific question."
                return
    except Exception as e:
        print(f"[RAG] Exception calling llama.cpp: {e}")
        import traceback
        traceback.print_exc()
        
    # Local fallback generation if llama.cpp server is offline
    full_response = "Offline Fallback: The Phi-3 (llama.cpp) generative endpoint is currently offline.\n\n"
    full_response += "Here are the retrieved policy references that match your query:\n"
    for idx, c in enumerate(tagged[:5], start=1):
        full_response += f"- {c['tag']} **{c['title']}** (Chunk {c['chunk_index']}): \"{c['content'][:120]}...\"\n"
        
    yield full_response
    
    # Save fallback response to memories
    _save_to_memories(session_id, query_text, full_response, query_id, working_memory)


def _save_to_memories(session_id: str, query_text: str, response_text: str, query_id: int, working_memory: list):
    """
    Helper function to save response to working memory in Redis and episodic memory in PostgreSQL.
    """
    # 1. Update Episodic Memory
    if query_id:
        conn = get_pg_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE memory_episodic SET response_text = %s WHERE id = %s",
                    (response_text, query_id)
                )
            conn.commit()
        except Exception as e:
            print(f"Failed to save response to episodic memory: {e}")
        finally:
            release_pg_connection(conn)
            
    # 2. Update Working Memory in Redis
    redis_client = get_redis_client()
    try:
        working_memory.append({"role": "user", "content": query_text})
        working_memory.append({"role": "assistant", "content": response_text})
        working_memory = working_memory[-20:] # Keep last 10 pairs (20 messages)
        redis_client.setex(f"working_memory:{session_id}", 14400, json.dumps(working_memory))
    except Exception as e:
        print(f"Failed to update working memory: {e}")

