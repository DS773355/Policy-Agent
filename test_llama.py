import requests
import sys

res = requests.post(
    "http://127.0.0.1:8080/v1/chat/completions",
    json={
        "model": "phi3",
        "messages": [{"role": "user", "content": "Hello!"}],
        "temperature": 0.0,
        "stream": True
    },
    stream=True
)
print("Status:", res.status_code)
for line in res.iter_lines():
    print("LINE:", line)
