"""
Direct test of the llama.cpp streaming endpoint — mirrors exactly what rag_service.py does.
Run with: python test_llama_stream.py
"""
import requests
import json
import sys

url = "http://localhost:8080/v1/chat/completions"
payload = {
    "model": "phi3",
    "messages": [
        {"role": "system", "content": "You are an expert policy assistant. Answer concisely."},
        {"role": "user",   "content": "What is a tender? Explain in 2-3 sentences."}
    ],
    "temperature": 0.0,
    "max_tokens": 200,
    "stream": True
}

print("Sending streaming request to llama.cpp...")
try:
    response = requests.post(url, json=payload, stream=True, timeout=120)
    print(f"Status: {response.status_code}")
    if response.status_code == 200:
        full = ""
        for line in response.iter_lines():
            if line:
                decoded = line.decode('utf-8')
                if decoded.startswith("data: "):
                    data_str = decoded[6:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        data_json = json.loads(data_str)
                        token = data_json['choices'][0]['delta'].get('content', '')
                        if token:
                            token = token.replace('\ufffd', '')
                            full += token
                            print(token, end='', flush=True)
                    except Exception as e:
                        print(f"\n[parse error]: {e} | raw: {decoded[:100]}")
        print(f"\n\n--- Full response ({len(full)} chars) ---\n{full}")
    else:
        print(f"ERROR {response.status_code}: {response.text[:500]}")
except Exception as e:
    print(f"Exception: {e}")
    import traceback; traceback.print_exc()
