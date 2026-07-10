"""
reseed.py — Fixed one-shot seeder for Policy AGENT.
Fixes:
  - Uses JSONB embedding storage (no ::vector cast)
  - Skips Neo4j (offline mode)
  - Uses correct change_events column names (from_version / to_version)
  - Cleans up orphaned documents before re-seeding

Usage (from backend/ directory):
    python reseed.py
"""
import sys
import os
import uuid
import json
import datetime

sys.path.insert(0, os.path.dirname(__file__))

import psycopg
from psycopg.rows import dict_row
from services.auth import hash_password
from services.embedding_service import get_embeddings_batch
from services.chunker import split_into_chunks

PG_DSN = "host=localhost port=5432 dbname=policy_db user=postgres password=postgres_password"

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
5.2 Approved hashing algorithms: bcrypt (cost factor >= 12), Argon2id.
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
- Access Control Policy (Section 3, 5, 6 -- account and privileged access requirements)
- Password Security Standard (Section 4 -- MFA requirements for Confidential data)
- Incident Response Policy
""",
    },
    {
        "id": str(uuid.uuid4()),
        "title": "System Security Policy",
        "owner": "it-operations",
        "text": """
SYSTEM SECURITY POLICY
Version 1.0 | Effective Date: 2026-01-15

1. PURPOSE
This policy defines the security requirements for all organizational information systems, including servers, workstations, network devices, and cloud infrastructure.

2. SCOPE
This policy applies to all IT systems owned or operated by the organization, including on-premises, cloud, and hybrid environments.

3. SYSTEM HARDENING
3.1 All systems must be configured according to approved hardening baselines (CIS Benchmarks or equivalent).
3.2 Unnecessary services, ports, and protocols must be disabled.
3.3 Default credentials must be changed prior to production deployment.
3.4 System configurations must be reviewed and re-validated annually.

4. PATCH MANAGEMENT
4.1 Critical security patches must be applied within 72 hours of vendor release.
4.2 High-severity patches must be applied within 14 days.
4.3 Medium and low severity patches must be applied within 30 days.
4.4 All patches must be tested in a staging environment before production deployment.

5. VULNERABILITY MANAGEMENT
5.1 All systems must be scanned for vulnerabilities at least monthly.
5.2 Critical vulnerabilities must be remediated within 30 days of discovery.
5.3 Vulnerability scan results must be reviewed by the Security Operations team.

6. ENDPOINT SECURITY
6.1 All endpoints must run approved antivirus/EDR software.
6.2 Full disk encryption is mandatory for all laptops and portable devices.
6.3 USB storage is disabled by default; exceptions require written approval.

7. LOGGING AND MONITORING
7.1 All systems must forward logs to the centralized SIEM platform.
7.2 Logs must be retained for a minimum of 12 months.
7.3 Security events must be reviewed daily by the SOC team.

8. BACKUP AND RECOVERY
8.1 All critical systems must be backed up daily.
8.2 Backups must be stored in a geographically separate location.
8.3 Recovery procedures must be tested quarterly.

9. RELATED DOCUMENTS
- Access Control Policy
- Incident Response Policy
- Data Classification Standard
""",
    },
]

USERS = [
    {"username": "admin",  "password": "AdminPass123!",  "role": "admin"},
    {"username": "editor", "password": "EditorPass123!", "role": "editor"},
    {"username": "viewer", "password": "ViewerPass123!", "role": "viewer"},
]


def main():
    print("\n=== Policy AGENT Re-Seeder ===\n")
    conn = psycopg.connect(PG_DSN)

    # ── Clean up orphaned documents (no version record) ──────────────────────
    print("  [cleanup] Removing orphaned documents without versions...")
    with conn.cursor() as cur:
        cur.execute("""
            DELETE FROM documents
            WHERE id NOT IN (SELECT DISTINCT doc_id FROM document_versions)
        """)
        deleted = cur.rowcount
    conn.commit()
    print(f"    - Removed {deleted} orphaned document(s).")

    # ── Users ────────────────────────────────────────────────────────────────
    print("  [users] Seeding default user accounts...")
    with conn.cursor() as cur:
        for u in USERS:
            pw_hash = hash_password(u["password"])
            cur.execute("""
                INSERT INTO users (username, password_hash, role)
                VALUES (%s, %s, %s)
                ON CONFLICT (username) DO UPDATE
                  SET password_hash = EXCLUDED.password_hash,
                      role = EXCLUDED.role
            """, (u["username"], pw_hash, u["role"]))
    conn.commit()
    print(f"    - {len(USERS)} users created/updated.")

    # ── Documents + Chunks ───────────────────────────────────────────────────
    print("  [documents] Seeding sample policy documents...")
    version_ids = {}

    for doc in DOCUMENTS:
        doc_id = doc["id"]
        raw_text = doc["text"].strip()

        # Insert document
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO documents (id, title, owner)
                VALUES (%s, %s, %s)
                ON CONFLICT (id) DO NOTHING
            """, (doc_id, doc["title"], doc["owner"]))

        # Insert version 1
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO document_versions (doc_id, version_number, raw_text)
                VALUES (%s, %s, %s)
                ON CONFLICT DO NOTHING
                RETURNING version_id
            """, (doc_id, 1, raw_text))
            row = cur.fetchone()
            if row is None:
                cur.execute(
                    "SELECT version_id FROM document_versions WHERE doc_id=%s AND version_number=1",
                    (doc_id,)
                )
                row = cur.fetchone()
            version_id = row[0]

        version_ids[doc_id] = version_id

        # Chunk + embed
        chunks = split_into_chunks(raw_text)
        texts = [c["content"] for c in chunks]
        print(f"    Embedding {len(chunks)} chunks for '{doc['title']}'...")
        embeddings = get_embeddings_batch(texts)

        # Insert chunks — JSONB storage (no ::vector cast)
        with conn.cursor() as cur:
            for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
                chunk_id = str(uuid.uuid4())
                cur.execute("""
                    INSERT INTO chunks (chunk_id, version_id, chunk_index, content, embedding)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                """, (chunk_id, version_id, i, chunk["content"], json.dumps(emb)))

        conn.commit()
        print(f"    - '{doc['title']}' -> {len(chunks)} chunks stored.")

    # ── doc_graph citations ──────────────────────────────────────────────────
    print("  [doc_graph] Creating citation edges...")
    by_title = {d["title"]: d["id"] for d in DOCUMENTS}
    citations = {
        "Password Security Standard":  ["Access Control Policy"],
        "Data Classification Standard": ["Access Control Policy", "Password Security Standard"],
        "System Security Policy":       ["Access Control Policy", "Data Classification Standard"],
    }
    with conn.cursor() as cur:
        for src_title, targets in citations.items():
            for tgt_title in targets:
                cur.execute("""
                    INSERT INTO doc_graph (src_doc_id, tgt_doc_id, edge_type, similarity)
                    VALUES (%s, %s, 'CITES', 1.0)
                    ON CONFLICT DO NOTHING
                """, (by_title[src_title], by_title[tgt_title]))
    conn.commit()
    print("    - Citation edges created.")

    # ── Change Events ────────────────────────────────────────────────────────
    print("  [change_events] Seeding sample change events...")
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO change_events (doc_id, from_version, to_version, change_class, change_summary)
            VALUES (%s, NULL, 1, 1, 'Initial ingestion of Data Classification Standard. Section 3 defines four classification levels.')
            ON CONFLICT DO NOTHING
        """, (by_title["Data Classification Standard"],))
    conn.commit()
    print("    - Change event seeded.")

    # ── Episodic Memory ──────────────────────────────────────────────────────
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
            "query_text": "provide me an overview of the system policy",
            "response_text": "The System Security Policy covers: system hardening (CIS benchmarks), patch management (critical patches within 72h), vulnerability management (monthly scans), endpoint security (EDR + full disk encryption), logging/monitoring (SIEM + 12-month retention), and backup/recovery (daily backups, quarterly restore tests). See System Security Policy v1.0.",
            "user_rating": 5,
            "session_id": "seed-session-003",
            "created_at": now - datetime.timedelta(hours=6),
        },
    ]
    with conn.cursor() as cur:
        for q in queries:
            cur.execute("""
                INSERT INTO memory_episodic
                    (query_text, response_text, user_rating, session_id, retrieved_chunk_ids, created_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
            """, (
                q["query_text"], q["response_text"], q["user_rating"],
                q["session_id"], json.dumps([]), q["created_at"]
            ))
    conn.commit()
    print(f"    - {len(queries)} episodic memory rows inserted.")

    conn.close()
    print("\nOK: Re-seeding complete!\n")
    print("Default credentials:")
    for u in USERS:
        print(f"  {u['role']:8s}  username={u['username']:8s}  password={u['password']}")
    print()


if __name__ == "__main__":
    main()
