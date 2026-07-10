import hashlib
import json
import requests
from config import settings
from services.embedding_service import get_embeddings_batch, calculate_cosine_similarity
from services.chunker import extract_sections

def get_text_hash(text: str) -> str:
    """Returns SHA-256 hash of a given text."""
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def diff_and_embed_chunks(new_chunks: list[dict], old_chunks_db: list[dict]) -> list[dict]:
    """
    Compares new chunks against old chunks (retrieved from the DB).
    Reuses embeddings for unchanged chunks based on text hash matching.
    Fetches embeddings only for new/modified chunks.
    
    `old_chunks_db` elements should have fields:
        - "content": str
        - "embedding": list[float]
    
    Returns the updated `new_chunks` list where each chunk dictionary contains the "embedding" key.
    """
    # Create hash map of old chunks: hash -> embedding
    old_hashes = {}
    for chunk in old_chunks_db:
        h = get_text_hash(chunk["content"])
        # Store embedding
        old_hashes[h] = chunk["embedding"]
        
    # Separate chunks into reused and to-embed
    chunks_to_embed = []
    indices_to_embed = []
    
    for idx, chunk in enumerate(new_chunks):
        h = get_text_hash(chunk["content"])
        if h in old_hashes:
            # Reuse embedding
            chunk["embedding"] = old_hashes[h]
        else:
            chunks_to_embed.append(chunk["content"])
            indices_to_embed.append(idx)
            
    # Embed the new/modified chunks in batch
    if chunks_to_embed:
        new_embeddings = get_embeddings_batch(chunks_to_embed)
        for sub_idx, emb in zip(indices_to_embed, new_embeddings):
            new_chunks[sub_idx]["embedding"] = emb
            
    return new_chunks


def compute_doc_embedding(chunks: list[dict]) -> list[float]:
    """
    Computes a document-level embedding by averaging chunk embeddings.
    """
    if not chunks:
        return [0.0] * 1536
        
    embeddings = [c["embedding"] for c in chunks if "embedding" in c]
    if not embeddings:
        return [0.0] * 1536
        
    import numpy as np
    avg_vector = np.mean(embeddings, axis=0)
    norm = np.linalg.norm(avg_vector)
    if norm > 0:
        avg_vector = avg_vector / norm
    return avg_vector.tolist()


def classify_change_severity_heuristic(
    old_text: str, new_text: str,
    old_chunks: list[dict], new_chunks: list[dict]
) -> tuple[int, str]:
    """
    Deterministic heuristic classifier based on structural diff and semantic shift.
    Returns (change_class, summary_text).
    """
    # Calculate structural changes
    old_sections = extract_sections(old_text)
    new_sections = extract_sections(new_text)
    
    old_sections_map = {s["heading"]: s["level"] for s in old_sections}
    new_sections_map = {s["heading"]: s["level"] for s in new_sections}
    
    added_headings = [h for h in new_sections_map if h not in old_sections_map]
    removed_headings = [h for h in old_sections_map if h not in new_sections_map]
    
    heading_changes = len(added_headings) + len(removed_headings)
    section_count_diff = abs(len(new_sections) - len(old_sections))
    
    # Calculate semantic shift
    old_doc_emb = compute_doc_embedding(old_chunks)
    new_doc_emb = compute_doc_embedding(new_chunks)
    
    similarity = calculate_cosine_similarity(old_doc_emb, new_doc_emb)
    cosine_distance = 1.0 - similarity
    
    # Determine Class
    # Class 0: No change / Formatting only
    # Class 1: Minor (cos_dist < 0.02, few heading changes)
    # Class 2: Moderate (cos_dist < 0.08, some heading changes)
    # Class 3: Major (cos_dist < 0.2, multiple sections changed)
    # Class 4: Critical / Rewrite (cos_dist >= 0.2, total revamp)
    
    if old_text == new_text:
        change_class = 0
        summary = "No changes detected. The documents are identical."
    elif cosine_distance < 0.005 and heading_changes == 0:
        change_class = 0
        summary = f"Trivial changes. Semantic distance of {cosine_distance:.4f} indicates only minor formatting or whitespace updates."
    elif cosine_distance < 0.02 and heading_changes <= 1:
        change_class = 1
        summary = f"Minor updates. Semantic distance of {cosine_distance:.4f} and {heading_changes} heading modifications."
    elif cosine_distance < 0.08 and heading_changes <= 3:
        change_class = 2
        summary = f"Moderate changes. Semantic distance of {cosine_distance:.4f} with {heading_changes} section modifications."
    elif cosine_distance < 0.20:
        change_class = 3
        summary = f"Major edits. Semantic distance of {cosine_distance:.4f} with substantial modifications to the document structure ({heading_changes} heading updates)."
    else:
        change_class = 4
        summary = f"Critical revamp. Cosine distance of {cosine_distance:.4f} indicates a complete or near-complete rewriting of the document content."
        
    return change_class, summary


def classify_change_severity(
    old_text: str, new_text: str,
    old_chunks: list[dict], new_chunks: list[dict]
) -> tuple[int, str]:
    """
    Classifies change severity between two versions.
    Attempts to call Phi-3-mini running on local llama.cpp server for high-quality analysis.
    Falls back to heuristic classifier if llama.cpp server is unreachable.
    """
    heuristic_class, heuristic_summary = classify_change_severity_heuristic(
        old_text, new_text, old_chunks, new_chunks
    )
    
    # Attempt LLM Classification if llama.cpp server is online
    try:
        # Limit comparison text size to avoid prompt overflow
        old_truncated = old_text[:1500] + ("..." if len(old_text) > 1500 else "")
        new_truncated = new_text[:1500] + ("..." if len(new_text) > 1500 else "")
        
        prompt = f"""
You are an expert policy analyst auditing changes between two versions of a policy document.
Analyze the differences below and classify the changes into one of these classes:
- Class 0: No changes or formatting-only changes (typos, whitespaces, formatting).
- Class 1: Minor changes (small wording tweaks, single sentence additions, no structure change).
- Class 2: Moderate changes (minor policy updates, adding/modifying single section/paragraph, updating links/dates).
- Class 3: Major changes (significant rewrite of core sections, adding multiple new sections, deleting old sections).
- Class 4: Critical / Complete rewrite (complete replacement of the document content or massive structural restructuring).

Previous Document Version (Truncated):
\"\"\"
{old_truncated}
\"\"\"

New Document Version (Truncated):
\"\"\"
{new_truncated}
\"\"\"

Compare the versions. Determine:
1. The class label (0 to 4).
2. A brief 1-2 sentence plain-English summary of what changed.

Return your response EXACTLY as a JSON object, with keys "class" (integer 0-4) and "summary" (string). 
Do not include any explanation or markdown formatting, just the raw JSON object.
"""
        response = requests.post(
            f"{settings.vllm_api_url}/chat/completions",
            json={
                "model": "phi3",
                "messages": [
                    {"role": "system", "content": "You are a policy classification assistant. Respond only in JSON."},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.1,
                "max_tokens": 150
            },
            timeout=10
        )
        
        if response.status_code == 200:
            res_data = response.json()
            content = res_data['choices'][0]['message']['content'].strip()
            # Clean up potential markdown formatting block if LLM added it
            if content.startswith("```"):
                content = content.replace("```json", "").replace("```", "").strip()
            
            parsed = json.loads(content)
            llm_class = int(parsed.get("class", heuristic_class))
            llm_summary = parsed.get("summary", heuristic_summary)
            
            # Bound check the class
            if 0 <= llm_class <= 4:
                return llm_class, f"{llm_summary} (LLM Verified)"
    except Exception:
        pass
        
    return heuristic_class, heuristic_summary
