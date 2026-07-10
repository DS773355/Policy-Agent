import pytest
from services.chunker import split_into_chunks, extract_sections

def test_split_into_chunks_empty():
    assert split_into_chunks("") == []
    assert split_into_chunks("   ") == []


def test_split_into_chunks_basic():
    text = "Hello world! This is a simple test of the text chunking mechanism."
    # Set small chunk size and overlap to trigger chunking on small texts
    chunks = split_into_chunks(text, chunk_size=5, overlap=1)
    
    assert len(chunks) > 0
    for i, c in enumerate(chunks):
        assert c["chunk_index"] == i
        assert len(c["content"]) > 0
        assert c["token_count"] > 0


def test_split_into_chunks_overlap():
    # Construct a document with distinct words to test overlap
    words = [f"word{i}" for i in range(100)]
    text = " ".join(words)
    
    # Chunk size = 20, overlap = 5
    chunks = split_into_chunks(text, chunk_size=20, overlap=5)
    
    assert len(chunks) > 1
    # Check that overlap words are shared between chunk 0 and chunk 1
    c0_words = chunks[0]["content"].split()
    c1_words = chunks[1]["content"].split()
    
    # Find the overlap words (they should be at the end of c0 and start of c1)
    # Because of word-based or token-based differences, let's just make sure there is some overlap
    overlap_set = set(c0_words).intersection(set(c1_words))
    assert len(overlap_set) > 0


def test_extract_sections():
    markdown_text = """
# Heading 1
Some introductory text.

## Heading 2
Some section text.

### Heading 3
More detailed content.

AN UPPERCASE HEADING
This line should also be matched as a heading.
"""
    sections = extract_sections(markdown_text)
    
    assert len(sections) == 4
    
    assert sections[0]["heading"] == "Heading 1"
    assert sections[0]["level"] == 1
    
    assert sections[1]["heading"] == "Heading 2"
    assert sections[1]["level"] == 2
    
    assert sections[2]["heading"] == "Heading 3"
    assert sections[2]["level"] == 3
    
    assert sections[3]["heading"] == "AN UPPERCASE HEADING"
    assert sections[3]["level"] == 1
