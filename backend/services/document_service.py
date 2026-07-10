import io
import json
import hashlib
from pypdf import PdfReader
import docx

import psycopg
from psycopg.rows import dict_row

from database import get_pg_connection, release_pg_connection, get_neo4j_driver
from services.chunker import split_into_chunks, extract_sections
from services.classifier import diff_and_embed_chunks, classify_change_severity
from services.impact_service import (
    run_citation_extraction,
    run_semantic_proximity_check,
    evaluate_impact
)

# Legacy Hindi font names for KrutiDev detection
_LEGACY_FONT_NAMES = frozenset({
    "krutidev", "kruti dev", "kdev", "mangal",
    "devlys", "shivaji", "akruti", "walkman chanakya", "chanakya",
})


def extract_text_from_file(file_bytes: bytes, filename: str) -> str:
    """
    Extracts text from bytes based on file format (PDF, DOCX, or plain text).
    For PDFs: tries pymupdf first (better Unicode + KrutiDev detection), falls back to pypdf.
    """
    file_lower = filename.lower()

    if file_lower.endswith('.pdf'):
        # --- Primary: pymupdf (better Unicode fidelity for government PDFs) ---
        try:
            import fitz  # pymupdf
            doc = fitz.open(stream=file_bytes, filetype="pdf")
            page_texts = []
            for pno in range(len(doc)):
                page = doc[pno]
                # Detect legacy Hindi fonts per page
                has_legacy = any(
                    any(k in (f[3] or f[4] or "").lower() for k in _LEGACY_FONT_NAMES)
                    for f in page.get_fonts(full=True)
                )
                raw = page.get_text("text")
                if has_legacy:
                    try:
                        from services.krutidev_map import krutidev_to_unicode
                        raw = krutidev_to_unicode(raw)
                    except ImportError:
                        pass  # KrutiDev map not available — use raw text
                page_texts.append(raw)
            doc.close()
            text = "\n\n".join(page_texts)
            if text.strip():
                return text
        except ImportError:
            pass  # pymupdf not installed — fall through to pypdf
        except Exception as e:
            print(f"[document_service] pymupdf failed ({e}), falling back to pypdf.")

        # --- Fallback: pypdf ---
        pdf_file = io.BytesIO(file_bytes)
        reader = PdfReader(pdf_file)
        text_parts = []
        for page in reader.pages:
            t = page.extract_text()
            if t:
                text_parts.append(t)
        return "\n".join(text_parts)

    elif file_lower.endswith('.docx'):
        docx_file = io.BytesIO(file_bytes)
        doc = docx.Document(docx_file)
        text_parts = [p.text for p in doc.paragraphs]
        return "\n".join(text_parts)

    else:
        # Treat as plain text
        try:
            return file_bytes.decode('utf-8')
        except UnicodeDecodeError:
            return file_bytes.decode('latin-1', errors='ignore')


def save_document_to_postgres(
    doc_id: str, title: str, owner: str,
    file_hash: str = None, metadata: dict = None
) -> str:
    """
    Creates a new document metadata entry in PostgreSQL if doc_id is None,
    otherwise returns the existing document ID.
    Deduplicates by SHA-256 hash to prevent duplicate uploads.
    """
    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            # Dedup by SHA-256 hash
            if file_hash:
                cur.execute(
                    "SELECT id FROM documents WHERE hash_sha256 = %s LIMIT 1",
                    (file_hash,)
                )
                row = cur.fetchone()
                if row:
                    return str(row[0])

            if doc_id:
                cur.execute("SELECT id FROM documents WHERE id = %s", (doc_id,))
                row = cur.fetchone()
                if row:
                    return str(row[0])

            # Create new document record
            meta_json = json.dumps(metadata or {})
            cur.execute(
                """
                INSERT INTO documents (title, owner, metadata, hash_sha256)
                VALUES (%s, %s, %s, %s)
                RETURNING id
                """,
                (title, owner, meta_json, file_hash)
            )
            new_id = cur.fetchone()[0]
            conn.commit()
            return str(new_id)
    finally:
        release_pg_connection(conn)


def get_latest_version(doc_id: str) -> dict:
    """
    Retrieves the latest version metadata and chunks for a given document.
    Embeddings are stored as JSONB and returned as Python lists.
    """
    conn = get_pg_connection()
    try:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT version_id, version_number, raw_text, chunked_text
                FROM document_versions
                WHERE doc_id = %s
                ORDER BY version_number DESC
                LIMIT 1
                """,
                (doc_id,)
            )
            ver = cur.fetchone()
            if not ver:
                return None

            # Fetch chunks — embedding is JSONB (list of floats)
            cur.execute(
                """
                SELECT chunk_id, chunk_index, content, embedding
                FROM chunks
                WHERE version_id = %s
                ORDER BY chunk_index ASC
                """,
                (ver["version_id"],)
            )
            chunks = cur.fetchall()
            ver_chunks = []
            for c in chunks:
                emb = c["embedding"]
                # Normalize: JSONB may arrive as list or JSON string
                if isinstance(emb, str):
                    try:
                        emb = json.loads(emb)
                    except Exception:
                        emb = []
                elif emb is None:
                    emb = []
                ver_chunks.append({
                    "chunk_id": str(c["chunk_id"]),
                    "chunk_index": c["chunk_index"],
                    "content": c["content"],
                    "embedding": emb
                })

            ver["chunks"] = ver_chunks
            return dict(ver)
    finally:
        release_pg_connection(conn)


def create_document_version(
    doc_id: str,
    raw_text: str,
    title: str,
    owner: str,
    file_bytes: bytes = None
) -> dict:
    """
    Ingests a new version of a document.
    Steps: dedup check → chunk → diff/embed → persist to PG → classify change → impact.
    """
    # Compute SHA-256 for deduplication
    if file_bytes:
        file_hash = hashlib.sha256(file_bytes).hexdigest()
    else:
        file_hash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()

    # 1. Dedup check: If document with matching hash exists, return it early
    existing_doc_id = None
    if file_hash:
        conn = get_pg_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM documents WHERE hash_sha256 = %s LIMIT 1", (file_hash,))
                row = cur.fetchone()
                if row:
                    existing_doc_id = str(row[0])
        except Exception as e:
            print(f"[dedup] Check failed: {e}")
        finally:
            try:
                release_pg_connection(conn)
            except Exception:
                pass

    if existing_doc_id:
        prev_ver = get_latest_version(existing_doc_id)
        return {
            "doc_id": existing_doc_id,
            "version_id": prev_ver["version_id"] if prev_ver else None,
            "version_number": prev_ver["version_number"] if prev_ver else 1,
            "chunks_count": len(prev_ver["chunks"]) if prev_ver else 0,
            "change_event": None,
            "impacted_documents": [],
            "already_exists": True
        }

    # 2. Ensure the document container exists in PostgreSQL (with dedup)
    final_doc_id = save_document_to_postgres(doc_id, title, owner, file_hash=file_hash)

    # 3. Get latest version of this document (if any)
    prev_ver = get_latest_version(final_doc_id)
    next_ver_num = 1 if not prev_ver else prev_ver["version_number"] + 1

    # 4. Chunk the text
    chunks = split_into_chunks(raw_text)
    chunked_text_summary = [
        {"index": c["chunk_index"], "length": len(c["content"])} for c in chunks
    ]

    # 4. Diff against previous version and fetch embeddings (reuses unchanged hashes)
    old_chunks = prev_ver["chunks"] if prev_ver else []
    chunks_with_embeddings = diff_and_embed_chunks(chunks, old_chunks)

    # 5. Insert version row and chunks into Postgres
    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO document_versions (version_number, doc_id, raw_text, chunked_text)
                VALUES (%s, %s, %s, %s)
                RETURNING version_id
                """,
                (next_ver_num, final_doc_id, raw_text, json.dumps(chunked_text_summary))
            )
            new_version_id = cur.fetchone()[0]

            # 6. Insert chunks — store embedding as JSON list (JSONB-safe), NOT str()
            chunk_inserted_ids = []
            for chunk in chunks_with_embeddings:
                emb = chunk.get("embedding", [])
                # Ensure it's a plain Python list (numpy arrays are not JSON-serialisable)
                if hasattr(emb, "tolist"):
                    emb = emb.tolist()
                cur.execute(
                    """
                    INSERT INTO chunks (version_id, chunk_index, content, embedding)
                    VALUES (%s, %s, %s, %s)
                    RETURNING chunk_id
                    """,
                    (new_version_id, chunk["chunk_index"], chunk["content"], json.dumps(emb))
                )
                chunk_inserted_ids.append(str(cur.fetchone()[0]))

            # Assign DB chunk IDs back to in-memory list
            for i, chunk_id in enumerate(chunk_inserted_ids):
                chunks_with_embeddings[i]["chunk_id"] = chunk_id

            # 7. Severity classification if previous version exists
            change_event = None
            if prev_ver:
                change_class, change_summary = classify_change_severity(
                    prev_ver["raw_text"],
                    raw_text,
                    old_chunks,
                    chunks_with_embeddings
                )
                cur.execute(
                    """
                    INSERT INTO change_events (doc_id, from_version, to_version, change_class, change_summary)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING id, change_class, change_summary
                    """,
                    (final_doc_id, prev_ver["version_number"], next_ver_num, change_class, change_summary)
                )
                evt_row = cur.fetchone()
                change_event = {
                    "event_id": evt_row[0],
                    "change_class": evt_row[1],
                    "change_summary": evt_row[2]
                }

            conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        release_pg_connection(conn)

    # 8. Sync to Neo4j (stubbed — Neo4j replaced by doc_graph PG table)
    sync_version_to_neo4j(final_doc_id, title, next_ver_num, raw_text, chunks_with_embeddings, prev_ver)

    # 9. Citation & Semantic overlap passes
    try:
        run_citation_extraction(final_doc_id, next_ver_num, chunks_with_embeddings)
    except Exception as e:
        print(f"Error during citation extraction: {e}")

    try:
        run_semantic_proximity_check(final_doc_id, next_ver_num, chunks_with_embeddings)
    except Exception as e:
        print(f"Error during semantic proximity check: {e}")

    # 10. Impact Engine (only for significant changes)
    impacted_docs = []
    if change_event and change_event["change_class"] >= 2:
        try:
            impacted_docs = evaluate_impact(final_doc_id, next_ver_num, change_event["change_class"])
        except Exception as e:
            print(f"Error during impact evaluation: {e}")

    return {
        "doc_id": final_doc_id,
        "version_id": new_version_id,
        "version_number": next_ver_num,
        "chunks_count": len(chunks_with_embeddings),
        "change_event": change_event,
        "impacted_documents": impacted_docs
    }


def sync_version_to_neo4j(
    doc_id: str,
    title: str,
    version_number: int,
    raw_text: str,
    chunks: list[dict],
    prev_ver: dict
):
    """
    Syncs to Neo4j if available. Neo4j is stubbed in this deployment
    (replaced by doc_graph PostgreSQL table) — exits immediately if driver is None.
    """
    driver = get_neo4j_driver()
    if not driver:
        print("  [Neo4j] Neo4j is disabled or stubbed. Skipping Neo4j sync.")
        return
    doc_node_id = f"{doc_id}_v{version_number}"
    headings = extract_sections(raw_text)

    with driver.session() as session:
        session.run(
            """
            MERGE (d:Document {id: $doc_node_id})
            SET d.doc_id = $doc_id,
                d.title = $title,
                d.version = $version
            """,
            doc_node_id=doc_node_id, doc_id=doc_id,
            title=title, version=version_number
        )
        if prev_ver:
            prev_doc_node_id = f"{doc_id}_v{prev_ver['version_number']}"
            session.run(
                """
                MATCH (new_d:Document {id: $new_id})
                MATCH (old_d:Document {id: $old_id})
                MERGE (new_d)-[:SUPERSEDES]->(old_d)
                """,
                new_id=doc_node_id, old_id=prev_doc_node_id
            )
        for h in headings:
            sec_id = f"{doc_node_id}_s_{h['line_no']}"
            session.run(
                """
                MERGE (s:Section {id: $sec_id})
                SET s.heading = $heading, s.level = $level, s.line_no = $line_no
                WITH s
                MATCH (d:Document {id: $doc_node_id})
                MERGE (s)-[:BELONGS_TO]->(d)
                """,
                sec_id=sec_id, heading=h["heading"],
                level=h["level"], line_no=h["line_no"],
                doc_node_id=doc_node_id
            )
        for c in chunks:
            chunk_node_id = c["chunk_id"]
            session.run(
                """
                MERGE (ch:Chunk {id: $chunk_id})
                SET ch.chunk_index = $chunk_index,
                    ch.content_preview = $preview
                WITH ch
                MATCH (d:Document {id: $doc_node_id})
                MERGE (ch)-[:BELONGS_TO]->(d)
                """,
                chunk_id=chunk_node_id, chunk_index=c["chunk_index"],
                preview=c["content"][:100] + "...",
                doc_node_id=doc_node_id
            )
            for h in headings:
                if h["heading"] in c["content"]:
                    sec_id = f"{doc_node_id}_s_{h['line_no']}"
                    session.run(
                        """
                        MATCH (ch:Chunk {id: $chunk_id})
                        MATCH (s:Section {id: $sec_id})
                        MERGE (ch)-[:BELONGS_TO]->(s)
                        """,
                        chunk_id=chunk_node_id, sec_id=sec_id
                    )


def get_document_version_history(doc_id: str) -> list[dict]:
    """
    Returns the complete version history of a document with change event details.
    """
    conn = get_pg_connection()
    try:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT
                    dv.version_id,
                    dv.version_number,
                    dv.uploaded_at,
                    LENGTH(dv.raw_text) as doc_size_chars,
                    ce.change_class,
                    ce.change_summary
                FROM document_versions dv
                LEFT JOIN change_events ce
                  ON dv.doc_id = ce.doc_id
                 AND dv.version_number = ce.to_version
                WHERE dv.doc_id = %s
                ORDER BY dv.version_number DESC
                """,
                (doc_id,)
            )
            rows = cur.fetchall()
            return [dict(r) for r in rows]
    finally:
        release_pg_connection(conn)


def get_all_overlaps_grouped() -> dict:
    """
    Retrieves all overlap records from PostgreSQL, grouped by overlap_class.
    """
    conn = get_pg_connection()
    try:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT
                    o.id,
                    o.doc_id_a,
                    o.doc_id_b,
                    da.title as title_a,
                    db.title as title_b,
                    o.overlap_class,
                    o.matched_chunk_ids,
                    o.detected_at
                FROM overlap_records o
                JOIN documents da ON o.doc_id_a = da.id
                JOIN documents db ON o.doc_id_b = db.id
                ORDER BY o.detected_at DESC
                """
            )
            rows = cur.fetchall()
            grouped = {
                "DUPLICATE": [],
                "PARTIAL_OVERLAP": [],
                "CONFLICT": [],
                "SUPERSEDED": []
            }
            for row in rows:
                cls = row["overlap_class"]
                if cls in grouped:
                    row["doc_id_a"] = str(row["doc_id_a"])
                    row["doc_id_b"] = str(row["doc_id_b"])
                    row["detected_at"] = row["detected_at"].isoformat()
                    grouped[cls].append(dict(row))
            return grouped
    finally:
        release_pg_connection(conn)
