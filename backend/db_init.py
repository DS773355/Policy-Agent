import time
import sys
import psycopg
from psycopg import sql
from database import (
    get_pg_connection,
    release_pg_connection,
    get_redis_client
)
from config import settings
from services.auth import hash_password

# SQL migrations — no pgvector, no Neo4j
SQL_SCHEMA = """
-- Enable UUID generation
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- 1. documents table
CREATE TABLE IF NOT EXISTS documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title VARCHAR(255) NOT NULL,
    owner VARCHAR(255) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    metadata JSONB DEFAULT '{}'::jsonb,
    hash_sha256 VARCHAR(64) UNIQUE
);

-- 2. document_versions table (append-only)
CREATE TABLE IF NOT EXISTS document_versions (
    version_id SERIAL PRIMARY KEY,
    version_number INT NOT NULL,
    doc_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    raw_text TEXT NOT NULL,
    chunked_text JSONB NOT NULL DEFAULT '[]'::jsonb,
    uploaded_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 3. chunks table — embeddings stored as JSONB (no pgvector required)
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    version_id INT NOT NULL REFERENCES document_versions(version_id) ON DELETE CASCADE,
    chunk_index INT NOT NULL,
    content TEXT NOT NULL,
    embedding JSONB NOT NULL DEFAULT '[]'::jsonb
);

-- Index for fast lookup by version
CREATE INDEX IF NOT EXISTS chunks_version_idx ON chunks(version_id);

-- 4. change_events table
CREATE TABLE IF NOT EXISTS change_events (
    id SERIAL PRIMARY KEY,
    doc_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    from_version INT,
    to_version INT NOT NULL,
    change_class INT NOT NULL CHECK (change_class BETWEEN 0 AND 4),
    change_summary TEXT NOT NULL,
    triggered_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 5. overlap_records table
CREATE TABLE IF NOT EXISTS overlap_records (
    id SERIAL PRIMARY KEY,
    doc_id_a UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    doc_id_b UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    overlap_class VARCHAR(50) NOT NULL CHECK (overlap_class IN ('DUPLICATE', 'PARTIAL_OVERLAP', 'CONFLICT', 'SUPERSEDED')),
    matched_chunk_ids JSONB NOT NULL,
    detected_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 6. doc_graph table — replaces Neo4j for citation/overlap graph traversal
CREATE TABLE IF NOT EXISTS doc_graph (
    id SERIAL PRIMARY KEY,
    src_doc_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    tgt_doc_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    edge_type VARCHAR(50) NOT NULL CHECK (edge_type IN ('CITES', 'OVERLAPS_WITH')),
    similarity REAL DEFAULT 1.0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(src_doc_id, tgt_doc_id, edge_type)
);

CREATE INDEX IF NOT EXISTS doc_graph_src_idx ON doc_graph(src_doc_id);
CREATE INDEX IF NOT EXISTS doc_graph_tgt_idx ON doc_graph(tgt_doc_id);

-- 7. memory_episodic table
CREATE TABLE IF NOT EXISTS memory_episodic (
    id SERIAL PRIMARY KEY,
    query_text TEXT NOT NULL,
    retrieved_chunk_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    user_rating INT CHECK (user_rating BETWEEN 1 AND 5),
    correction_note TEXT,
    response_text TEXT,
    session_id VARCHAR(255) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 8. memory_frozen table — embedding stored as JSONB
CREATE TABLE IF NOT EXISTS memory_frozen (
    id SERIAL PRIMARY KEY,
    canonical_query TEXT NOT NULL,
    answer_text TEXT NOT NULL,
    source_chunk_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    consistency_score NUMERIC(5, 2) NOT NULL,
    embedding JSONB NOT NULL DEFAULT '[]'::jsonb,
    promoted_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 9. users table
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    role VARCHAR(50) NOT NULL CHECK (role IN ('viewer', 'editor', 'admin'))
);

-- 10. api_logs table
CREATE TABLE IF NOT EXISTS api_logs (
    id SERIAL PRIMARY KEY,
    user_id INT REFERENCES users(id) ON DELETE SET NULL,
    endpoint VARCHAR(255) NOT NULL,
    latency_ms INT NOT NULL,
    status_code INT NOT NULL,
    timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Trigger: block UPDATE/DELETE on document_versions (append-only)
CREATE OR REPLACE FUNCTION block_update_delete_version()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'UPDATE or DELETE operations are not allowed on the document_versions table.';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_block_version_mutations ON document_versions;
CREATE TRIGGER trigger_block_version_mutations
BEFORE UPDATE OR DELETE ON document_versions
FOR EACH ROW EXECUTE FUNCTION block_update_delete_version();
"""


def verify_postgres(retries=5, delay=3):
    print("Checking PostgreSQL connection...")
    conn = None
    for attempt in range(1, retries + 1):
        try:
            conn = get_pg_connection()
            print("Successfully connected to PostgreSQL.")

            print("Running SQL migrations...")
            with conn.cursor() as cur:
                cur.execute(SQL_SCHEMA)
                # Idempotent column additions for previously-created DBs
                extras = [
                    "ALTER TABLE documents        ADD COLUMN IF NOT EXISTS hash_sha256 VARCHAR(64) UNIQUE;",
                    "ALTER TABLE memory_episodic ADD COLUMN IF NOT EXISTS response_text TEXT;",
                    "ALTER TABLE memory_frozen    ADD COLUMN IF NOT EXISTS embedding JSONB NOT NULL DEFAULT '[]'::jsonb;",
                    "ALTER TABLE chunks           ADD COLUMN IF NOT EXISTS embedding JSONB NOT NULL DEFAULT '[]'::jsonb;",
                ]
                for stmt in extras:
                    try:
                        cur.execute(stmt)
                    except Exception:
                        pass

                # Seed minimal default users if table is empty
                cur.execute("SELECT COUNT(*) FROM users")
                if cur.fetchone()[0] == 0:
                    print("Seeding default users...")
                    seeds = [
                        ("viewer",      "ViewerPass123!",  "viewer"),
                        ("editor",      "EditorPass123!",  "editor"),
                        ("admin",       "AdminPass123!",   "admin"),
                        # legacy names kept for backward compatibility
                        ("viewer_user", "viewer_pass",     "viewer"),
                        ("editor_user", "editor_pass",     "editor"),
                        ("admin_user",  "admin_pass",      "admin"),
                    ]
                    for username, password, role in seeds:
                        p_hash = hash_password(password)
                        cur.execute(
                            "INSERT INTO users (username, password_hash, role) VALUES (%s, %s, %s)",
                            (username, p_hash, role)
                        )
            conn.commit()
            print("SQL migrations applied successfully.")
            return True
        except Exception as e:
            print(f"PostgreSQL connection attempt {attempt} failed: {e}")
            if conn:
                release_pg_connection(conn)
                conn = None
            time.sleep(delay)
    return False


def verify_redis(retries=5, delay=3):
    print("Checking Redis connection...")
    for attempt in range(1, retries + 1):
        try:
            client = get_redis_client()
            client.ping()
            print("Successfully connected to Redis.")
            return True
        except Exception as e:
            print(f"Redis connection attempt {attempt} failed: {e}")
            time.sleep(delay)
    return False


def verify_vllm(retries=1, delay=1):
    """Non-blocking llama.cpp server check — application works without it via mock embeddings."""
    print(f"Checking llama.cpp server (Phi-3) at {settings.vllm_api_url}...")
    try:
        import requests
        url = f"{settings.vllm_api_url}/models"
        response = requests.get(url, timeout=3)
        if response.status_code == 200:
            print("Successfully connected to llama.cpp server (Phi-3).")
            return True
        else:
            print(f"llama.cpp server returned status {response.status_code} — running with mock embeddings.")
            return False
    except Exception as e:
        print(f"llama.cpp server not available ({e}) — running with mock embeddings (offline mode).")
        return False


if __name__ == "__main__":
    print("=== Policy AGENT — Database Initialisation ===\n")
    pg_ok    = verify_postgres()
    redis_ok = verify_redis()
    vllm_ok  = verify_vllm()

    print("\n-- Service Status ----------------------------------")
    print(f"  PostgreSQL : {'[OK]'     if pg_ok    else '[FAILED]'}")
    print(f"  Redis      : {'[OK]'     if redis_ok else '[FAILED]'}")
    print(f"  llama.cpp  : {'[OK]'     if vllm_ok  else '[offline - mock embeddings active]'}")
    print("----------------------------------------------------\n")

    if not pg_ok:
        print("ERROR: PostgreSQL is required. Please start it and retry.")
        sys.exit(1)
    if not redis_ok:
        print("ERROR: Redis is required. Please start it and retry.")
        sys.exit(1)

    print("OK: Initialisation complete.\n")
