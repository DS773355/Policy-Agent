"""
End-to-end chat pipeline test.
Tests: auth -> RAG retrieval -> llama streaming -> full response assembly.
"""
import requests
import time

API_URL = "http://127.0.0.1:8000"

def test_e2e():
    # 1. Login
    print("=== 1. Auth ===")
    r = requests.post(f"{API_URL}/api/auth/login", json={"username": "editor", "password": "EditorPass123!"})
    assert r.status_code == 200, f"Login failed: {r.text}"
    token = r.json()["access_token"]
    print(f"  Login OK — token: {token[:40]}...")

    headers = {"Authorization": f"Bearer {token}"}

    # 2. Health check
    print("\n=== 2. Health ===")
    h = requests.get(f"{API_URL}/api/health", headers=headers)
    print(f"  {h.json()}")

    # 3. List documents
    print("\n=== 3. Documents ===")
    d = requests.get(f"{API_URL}/api/documents", headers=headers)
    docs = d.json().get("documents", [])
    print(f"  {len(docs)} document(s) in DB:")
    for doc in docs:
        print(f"    - {doc['title']}")

    # 4. Chat streaming
    print("\n=== 4. Chat stream ===")
    payload = {"session_id": "e2e-test-session", "query_text": "What are the password complexity requirements?"}
    print(f"  Query: {payload['query_text']}")
    start = time.time()

    full_text = ""
    with requests.post(f"{API_URL}/api/chat/query", json=payload, headers=headers, stream=True, timeout=180) as res:
        print(f"  HTTP {res.status_code} — first byte in {time.time()-start:.2f}s")
        assert res.status_code == 200

        for chunk in res.iter_content(chunk_size=64):
            if chunk:
                decoded = chunk.decode("utf-8", errors="replace")
                full_text += decoded

    elapsed = time.time() - start
    print(f"\n  --- Full response ({len(full_text)} chars, {elapsed:.1f}s) ---")
    print(full_text[:1200])
    if len(full_text) > 1200:
        print(f"  ... [{len(full_text) - 1200} more chars]")

    # 5. Validate
    print("\n=== 5. Validation ===")
    has_query_id = "[QUERY_ID:" in full_text
    is_offline_fallback = "Offline Fallback" in full_text
    is_no_docs = "could not find" in full_text.lower()
    has_content = len(full_text.strip()) > 50

    print(f"  QUERY_ID header present: {has_query_id}")
    print(f"  Offline fallback triggered: {is_offline_fallback}")
    print(f"  No-docs message: {is_no_docs}")
    print(f"  Has meaningful content: {has_content}")

    if is_offline_fallback:
        print("\n  [WARNING] llama.cpp endpoint is offline - response is a fallback listing.")
    elif is_no_docs:
        print("\n  [WARNING] No matching chunks retrieved - check if documents are ingested correctly.")
    elif has_content and not is_offline_fallback:
        print("\n  [SUCCESS] Pipeline working: streamed LLM response received!")
    else:
        print("\n  [ERROR] Something went wrong - check logs.")

if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except AttributeError:
        pass
    test_e2e()
