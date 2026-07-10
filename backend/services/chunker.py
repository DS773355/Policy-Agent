import tiktoken
import re

# Global encoder cache
_encoder = None

def get_encoder():
    global _encoder
    if _encoder is None:
        try:
            _encoder = tiktoken.get_encoding("cl100k_base")
        except Exception as e:
            print(f"Warning: Failed to load tiktoken encoder cl100k_base ({e}). Using fallback word-based encoder.")
            _encoder = FallbackEncoder()
    return _encoder


class FallbackEncoder:
    """A simple fallback encoder that estimates tokens based on whitespace words."""
    def encode(self, text: str) -> list[int]:
        # Estimate: split by non-alphanumeric and return list of mock token ids (just using word indices or hash)
        words = re.findall(r'\b\w+\b|\s+|[^\w\s]', text)
        return [hash(w) % 100000 for w in words]
        
    def decode(self, tokens: list[int]) -> str:
        # A simple fallback cannot fully reconstruct from hashed ids,
        # but we only use encode/decode length and chunk boundaries.
        # In our sliding window we will chunk by words.
        return ""


def split_into_chunks(text: str, chunk_size: int = 500, overlap: int = 50) -> list[dict]:
    """
    Splits text into chunks of ~chunk_size tokens with ~overlap tokens overlap.
    Returns a list of dicts: [
        {
            "chunk_index": int,
            "content": str,
            "token_count": int
        }, ...
    ]
    """
    if not text or not text.strip():
        return []
        
    encoder = get_encoder()
    
    # Check if we are using the fallback encoder
    if isinstance(encoder, FallbackEncoder):
        # Word-based splitting for simple and robust fallback
        words = text.split()
        chunks = []
        chunk_idx = 0
        
        # Word size equivalents: 500 tokens is roughly 375 words, 50 overlap is ~38 words
        word_chunk_size = int(chunk_size * 0.75)
        word_overlap = int(overlap * 0.75)
        
        i = 0
        while i < len(words):
            chunk_words = words[i:i + word_chunk_size]
            content = " ".join(chunk_words)
            chunks.append({
                "chunk_index": chunk_idx,
                "content": content,
                "token_count": len(chunk_words)  # word count as token count
            })
            chunk_idx += 1
            if i + word_chunk_size >= len(words):
                break
            i += (word_chunk_size - word_overlap)
        return chunks

    # Standard tiktoken token-level chunking
    tokens = encoder.encode(text)
    chunks = []
    chunk_idx = 0
    
    i = 0
    while i < len(tokens):
        # Slice the tokens for the current chunk
        chunk_tokens = tokens[i:i + chunk_size]
        content = encoder.decode(chunk_tokens)
        
        chunks.append({
            "chunk_index": chunk_idx,
            "content": content,
            "token_count": len(chunk_tokens)
        })
        chunk_idx += 1
        
        # Move forward by chunk_size - overlap
        if i + chunk_size >= len(tokens):
            break
        i += (chunk_size - overlap)
        
    return chunks


def extract_sections(text: str) -> list[dict]:
    """
    Identifies sections and headings in the document.
    Matches markdown headings like '# Title', '## Section Name' or uppercase lines.
    Returns a list of dicts: [{"heading": str, "level": int, "line_no": int}]
    """
    sections = []
    lines = text.split('\n')
    for idx, line in enumerate(lines):
        line = line.strip()
        # Match markdown headers
        match = re.match(r'^(#{1,6})\s+(.+)$', line)
        if match:
            level = len(match.group(1))
            heading = match.group(2).strip()
            sections.append({
                "heading": heading,
                "level": level,
                "line_no": idx + 1
            })
        # Alternatively, match lines that look like uppercase headers
        elif len(line) > 3 and line.isupper() and len(line) < 100:
            # Check if it doesn't end with a period and isn't part of normal text
            sections.append({
                "heading": line,
                "level": 1,
                "line_no": idx + 1
            })
    return sections
