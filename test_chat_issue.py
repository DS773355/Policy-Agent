import requests
import json
import time

def test_chat():
    api_url = "http://127.0.0.1:8000"
    print("1. Logging in...")
    login_res = requests.post(f"{api_url}/api/auth/login", json={"username": "editor", "password": "EditorPass123!"})
    if not login_res.ok:
        print("Login failed:", login_res.text)
        return
    token = login_res.json()["access_token"]
    print("Token obtained.")

    print("2. Calling chat endpoint...")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {"session_id": "test-session", "query_text": "what is tender"}
    
    start_time = time.time()
    try:
        print("Making POST request...")
        res = requests.post(f"{api_url}/api/chat/query", json=payload, headers=headers, stream=True, timeout=120)
        print("Response received in", time.time() - start_time, "seconds. Status:", res.status_code)
        print("Starting to read chunks...")
        for chunk in res.iter_content(chunk_size=1):
            if chunk:
                print(repr(chunk.decode("utf-8", errors="replace")), end="", flush=True)
        print("\nFinished stream.")
    except Exception as e:
        print("\nError during request:", str(e))

if __name__ == "__main__":
    test_chat()
