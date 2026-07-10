import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))

import psycopg
from config import settings

conninfo = (
    f"dbname={settings.postgres_db} user={settings.postgres_user} "
    f"password={settings.postgres_password} host={settings.postgres_host} "
    f"port={settings.postgres_port}"
)

with psycopg.connect(conninfo) as conn:
    with conn.cursor() as cur:
        # Find trigger name
        cur.execute("""
            SELECT trigger_name, event_object_table
            FROM information_schema.triggers
            WHERE trigger_schema = 'public'
            ORDER BY event_object_table
        """)
        print("Triggers:", cur.fetchall())

        # Count
        cur.execute("SELECT COUNT(*) FROM documents")
        before = cur.fetchone()[0]
        print(f"Documents before: {before}")

        # Get all triggers on document_versions
        cur.execute("""
            SELECT trigger_name FROM information_schema.triggers
            WHERE event_object_table = 'document_versions' AND trigger_schema = 'public'
        """)
        triggers = [r[0] for r in cur.fetchall()]
        print("document_versions triggers:", triggers)

        # Disable all triggers on document_versions and documents
        for t in triggers:
            cur.execute(f"ALTER TABLE document_versions DISABLE TRIGGER {t}")
            print(f"Disabled trigger: {t}")

        # Delete duplicates — keep one per title (most recently created)
        cur.execute("""
            DELETE FROM documents
            WHERE id NOT IN (
                SELECT DISTINCT ON (title) id
                FROM documents
                ORDER BY title, created_at DESC
            )
        """)
        deleted = cur.rowcount
        print(f"Deleted {deleted} duplicate document(s).")

        # Re-enable triggers
        for t in triggers:
            cur.execute(f"ALTER TABLE document_versions ENABLE TRIGGER {t}")

        cur.execute("SELECT COUNT(*) FROM documents")
        after = cur.fetchone()[0]
        print(f"Documents after: {after}")

        cur.execute("SELECT title FROM documents ORDER BY title")
        for r in cur.fetchall():
            print(f"  - {r[0]}")

    conn.commit()
print("Done.")
