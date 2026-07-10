import sys
sys.path.append("backend")
from services.rag_service import generate_rag_response

def test():
    query_text = "what is tender"
    session_id = "test-session-123"
    print("Testing generate_rag_response for query:", query_text)
    
    with open("output.txt", "w", encoding="utf-8") as f:
        response_stream = generate_rag_response(query_text, session_id)
        for token in response_stream:
            f.write(token)
            f.flush()
            print(token, end="", flush=True)
    print("\n--- Done ---")

if __name__ == "__main__":
    test()
