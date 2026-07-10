"""
seed.py — One-time database seeder for Policy AGENT.

Loads 3 sample compliance policy documents with pre-built cross-references,
creates initial users, and populates dummy episodic memory rows so the
frontend has content on first launch.

Usage:
    python backend/seed.py                # from repo root
    python seed.py                        # from backend/ directory
"""
import sys
import os
import uuid
import json
import datetime

# Allow running from repo root or from backend/
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from psycopg.rows import dict_row
from database import get_pg_connection, release_pg_connection, get_neo4j_driver
from services.auth import hash_password
from services.embedding_service import get_embeddings_batch
from services.chunker import split_into_chunks

# ─────────────────────────────────────────────────────────────────────────────
# Sample Policy Documents
# ─────────────────────────────────────────────────────────────────────────────

DOCUMENTS = [
    {
        "id": str(uuid.uuid4()),
        "title": "Access Control Policy",
        "owner": "security-team",
        "text": """
ACCESS CONTROL POLICY
Version 1.0 | Effective Date: 2026-01-01

1. PURPOSE
This policy establishes requirements for managing access to information systems and data assets to protect confidentiality, integrity, and availability.

2. SCOPE
This policy applies to all employees, contractors, and third parties who access organizational information systems.

3. USER ACCOUNT MANAGEMENT
3.1 All user accounts must be formally requested and approved by the system owner.
3.2 User accounts must be reviewed quarterly and removed within 24 hours of termination.
3.3 Shared accounts are prohibited unless technically unavoidable and explicitly approved.

4. AUTHENTICATION REQUIREMENTS
4.1 Multi-factor authentication (MFA) is mandatory for all privileged accounts and remote access.
4.2 Passwords must comply with the Password Security Standard, Section 2.
4.3 Default passwords must be changed before any system is placed into production.

5. PRIVILEGED ACCESS
5.1 Privileged access must be granted on a least-privilege basis.
5.2 Privileged account usage must be logged and reviewed monthly.
5.3 Jump servers or privileged access workstations (PAWs) must be used for administrative tasks.

6. ACCESS REVIEW
6.1 Access rights must be reviewed every 90 days by the system owner.
6.2 Any access rights no longer required must be revoked immediately.
6.3 Results of access reviews must be documented and retained for 2 years.

7. REMOTE ACCESS
7.1 Remote access is permitted only through approved VPN solutions.
7.2 All remote sessions must enforce MFA as described in Section 4.
7.3 Remote access privileges must be reviewed monthly.

8. VIOLATIONS
Violations of this policy may result in disciplinary action up to and including termination.

9. RELATED DOCUMENTS
- Password Security Standard
- Data Classification Standard
- Incident Response Policy
""",
    },
    {
        "id": str(uuid.uuid4()),
        "title": "Password Security Standard",
        "owner": "security-team",
        "text": """
PASSWORD SECURITY STANDARD
Version 2.1 | Effective Date: 2026-02-01

1. PURPOSE
This standard defines the minimum technical requirements for passwords used to protect access to information systems. It supplements the Access Control Policy.

2. PASSWORD COMPLEXITY REQUIREMENTS
2.1 Minimum length: 14 characters for standard accounts; 20 characters for privileged accounts.
2.2 Passwords must contain at least one character from each of the following categories:
    - Uppercase letters (A-Z)
    - Lowercase letters (a-z)
    - Digits (0-9)
    - Special characters (! @ # $ % ^ & *)
2.3 Passwords must not contain the user's name, username, or email address.
2.4 Passwords must not be one of the organization's last 12 used passwords.

3. PASSWORD EXPIRY
3.1 Standard user passwords expire every 90 days.
3.2 Privileged account passwords expire every 60 days.
3.3 Service account passwords expire every 365 days and must be stored in the approved secrets manager.

4. MULTI-FACTOR AUTHENTICATION
4.1 MFA is required for all privileged access as specified in the Access Control Policy, Section 4.
4.2 Approved MFA methods include: hardware security keys (FIDO2), TOTP authenticator apps.
4.3 SMS-based OTP is not approved as a primary MFA factor due to SIM-swapping risk.

5. PASSWORD STORAGE
5.1 Passwords must never be stored in plaintext.
5.2 Approved hashing algorithms: bcrypt (cost factor ≥ 12), Argon2id.
5.3 Application secrets must be managed through an approved secrets manager, not hardcoded.

6. PASSWORD TRANSMISSION
6.1 Passwords must only be transmitted over encrypted channels (TLS 1.2 or higher).
6.2 Passwords must never be sent via email, chat, or any unencrypted medium.

7. RELATED DOCUMENTS
- Access Control Policy (see Section 3 and Section 4 for account management requirements)
- Data Classification Standard
""",
    },
    {
        "id": str(uuid.uuid4()),
        "title": "Data Classification Standard",
        "owner": "data-governance",
        "text": """
DATA CLASSIFICATION STANDARD
Version 1.3 | Effective Date: 2026-03-01

1. PURPOSE
This standard establishes a consistent framework for classifying organizational data to ensure appropriate protection commensurate with sensitivity and business impact.

2. SCOPE
This standard applies to all data created, processed, stored, or transmitted by the organization and any authorized third parties.

3. DATA CLASSIFICATION LEVELS
3.1 PUBLIC
    - Data intended for public release with no sensitivity.
    - No access controls required beyond standard network perimeter.
    - Examples: Marketing materials, public product documentation.

3.2 INTERNAL
    - Data for internal use; disclosure could cause minor business impact.
    - Standard access controls as defined in the Access Control Policy.
    - Examples: Internal procedures, employee directory.

3.3 CONFIDENTIAL
    - Sensitive business data; unauthorized disclosure could cause significant harm.
    - Requires encryption at rest and in transit, and MFA for access per Password Security Standard Section 4.
    - Examples: Customer PII, financial reports, contracts.

3.4 RESTRICTED
    - Highly sensitive data; unauthorized disclosure could cause severe harm or regulatory penalty.
    - Requires privileged access controls per Access Control Policy Section 5.
    - Must be stored in isolated, audited environments.
    - Examples: Cryptographic keys, source code of security systems, regulated health data.

4. DATA HANDLING REQUIREMENTS
4.1 All data must be classified at point of creation and labeled appropriately.
4.2 Data must be handled in accordance with its classification level throughout its lifecycle.
4.3 Reclassification must be approved by the data owner and documented.

5. DATA RETENTION AND DISPOSAL
5.1 Data must be retained according to the organization's Data Retention Schedule.
5.2 CONFIDENTIAL and RESTRICTED data must be securely deleted (NIST SP 800-88 guidelines) when no longer needed.
5.3 Physical media containing RESTRICTED data must be destroyed by an approved third-party vendor.

6. ACCESS TO CLASSIFIED DATA
6.1 Access to CONFIDENTIAL data must follow the quarterly access review process in Access Control Policy Section 6.
6.2 Access to RESTRICTED data requires additional approval from the CISO and must be logged.

7. RELATED DOCUMENTS
- Access Control Policy (Section 3, 5, 6 — account and privileged access requirements)
- Password Security Standard (Section 4 — MFA requirements for Confidential data)
- Incident Response Policy
""",
    },
]

# ─────────────────────────────────────────────────────────────────────────────
# Default Users
# ─────────────────────────────────────────────────────────────────────────────

USERS = [
    {"username": "admin",  "password": "AdminPass123!", "role": "admin"},
    {"username": "editor", "password": "EditorPass123!", "role": "editor"},
    {"username": "viewer", "password": "ViewerPass123!", "role": "viewer"},
]

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def seed_users(conn):
    print("  [users] Seeding default user accounts...")
    with conn.cursor() as cur:
        for u in USERS:
            pw_hash = hash_password(u["password"])
            cur.execute(
                """
                INSERT INTO users (username, password_hash, role)
                VALUES (%s, %s, %s)
                ON CONFLICT (username) DO UPDATE
                  SET password_hash = EXCLUDED.password_hash,
                      role = EXCLUDED.role
                """,
                (u["username"], pw_hash, u["role"]),
            )
    conn.commit()
    print(f"    ✓ {len(USERS)} users created/updated.")


def seed_documents(conn):
    print("  [documents] Seeding sample policy documents...")
    version_ids = {}

    for doc in DOCUMENTS:
        doc_id = doc["id"]

        # Upsert document row
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO documents (id, title, owner)
                VALUES (%s, %s, %s)
                ON CONFLICT (id) DO NOTHING
                """,
                (doc_id, doc["title"], doc["owner"]),
            )

        # Create version 1
        version_id = str(uuid.uuid4())
        raw_text = doc["text"].strip()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO document_versions (version_id, doc_id, version_number, raw_text)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                RETURNING version_id
                """,
                (version_id, doc_id, 1, raw_text),
            )
            row = cur.fetchone()
            if row is None:
                # already exists — look it up
                cur.execute(
                    "SELECT version_id FROM document_versions WHERE doc_id=%s AND version_number=1",
                    (doc_id,)
                )
                row = cur.fetchone()
            version_id = str(row[0])

        version_ids[doc_id] = version_id

        # Chunk the text
        chunks = split_into_chunks(raw_text)
        texts = [c["text"] for c in chunks]

        print(f"    Embedding {len(chunks)} chunks for '{doc['title']}'...")
        embeddings = get_embeddings_batch(texts)

        # Insert chunks
        with conn.cursor() as cur:
            for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
                chunk_id = str(uuid.uuid4())
                cur.execute(
                    """
                    INSERT INTO chunks (chunk_id, version_id, chunk_index, content, embedding)
                    VALUES (%s, %s, %s, %s, %s::vector)
                    ON CONFLICT DO NOTHING
                    """,
                    (chunk_id, version_id, i, chunk["text"], str(emb)),
                )
        conn.commit()
        print(f"    ✓ '{doc['title']}' → {len(chunks)} chunks stored.")

    return version_ids


def seed_neo4j_citations(version_ids):
    """
    Create Document nodes and CITES edges in Neo4j matching the
    cross-references in the sample documents.
    """
    print("  [neo4j] Creating document nodes and citation edges...")
    driver = get_neo4j_driver()
    if not driver:
        print("  [neo4j] Neo4j is disabled or stubbed. Skipping Neo4j citation seeding.")
        return

    # Doc lookup by title
    by_title = {d["title"]: d["id"] for d in DOCUMENTS}

    # Define explicit citations (source_title → [target_title, ...])
    citations = {
        "Password Security Standard": ["Access Control Policy"],
        "Data Classification Standard": ["Access Control Policy", "Password Security Standard"],
    }

    with driver.session() as session:
        # Create all Document nodes
        for doc in DOCUMENTS:
            doc_node_id = f"{doc['id']}_v1"
            session.run(
                """
                MERGE (d:Document {id: $node_id})
                ON CREATE SET d.doc_id = $doc_id, d.title = $title
                """,
                node_id=doc_node_id, doc_id=doc["id"], title=doc["title"],
            )

        # Create CITES edges
        for src_title, targets in citations.items():
            src_id = by_title[src_title]
            src_node_id = f"{src_id}_v1"
            for tgt_title in targets:
                tgt_id = by_title[tgt_title]
                tgt_node_id = f"{tgt_id}_v1"
                session.run(
                    """
                    MATCH (src:Document {id: $src_node_id})
                    MATCH (tgt:Document {id: $tgt_node_id})
                    MERGE (src)-[:CITES]->(tgt)
                    """,
                    src_node_id=src_node_id, tgt_node_id=tgt_node_id,
                )
                print(f"    ✓ {src_title} --CITES--> {tgt_title}")


def seed_episodic_memory(conn):
    """Insert representative episodic memory rows for a populated UI."""
    print("  [memory] Seeding dummy episodic memory rows...")
    now = datetime.datetime.utcnow()

    queries = [
        {
            "query_text": "What are the password complexity requirements?",
            "response_text": "Passwords must be at least 14 characters and contain uppercase, lowercase, digits, and special characters. See Password Security Standard Section 2.",
            "user_rating": 5,
            "session_id": "seed-session-001",
            "created_at": now - datetime.timedelta(days=3),
        },
        {
            "query_text": "Who needs multi-factor authentication?",
            "response_text": "MFA is mandatory for all privileged accounts and remote access per Access Control Policy Section 4.",
            "user_rating": 4,
            "session_id": "seed-session-001",
            "created_at": now - datetime.timedelta(days=2),
        },
        {
            "query_text": "How often must access rights be reviewed?",
            "response_text": "Access rights must be reviewed every 90 days per Access Control Policy Section 6.",
            "user_rating": 5,
            "session_id": "seed-session-002",
            "created_at": now - datetime.timedelta(days=1),
        },
        {
            "query_text": "What classification level applies to customer PII?",
            "response_text": "Customer PII is CONFIDENTIAL under the Data Classification Standard Section 3.3.",
            "user_rating": 5,
            "session_id": "seed-session-002",
            "created_at": now - datetime.timedelta(hours=12),
        },
        {
            "query_text": "How must RESTRICTED data be disposed of?",
            "response_text": "Physical media with RESTRICTED data must be destroyed by an approved third-party vendor per Data Classification Standard Section 5.3.",
            "user_rating": 4,
            "session_id": "seed-session-003",
            "created_at": now - datetime.timedelta(hours=6),
        },
    ]

    with conn.cursor() as cur:
        for q in queries:
            emb = get_embeddings_batch([q["query_text"]])[0]
            cur.execute(
                """
                INSERT INTO memory_episodic
                    (query_text, response_text, user_rating, session_id, retrieved_chunk_ids, created_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (
                    q["query_text"],
                    q["response_text"],
                    q["user_rating"],
                    q["session_id"],
                    json.dumps([]),
                    q["created_at"],
                ),
            )
    conn.commit()
    print(f"    ✓ {len(queries)} episodic memory rows inserted.")


def seed_change_event(conn):
    """Create a sample change event so suggestions appear in the UI."""
    print("  [change_events] Seeding sample change event...")
    doc_id = DOCUMENTS[2]["id"]  # Data Classification Standard
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO change_events
                (doc_id, old_version, new_version, change_class, summary, triggered_at)
            VALUES (%s, %s, %s, %s, %s, NOW())
            ON CONFLICT DO NOTHING
            """,
            (
                doc_id, 0, 1, 1,
                "Initial ingestion of Data Classification Standard. Section 3 defines four classification levels.",
            ),
        )
    conn.commit()
    print("    ✓ Change event seeded for 'Data Classification Standard'.")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("\n=== Policy AGENT Seeder ===\n")

    conn = get_pg_connection()
    try:
        seed_users(conn)
        version_ids = seed_documents(conn)
        seed_neo4j_citations(version_ids)
        seed_episodic_memory(conn)
        seed_change_event(conn)
    finally:
        release_pg_connection(conn)

    print("\n✅  Seeding complete! You can now start the backend and explore the UI.\n")
    print("Default credentials:")
    for u in USERS:
        print(f"  {u['role']:8s}  username={u['username']:8s}  password={u['password']}")
    print()


if __name__ == "__main__":
    main()
