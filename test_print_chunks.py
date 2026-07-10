import sys
sys.path.append("backend")
from services.rag_service import semantic_retrieval, keyword_retrieval, rrf_blend, local_rerank, context_tagger
from config import settings

def test():
    query_text = "what is tender"
    semantic_res = semantic_retrieval(query_text, limit=20)
    keyword_res = keyword_retrieval(query_text, limit=20)
    blended = rrf_blend(semantic_res, keyword_res, limit=30)
    reranked = local_rerank(query_text, blended, limit=4)
    tagged = context_tagger(reranked)
    
    print(f"Retrieved {len(tagged)} chunks:")
    for idx, c in enumerate(tagged):
        print(f"\n--- Chunk {idx+1} ---")
        print(f"Title: {c['title']}")
        print(f"Content (first 500 chars):\n{c['content'][:500]}")
        print(f"Content (last 500 chars):\n{c['content'][-500:]}")
        print("-" * 40)

if __name__ == "__main__":
    test()
