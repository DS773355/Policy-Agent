from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Query, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional
import uuid
import asyncio
import time
import datetime
from contextlib import asynccontextmanager
from psycopg.rows import dict_row
import redis

from services.document_service import (
    extract_text_from_file,
    create_document_version,
    get_document_version_history,
    get_all_overlaps_grouped,
    get_latest_version
)
from services.impact_service import get_impact_graph
from services.rag_service import generate_rag_response
from database import close_connections, get_pg_connection, release_pg_connection, get_redis_client
from services.memory_service import consolidate_memories
from services.auth import get_current_user, RoleChecker, verify_password, create_access_token

class QueryRequest(BaseModel):
    session_id: str
    query_text: str

class FeedbackRequest(BaseModel):
    session_id: str
    query_id: int
    star_rating: int
    correction_note: Optional[str] = None

class LoginRequest(BaseModel):
    username: str
    password: str

class DraftLetterRequest(BaseModel):
    conversation: list[dict]          # [{role: 'user'|'assistant', text: str}, ...]
    instructions: Optional[str] = None  # extra user instructions for the letter

async def schedule_nightly_consolidation():
    """
    Background worker that runs memory consolidation nightly (every 24 hours).
    """
    while True:
        await asyncio.sleep(86400)
        try:
            consolidate_memories()
        except Exception as e:
            print(f"Error in nightly consolidation: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Start consolidation background task
    task = asyncio.create_task(schedule_nightly_consolidation())
    yield
    # Shutdown: Cancel background task and close database pools
    task.cancel()
    close_connections()

app = FastAPI(
    title="Policy Ingestion API",
    description="Backend API for document ingestion, chunking, and severity classification.",
    version="1.0.0",
    lifespan=lifespan
)

# Enable CORS for frontend integration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Logging Middleware
SKIP_LOG_PATHS = {"/docs", "/redoc", "/openapi.json", "/", "/favicon.ico"}
# Also skip streaming endpoints to avoid holding DB connections during long LLM responses
SKIP_LOG_PREFIXES = ("/docs/", "/api/chat/")

@app.middleware("http")
async def log_api_calls(request, call_next):
    start_time = time.time()
    response = await call_next(request)
    latency = int((time.time() - start_time) * 1000)

    import sys
    if "pytest" in sys.modules:
        return response

    # Skip logging for internal/documentation routes and streaming chat routes
    if request.url.path in SKIP_LOG_PATHS or any(request.url.path.startswith(p) for p in SKIP_LOG_PREFIXES):
        return response
    
    # Try to extract user from JWT
    user_id = None
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header.split(" ")[1]
        try:
            from services.auth import JWT_SECRET, JWT_ALGORITHM
            import jwt
            payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
            username = payload.get("sub")
            if username:
                conn = get_pg_connection()
                try:
                    with conn.cursor() as cur:
                        cur.execute("SELECT id FROM users WHERE username = %s LIMIT 1", (username,))
                        row = cur.fetchone()
                        if row:
                            user_id = row[0]
                finally:
                    release_pg_connection(conn)
        except Exception:
            pass
            
    # Log to PostgreSQL api_logs table
    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO api_logs (user_id, endpoint, latency_ms, status_code) VALUES (%s, %s, %s, %s)",
                (user_id, request.url.path, latency, response.status_code)
            )
        conn.commit()
    except Exception as e:
        print(f"Failed to log API request: {e}")
    finally:
        release_pg_connection(conn)
        
    return response


@app.get("/")
def read_root():
    return {"status": "online", "message": "Policy Agent API is running."}


@app.get("/api/health")
def health_check():
    """
    Checks connectivity to PostgreSQL, Redis, and llama.cpp (Phi-3) server.
    """
    components = {
        "postgres": "unhealthy",
        "redis": "unhealthy",
        "llama_cpp": "unhealthy"
    }
    
    # Check Postgres
    try:
        conn = get_pg_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
        release_pg_connection(conn)
        components["postgres"] = "healthy"
    except Exception:
        pass
        
    # Check Neo4j
    try:
        from database import get_neo4j_driver
        driver = get_neo4j_driver()
        driver.verify_connectivity()
        components["neo4j"] = "healthy"
    except Exception:
        pass
        
    # Check Redis
    try:
        from database import get_redis_client
        client = get_redis_client()
        if client.ping():
            components["redis"] = "healthy"
    except Exception:
        pass
        
    # Check llama.cpp server (Phi-3)
    try:
        from config import settings as cfg
        import requests
        res = requests.get(f"{cfg.vllm_api_url}/models", timeout=2)
        if res.status_code == 200:
            components["llama_cpp"] = "healthy"
    except Exception:
        pass
        
    is_healthy = (
        components["postgres"] == "healthy" and 
        components["redis"] == "healthy"
    )
    
    return {
        "status": "healthy" if is_healthy else "unhealthy",
        "components": components
    }


@app.post("/api/auth/login")
def login(request: LoginRequest):
    """
    Authenticates a user and returns a signed JWT.
    """
    conn = get_pg_connection()
    user = None
    try:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT id, username, password_hash, role FROM users WHERE username = %s LIMIT 1",
                (request.username,)
            )
            user = cur.fetchone()
    finally:
        release_pg_connection(conn)
        
    if not user or not verify_password(request.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid username or password.")
        
    token = create_access_token(data={"sub": user["username"], "role": user["role"]})
    return {
        "access_token": token,
        "token_type": "bearer",
        "username": user["username"],
        "role": user["role"]
    }


@app.post("/api/documents/upload")
async def upload_document(
    file: UploadFile = File(...),
    title: Optional[str] = Form(None),
    owner: Optional[str] = Form("system"),
    doc_id: Optional[str] = Form(None),
    current_user: dict = Depends(RoleChecker(["editor", "admin"]))
):
    """
    Upload a document (PDF, DOCX, or text) to ingest it (Editor/Admin only).
    Processing runs in a thread pool so it never blocks the event loop.
    """
    MAX_FILE_BYTES = 50 * 1024 * 1024  # 50 MB server-side guard

    # Read file bytes
    try:
        file_bytes = await file.read()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to read uploaded file: {str(e)}")

    if not file_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    if len(file_bytes) > MAX_FILE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum allowed size is 50 MB (received {len(file_bytes)//1024//1024} MB)."
        )

    # Validate extension
    allowed_extensions = {'.pdf', '.docx', '.txt'}
    fname = (file.filename or '').lower()
    if not any(fname.endswith(ext) for ext in allowed_extensions):
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type. Allowed: PDF, DOCX, TXT."
        )

    # Extract text (may be slow for large PDFs — keep synchronous for simplicity)
    try:
        raw_text = extract_text_from_file(file_bytes, file.filename)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Failed to parse document: {str(e)}")

    if not raw_text.strip():
        raise HTTPException(status_code=422, detail="No readable text found in document. The file may be image-only or corrupted.")

    final_title = title.strip() if title and title.strip() else file.filename

    if doc_id:
        try:
            uuid.UUID(doc_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid doc_id UUID format.")

    # Run heavy ingestion (chunking + embedding) in a thread pool to avoid
    # blocking the async event loop during long-running document processing
    try:
        import asyncio
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: create_document_version(
                doc_id=doc_id,
                raw_text=raw_text,
                title=final_title,
                owner=owner or "system",
                file_bytes=file_bytes
            )
        )
        return {
            "success": True,
            "message": "Document uploaded and processed successfully.",
            "data": result
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Ingestion failed: {str(e)}")



@app.get("/api/documents")
def list_documents(current_user: dict = Depends(get_current_user)):
    """
    Lists all documents with title, owner, latest version, and last change class.
    """
    conn = get_pg_connection()
    try:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT 
                    d.id, 
                    d.title, 
                    d.owner, 
                    d.created_at, 
                    COALESCE(MAX(dv.version_number), 1) as latest_version,
                    COALESCE(
                        (SELECT change_class FROM change_events WHERE doc_id = d.id ORDER BY triggered_at DESC LIMIT 1), 
                        0
                    ) as last_change_class
                FROM documents d
                LEFT JOIN document_versions dv ON d.id = dv.doc_id
                GROUP BY d.id, d.title, d.owner, d.created_at
                ORDER BY d.created_at DESC
                """
            )
            rows = cur.fetchall()
            return {
                "success": True,
                "documents": [dict(r) for r in rows]
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list documents: {str(e)}")
    finally:
        release_pg_connection(conn)


@app.delete("/api/documents/{doc_id}")
def delete_document(
    doc_id: str,
    current_user: dict = Depends(RoleChecker(["editor", "admin"]))
):
    """
    Permanently deletes a document and all its associated data (versions, chunks,
    change events, overlap records, citations, episodic memory references).
    Editor and Admin roles only.
    """
    try:
        uuid.UUID(doc_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid doc_id format.")

    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            # Check document exists
            cur.execute("SELECT id, title FROM documents WHERE id = %s", (doc_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Document not found.")
            title = row[1]

            # Temporarily disable mutability trigger
            cur.execute("ALTER TABLE document_versions DISABLE TRIGGER trigger_block_version_mutations;")

            # Cascade delete — order matters (FK constraints)
            # 1. Remove from overlap_records (both sides)
            cur.execute(
                "DELETE FROM overlap_records WHERE doc_id_a = %s OR doc_id_b = %s",
                (doc_id, doc_id)
            )
            # 2. Remove from doc_graph
            cur.execute(
                "DELETE FROM doc_graph WHERE src_doc_id = %s OR tgt_doc_id = %s",
                (doc_id, doc_id)
            )
            # 3. Remove change_events
            cur.execute("DELETE FROM change_events WHERE doc_id = %s", (doc_id,))
            # 4. Remove chunks (via version_id join)
            cur.execute(
                """
                DELETE FROM chunks
                WHERE version_id IN (
                    SELECT version_id FROM document_versions WHERE doc_id = %s
                )
                """,
                (doc_id,)
            )
            # 5. Remove document versions
            cur.execute("DELETE FROM document_versions WHERE doc_id = %s", (doc_id,))
            # 6. Remove document
            cur.execute("DELETE FROM documents WHERE id = %s", (doc_id,))

            # Re-enable trigger
            cur.execute("ALTER TABLE document_versions ENABLE TRIGGER trigger_block_version_mutations;")

        conn.commit()
        print(f"[delete_document] Deleted doc '{title}' ({doc_id})")
        return {"success": True, "message": f"Document '{title}' deleted successfully."}
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        import traceback; traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Delete failed: {str(e)}")
    finally:
        release_pg_connection(conn)


@app.post("/api/admin/clear-all-documents")
def clear_all_documents(current_user: dict = Depends(RoleChecker(["admin"]))):
    """
    Admin-only: wipes ALL documents and related data from the knowledge base.
    Use with caution — this is irreversible.
    """
    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("ALTER TABLE document_versions DISABLE TRIGGER trigger_block_version_mutations;")
            cur.execute("TRUNCATE TABLE overlap_records, doc_graph, change_events, chunks, document_versions, documents RESTART IDENTITY CASCADE")
            cur.execute("ALTER TABLE document_versions ENABLE TRIGGER trigger_block_version_mutations;")
        conn.commit()
        return {"success": True, "message": "All documents cleared from the knowledge base."}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Clear failed: {str(e)}")
    finally:
        release_pg_connection(conn)


@app.get("/api/workspace/suggestions")
def get_suggestions(current_user: dict = Depends(get_current_user)):
    """
    Returns real-time suggestion chips based on recent changes and active conflicts,
    along with a list of impacted documents currently in the review workspace.
    """
    conn = get_pg_connection()
    suggestions = []
    
    try:
        with conn.cursor(row_factory=dict_row) as cur:
            # 1. Fetch recent changes
            cur.execute(
                """
                SELECT ce.doc_id, ce.change_class, ce.change_summary, d.title
                FROM change_events ce
                JOIN documents d ON ce.doc_id = d.id
                ORDER BY ce.triggered_at DESC
                LIMIT 3
                """
            )
            changes = cur.fetchall()
            for c in changes:
                suggestions.append({
                    "label": "Recent Policy Update",
                    "title": c["title"],
                    "change_class": c["change_class"],
                    "query": f"Explain the changes and impact of the latest version of {c['title']}."
                })
                
            # 2. Fetch conflicts
            cur.execute(
                """
                SELECT o.id, da.title as title_a, db.title as title_b
                FROM overlap_records o
                JOIN documents da ON o.doc_id_a = da.id
                JOIN documents db ON o.doc_id_b = db.id
                WHERE o.overlap_class = 'CONFLICT'
                LIMIT 2
                """
            )
            conflicts = cur.fetchall()
            for cf in conflicts:
                suggestions.append({
                    "label": "Detected Policy Conflict",
                    "title": f"{cf['title_a']} & {cf['title_b']}",
                    "change_class": 3,
                    "query": f"What is the conflict between {cf['title_a']} and {cf['title_b']}?"
                })
    except Exception as e:
        print(f"Suggestions fetching failed: {e}")
    finally:
        release_pg_connection(conn)
        
    # Append default fallbacks if suggestions are empty or short
    if len(suggestions) < 3:
        suggestions.append({
            "label": "Compliance Health Check",
            "title": "All Policy Libraries",
            "change_class": 0,
            "query": "Are there any active conflicts or superseded sections in our policy documents?"
        })
        suggestions.append({
            "label": "Security Policy Overview",
            "title": "System Policies",
            "change_class": 1,
            "query": "What are the core security policies active in the workspace?"
        })
        
    # 3. Retrieve impacted documents in the active review workspace
    redis_client = get_redis_client()
    active_impacted = []
    try:
        workspace_members = redis_client.smembers("active_review_workspace")
        if workspace_members:
            doc_ids = [m.decode('utf-8') if isinstance(m, bytes) else str(m) for m in workspace_members]
            
            # Fetch scores
            scores_hash = redis_client.hgetall("active_review_workspace_scores")
            
            # Query titles from PostgreSQL
            conn = get_pg_connection()
            try:
                with conn.cursor(row_factory=dict_row) as cur:
                    for d_id in doc_ids:
                        try:
                            uuid.UUID(d_id)
                        except ValueError:
                            continue
                        cur.execute("SELECT title FROM documents WHERE id = %s LIMIT 1", (d_id,))
                        row = cur.fetchone()
                        if row:
                            score_bytes = scores_hash.get(d_id)
                            score_str = score_bytes.decode('utf-8') if isinstance(score_bytes, bytes) else str(score_bytes) if score_bytes else "0.5"
                            try:
                                score = float(score_str)
                            except ValueError:
                                score = 0.5
                            active_impacted.append({
                                "doc_id": d_id,
                                "title": row["title"],
                                "impact_score": score
                            })
            finally:
                release_pg_connection(conn)
    except Exception as e:
        print(f"Failed to fetch impacted documents: {e}")
        
    active_impacted.sort(key=lambda x: x["impact_score"], reverse=True)
    
    return {
        "success": True,
        "suggestions": suggestions,
        "active_impacted_docs": active_impacted
    }


@app.get("/api/documents/{doc_id}/versions")
def get_versions(doc_id: str, current_user: dict = Depends(get_current_user)):
    """
    Retrieves all ingested versions for the specified document ID.
    """
    try:
        uuid.UUID(doc_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid doc_id UUID format.")

    try:
        history = get_document_version_history(doc_id)
        if not history:
            raise HTTPException(status_code=404, detail="Document not found or has no versions.")
        return {
            "success": True,
            "doc_id": doc_id,
            "versions": history
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch history: {str(e)}")


@app.get("/api/documents/{doc_id}/impact")
def get_document_impact(
    doc_id: str,
    version: Optional[int] = Query(None, description="Document version number. If not provided, the latest version is used."),
    current_user: dict = Depends(get_current_user)
):
    """
    Retrieves the 3-hop impact graph nodes and edges for the specified document version.
    """
    try:
        uuid.UUID(doc_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid doc_id UUID format.")

    try:
        version_num = version
        if not version_num:
            latest = get_latest_version(doc_id)
            if not latest:
                raise HTTPException(status_code=404, detail="Document not found.")
            version_num = latest["version_number"]

        graph = get_impact_graph(doc_id, version_num)
        return {
            "success": True,
            "doc_id": doc_id,
            "version": version_num,
            "graph": graph
        }
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Failed to fetch impact graph: {str(e)}")


@app.get("/api/documents/overlaps")
def get_overlaps(current_user: dict = Depends(get_current_user)):
    """
    Retrieves all overlap records, grouped by overlap class.
    """
    try:
        overlaps = get_all_overlaps_grouped()
        return {
            "success": True,
            "overlaps": overlaps
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Failed to fetch overlap records: {str(e)}")


@app.post("/api/chat/query")
async def chat_query(request: QueryRequest, current_user: dict = Depends(get_current_user)):
    """
    Exposes the hybrid retrieval and streamed LLM generation interface (Authenticated + Rate limited).
    """
    # Rate Limiting
    redis_client = get_redis_client()
    user_id = current_user["id"]
    minute_bucket = datetime.datetime.now().strftime("%Y-%m-%d-%H-%M")
    rate_key = f"rate_limit:{user_id}:{minute_bucket}"

    try:
        requests_count = redis_client.incr(rate_key)
        if requests_count == 1:
            redis_client.expire(rate_key, 60)
        if requests_count > 30:
            raise HTTPException(status_code=429, detail="Rate limit exceeded: Max 30 requests per minute.")
    except redis.RedisError:
        pass  # fail-safe bypass

    try:
        # Run the synchronous generator in a thread pool so it doesn't block the event loop
        import asyncio
        loop = asyncio.get_event_loop()

        async def async_generator():
            """Wrap the sync generator so FastAPI can stream it asynchronously."""
            gen = generate_rag_response(
                session_id=request.session_id,
                query_text=request.query_text
            )
            # Push each yielded chunk to an async queue via run_in_executor
            for chunk in gen:
                yield chunk
                # Yield control back to the event loop between chunks
                await asyncio.sleep(0)

        return StreamingResponse(async_generator(), media_type="text/plain; charset=utf-8")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Query execution failed: {str(e)}")


@app.post("/api/chat/feedback")
def chat_feedback(request: FeedbackRequest, current_user: dict = Depends(get_current_user)):
    """
    Receives user rating and feedback correction notes.
    If rating <= 2 and a correction note is provided, automatically triggers a corrected re-run.
    """
    conn = get_pg_connection()
    original_query = None
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE memory_episodic
                SET user_rating = %s, correction_note = %s
                WHERE id = %s AND session_id = %s
                RETURNING query_text
                """,
                (request.star_rating, request.correction_note, request.query_id, request.session_id)
            )
            row = cur.fetchone()
            if row:
                original_query = row[0]
        conn.commit()
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Feedback database update failed: {str(e)}")
    finally:
        release_pg_connection(conn)
        
    if not original_query:
        raise HTTPException(status_code=404, detail="Query record not found or session ID mismatch.")
        
    if request.star_rating <= 2 and request.correction_note:
        corrected_query = f"{original_query} [Correction: {request.correction_note}]"
        
        def stream_corrected():
            yield "[CORRECTED RESPONSE]\n"
            for chunk in generate_rag_response(request.session_id, corrected_query):
                if chunk.startswith("[QUERY_ID:"):
                    continue
                yield chunk
                
        return StreamingResponse(stream_corrected(), media_type="text/event-stream")
        
    return {"success": True, "message": "Feedback submitted successfully."}


@app.post("/api/chat/draft-letter")
async def draft_letter(request: DraftLetterRequest, current_user: dict = Depends(get_current_user)):
    """
    Streams a professionally drafted letter using conversation history as context.
    Uses the embedded letter-writing system prompt. Calls Phi-3 via llama.cpp.
    """
    LETTER_SYSTEM_PROMPT = """\
You are a professional letter-writing assistant. Your job is to draft a complete, ready-to-send letter using context from the conversation history provided, combined with any explicit instructions given.

STEP 1 — EXTRACT CONTEXT
Identify from the conversation: letter type/purpose, recipient (name, title, org), sender (name, title, org, contact), key facts that must appear (dates, incidents, amounts, reference numbers), desired tone, relationship between sender and recipient, and any relevant deadline.
Only use facts actually stated or clearly implied. Never invent names, dates, companies, or events. If something is unclear, treat it as missing rather than guessing.

STEP 2 — DRAFT OR CLARIFY
Draft directly if you have enough to produce something usable. Ask exactly ONE clarifying question, and only if an essential element is missing: the letter's core purpose, the recipient's identity (when clearly addressed to someone specific but unnamed), or one pivotal fact the letter can't function without. Otherwise, default sensibly and state assumptions briefly.

STEP 3 — MATCH STRUCTURE TO LETTER TYPE
- Formal/business (complaints, requests, appeals): sender block, date, recipient block, formal salutation, clear purpose up front, supporting detail, specific ask, formal close.
- Resignation: state intent immediately, last working day, brief reason (optional), transition offer, gratitude, short overall (under ~200 words).
- Recommendation: relationship/context, specific examples tied to the opportunity, clear endorsement, contact for follow-up.
- Cover letter: hook tied to the role, 2–3 concrete examples, why this company specifically, confident close.
- Personal (thank-you, apology, condolence, congratulations): warmer, conversational, sincerity over structure.
- Appeals/disputes: lead with the specific issue, cite facts/dates/reference numbers, state the exact remedy requested, firm but professional.
Default to clean formal structure if the type doesn't fit any category above.

STEP 4 — MISSING DETAILS
For minor gaps not worth a question, insert bracketed placeholders (e.g. [Your Name], [Date]) directly in the letter. List them afterward under "Fill in before sending."

STEP 5 — TONE & LENGTH
Match formality to the relationship implied by context. Prefer concise, plain, confident language over generic filler. Never pad with filler paragraphs.

STEP 6 — OUTPUT FORMAT
1. One line noting the inferred letter type and any assumption made (skip if everything was explicit).
2. The full letter, properly formatted.
3. "Fill in before sending" list, if placeholders were used.
4. One brief offer to adjust tone/length/formality.

GUARDRAILS
Never fabricate specific facts. If the conversation gives no usable basis for a letter, say so and ask what it's for.\
"""

    # Build the conversation context string
    conv_lines = []
    for msg in request.conversation[-20:]:  # last 20 messages max
        role = msg.get("role", "user")
        text = msg.get("text", "")
        if not text or text.startswith("👋"):
            continue
        conv_lines.append(f"[{role.upper()}]: {text[:800]}")  # truncate long messages

    conversation_context = "\n".join(conv_lines) if conv_lines else "(No prior conversation)"

    user_content = f"CONVERSATION_CONTEXT:\n{conversation_context}"
    if request.instructions and request.instructions.strip():
        user_content += f"\n\nUSER_INSTRUCTIONS:\n{request.instructions.strip()}"
    else:
        user_content += "\n\nUSER_INSTRUCTIONS: (none provided — draft based on conversation context only)"

    import requests as http_requests

    def stream_letter():
        try:
            from config import settings as cfg
            response = http_requests.post(
                f"{cfg.vllm_api_url}/chat/completions",
                json={
                    "model": "phi3",
                    "messages": [
                        {"role": "system", "content": LETTER_SYSTEM_PROMPT},
                        {"role": "user",   "content": user_content}
                    ],
                    "temperature": 0.3,
                    "max_tokens": 800,
                    "stream": True
                },
                stream=True,
                timeout=90
            )
            if response.status_code != 200:
                yield f"[Letter draft failed — LLM returned {response.status_code}]"
                return

            import json as _json
            for raw_line in response.iter_lines():
                if not raw_line:
                    continue
                line = raw_line.decode("utf-8", errors="replace") if isinstance(raw_line, bytes) else raw_line
                if line.startswith("data: "):
                    line = line[6:]
                if line.strip() == "[DONE]":
                    break
                try:
                    chunk_data = _json.loads(line)
                    delta = chunk_data.get("choices", [{}])[0].get("delta", {})
                    token = delta.get("content", "")
                    if token:
                        yield token
                except Exception:
                    continue
        except Exception as e:
            yield f"\n[Error generating letter: {e}]"

    return StreamingResponse(stream_letter(), media_type="text/plain; charset=utf-8")


@app.post("/api/admin/consolidate")
def trigger_consolidation(current_user: dict = Depends(RoleChecker(["admin"]))):
    """
    Manually triggers the memory consolidation background task (Admin only).
    """
    try:
        consolidate_memories()
        return {"success": True, "message": "Memory consolidation executed successfully."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Consolidation failed: {str(e)}")
