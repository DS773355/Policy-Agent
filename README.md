# Policy AGENT

> An enterprise compliance intelligence system powered by hybrid RAG, a Neo4j dependency graph, and a three-tier memory architecture.

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────────┐
│                         Browser (React)                          │
│  Sidebar: suggestion chips │ Chat panel │ Impact graph drawer    │
└───────────────────┬──────────────────────────────────────────────┘
                    │  HTTP / SSE
┌───────────────────▼──────────────────────────────────────────────┐
│                    Nginx Reverse Proxy (:80)                      │
│  /              → React static build                             │
│  /api/          → FastAPI backend (:8000)                        │
└───────────────────┬──────────────────────────────────────────────┘
                    │
┌───────────────────▼──────────────────────────────────────────────┐
│                    FastAPI Backend                                │
│                                                                   │
│  POST /api/documents/upload   ─── Document Ingestion Pipeline    │
│    │                                                              │
│    ├─ Extract text (PDF/DOCX/TXT)                                │
│    ├─ Chunk (~500 tokens, 50 overlap)                            │
│    ├─ Diff against previous version (hash reuse)                 │
│    ├─ Embed via vLLM                                             │
│    ├─ Classify change severity (Class 0–4)                       │
│    ├─ Extract citations → Neo4j CITES edges                      │
│    ├─ Semantic proximity → Neo4j OVERLAPS_WITH edges             │
│    └─ If Class ≥ 2: run Impact Engine (3-hop traversal)         │
│                                                                   │
│  POST /api/chat/query         ─── Hybrid RAG Pipeline            │
│    │                                                              │
│    ├─ Check Frozen Memory (pgvector, similarity > 0.95)          │
│    ├─ Retrieve: Semantic (pgvector) + Keyword (FTS)              │
│    ├─ RRF Blend (top-30) + Local Re-Rank (top-15)               │
│    ├─ Context Tag ([Changed] [Affected] [Overlapping])           │
│    └─ Stream response from vLLM                                  │
│                                                                   │
│  POST /api/chat/feedback      ─── Memory Feedback Loop           │
│    └─ Rating ≤ 2 + note → auto-corrected re-query               │
└────┬──────────────┬───────────────┬──────────────────────────────┘
     │              │               │
┌────▼────┐  ┌──────▼──────┐  ┌───▼──────┐   ┌─────────────────┐
│PostgreSQL│  │    Neo4j    │  │  Redis   │   │   vLLM Server   │
│+pgvector │  │(Graph/deps) │  │(working  │   │(Qwen-2.5-32B   │
│chunks    │  │CITES edges  │  │ memory,  │   │ embeddings +    │
│memory_*  │  │OVERLAPS_WITH│  │ sessions)│   │ generation)     │
└──────────┘  └─────────────┘  └──────────┘   └─────────────────┘
```

---

## Local Development Setup

### Prerequisites

- Docker Desktop (or Podman) with Compose V2
- Python 3.11+
- Node.js 20+ (for frontend)
- An NVIDIA GPU with ≥ 40 GB VRAM for the vLLM server (optional — falls back to deterministic mock embeddings)

### 1. Clone & configure environment

```bash
git clone <repo-url>
cd "Policy AGENT"
cp .env.example .env
# Edit .env with your secrets (see Environment Variables below)
```

### 2. Start infrastructure services

```bash
docker compose -f infra/docker-compose.yml up -d postgres neo4j redis
```

Wait for all three to be healthy:

```bash
docker compose -f infra/docker-compose.yml ps
```

### 3. Create and activate virtual environment

```bash
python -m venv venv
# Windows:
.\venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate

pip install -r backend/requirements.txt
```

### 4. Initialise the database schema

```bash
# From repo root:
python backend/db_init.py
```

### 5. Seed sample data (optional but recommended)

```bash
python backend/seed.py
```

This loads 3 sample compliance policies, creates cross-reference edges in Neo4j, seeds default user accounts, and inserts dummy episodic memory rows so the UI sidebar is populated on first launch.

**Default credentials after seeding:**

| Role   | Username | Password       |
|--------|----------|----------------|
| admin  | admin    | AdminPass123!  |
| editor | editor   | EditorPass123! |
| viewer | viewer   | ViewerPass123! |

### 6. Start the FastAPI backend

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000 --app-dir backend
```

### 7. Start the React frontend

```bash
cd frontend
npm install
npm run dev
```

Open [http://localhost:5173](http://localhost:5173).

---

## Environment Variable Reference

Copy `.env.example` to `.env` and customise the following:

| Variable | Default | Description |
|---|---|---|
| `POSTGRES_DB` | `policy_db` | PostgreSQL database name |
| `POSTGRES_USER` | `postgres` | PostgreSQL username |
| `POSTGRES_PASSWORD` | `postgres_password` | PostgreSQL password |
| `POSTGRES_HOST` | `localhost` | PostgreSQL host |
| `POSTGRES_PORT` | `5432` | PostgreSQL port |
| `NEO4J_URI` | `bolt://localhost:7687` | Neo4j Bolt connection URI |
| `NEO4J_USER` | `neo4j` | Neo4j username |
| `NEO4J_PASSWORD` | `neo4j_password` | Neo4j password |
| `REDIS_HOST` | `localhost` | Redis host |
| `REDIS_PORT` | `6379` | Redis port |
| `VLLM_API_URL` | `http://localhost:8000/v1` | Base URL of the vLLM OpenAI-compatible API |
| `JWT_SECRET_KEY` | *(change me)* | JWT signing secret — **must be changed in production** |
| `IMPACT_SCORE_THRESHOLD` | `0.4` | Minimum impact score to add a document to the review workspace |
| `OVERLAP_SIMILARITY_THRESHOLD` | `0.88` | Cosine similarity cutoff to create `OVERLAPS_WITH` Neo4j edges |
| `FROZEN_MEMORY_SIMILARITY_THRESHOLD` | `0.95` | Similarity cutoff to serve a frozen memory cache hit |
| `DBSCAN_EPS` | `0.15` | Max cosine distance between points in the same DBSCAN cluster |
| `CONSOLIDATION_MIN_SAMPLES` | `2` | Minimum cluster size to promote to frozen memory |
| `RERANK_TOP_K` | `15` | Number of chunks returned after local re-ranking |

---

## How to Upload the First Document

### Via the UI

1. Log in as `editor` or `admin`.
2. Click **"Upload Document"** in the left sidebar.
3. Choose a PDF or DOCX file, enter a title and owner, and click **"Upload"**.
4. The system will chunk, embed, diff (if a previous version exists), classify the change, and run the impact engine automatically.

### Via the API

```bash
curl -X POST http://localhost:8000/api/documents/upload \
  -H "Authorization: Bearer <token>" \
  -F "file=@./my_policy.pdf" \
  -F "title=My Policy" \
  -F "owner=compliance-team"
```

To upload a **new version** of an existing document, include the `doc_id` form field:

```bash
curl -X POST http://localhost:8000/api/documents/upload \
  -H "Authorization: Bearer <token>" \
  -F "file=@./my_policy_v2.pdf" \
  -F "title=My Policy" \
  -F "owner=compliance-team" \
  -F "doc_id=<uuid-from-first-upload>"
```

---

## How to Run the Memory Consolidation Job Manually

### Via the API (admin only)

```bash
curl -X POST http://localhost:8000/api/admin/consolidate \
  -H "Authorization: Bearer <admin-token>"
```

### Directly from Python

```bash
cd backend
python -c "from services.memory_service import consolidate_memories; consolidate_memories()"
```

The nightly consolidation job also runs automatically every 24 hours via the background scheduler started at application startup.

---

## Running Tests

### Unit + Integration tests (42 tests, no live infrastructure needed)

```bash
pytest backend/tests/ -o pythonpath=backend -v
```

### End-to-end pipeline validation tests

```bash
pytest backend/tests/test_e2e.py -o pythonpath=backend -v -s
```

The `-s` flag prints the step-by-step ✓ confirmations for each pipeline stage.

---

## Production Deployment

### Build the React frontend

```bash
cd frontend && npm run build
```

This outputs static files to `frontend/dist/`, which Nginx serves directly.

### Start all services

```bash
docker compose -f docker-compose.prod.yml up -d
```

Services started:
- `postgres` — PostgreSQL 16 + pgvector (persistent volume: `postgres_data`)
- `neo4j` — Neo4j 5 (persistent volume: `neo4j_data`)
- `redis` — Redis 7 (persistent volume: `redis_data`)
- `vllm` — vLLM inference server (requires NVIDIA GPU)
- `backend` — FastAPI application
- `nginx` — Reverse proxy serving frontend and routing `/api/` to backend

### Run schema init and seed inside the container

```bash
docker compose -f docker-compose.prod.yml exec backend python db_init.py
docker compose -f docker-compose.prod.yml exec backend python seed.py
```

---

## Health Check

```bash
curl http://localhost:8000/api/health
```

Returns a JSON status report for PostgreSQL, Neo4j, Redis, and vLLM.

---

## API Reference Summary

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| `POST` | `/api/auth/login` | — | Authenticate and receive JWT |
| `POST` | `/api/documents/upload` | editor/admin | Upload or version a document |
| `GET` | `/api/documents` | any | List all documents |
| `GET` | `/api/documents/{id}/versions` | any | Version history |
| `GET` | `/api/documents/{id}/impact` | any | 3-hop impact graph |
| `GET` | `/api/documents/overlaps` | any | All overlap records |
| `GET` | `/api/workspace/suggestions` | any | Dynamic sidebar chips |
| `POST` | `/api/chat/query` | any | Streamed RAG query |
| `POST` | `/api/chat/feedback` | any | Rate a response / trigger correction |
| `POST` | `/api/admin/consolidate` | admin | Manually trigger memory consolidation |
| `GET` | `/api/health` | — | Infrastructure health check |
