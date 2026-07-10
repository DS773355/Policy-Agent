"""
Direct upload test — captures the exact error from the ingestion pipeline.
"""
import sys, os
import requests

API_URL = "http://127.0.0.1:8000"

# 1. Login
r = requests.post(f"{API_URL}/api/auth/login", json={"username": "editor", "password": "EditorPass123!"})
if not r.ok:
    print("LOGIN FAILED:", r.text)
    sys.exit(1)
token = r.json()["access_token"]
headers = {"Authorization": f"Bearer {token}"}
print("Login OK")

# 2. Create a minimal test PDF-like text file and upload it
test_content = b"This is a test policy document.\n\nSection 1: Purpose\nThis document defines test procedures."
files = {"file": ("test_policy.txt", test_content, "text/plain")}
data = {"title": "Test Policy", "owner": "test"}

print("Uploading test document...")
res = requests.post(f"{API_URL}/api/documents/upload", files=files, data=data, headers=headers, timeout=60)
print(f"Status: {res.status_code}")
print(f"Response: {res.text}")
