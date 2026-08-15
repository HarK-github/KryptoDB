import pytest
from kryptodb.memory_api.session_search import SessionSearchIndex

def test_bloom_filter_indexing_and_search():
    index = SessionSearchIndex(bit_size=1000, num_hashes=3)
    
    edge = {
        "edge_id": "1",
        "subject": "The quick brown fox",
        "predicate": "JUMPS_OVER",
        "object": "The lazy dog",
        "trust_weight": 0.9,
        "permission_scope": "TEST"
    }
    
    index.add_edge(edge)
    
    # Positive search
    results = index.search("brown fox")
    assert len(results) == 1
    assert results[0]["edge_id"] == "1"
    
    # Negative search
    results_empty = index.search("cat")
    assert len(results_empty) == 0
    
    # Case insensitive
    results_lower = index.search("JUMPS_OVER")
    assert len(results_lower) == 1
    
    # Clear works
    index.clear()
    assert len(index.search("fox")) == 0
