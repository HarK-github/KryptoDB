"""
Transient, in-memory search index for the active session.
Implements a basic Bloom filter for n-gram/word substring matching,
falling back to precise matching on hits. Cleared on session end.
"""
import hashlib
from typing import List

class SessionSearchIndex:
    def __init__(self, bit_size=10000, num_hashes=3):
        self.bit_size = bit_size
        self.num_hashes = num_hashes
        self.bit_array = [0] * bit_size
        # Cache for exact substring matches if bloom filter hits
        self._cached_edges = []
        
    def _hashes(self, term: str) -> List[int]:
        res = []
        base_hash = hashlib.sha256(term.encode('utf-8')).digest()
        for i in range(self.num_hashes):
            idx = int.from_bytes(base_hash[i*4:(i+1)*4], byteorder='big') % self.bit_size
            res.append(idx)
        return res

    def _tokenize(self, text: str) -> set:
        if not text:
            return set()
        words = text.lower().replace('-', ' ').replace('_', ' ').split()
        return set(words)

    def add_edge(self, edge: dict):
        # Avoid duplicates in cache
        for existing in self._cached_edges:
            if existing["edge_id"] == edge["edge_id"]:
                return
                
        self._cached_edges.append(edge)
        
        tokens = self._tokenize(edge.get("subject", ""))
        tokens.update(self._tokenize(edge.get("object", "")))
        tokens.update(self._tokenize(edge.get("predicate", "")))
        
        for token in tokens:
            for idx in self._hashes(token):
                self.bit_array[idx] = 1

    def search(self, query: str) -> List[dict]:
        query_tokens = self._tokenize(query)
        if not query_tokens:
            return []
            
        # 1. Bloom Filter Check (O(1) rejection)
        for token in query_tokens:
            for idx in self._hashes(token):
                if self.bit_array[idx] == 0:
                    return [] # Definitive negative
                    
        # 2. Cache check for exact substring match (if Bloom Filter hits)
        query_lower = query.lower()
        results = []
        for edge in self._cached_edges:
            sub = (edge.get("subject") or "").lower()
            obj = (edge.get("object") or "").lower()
            pred = (edge.get("predicate") or "").lower()
            
            if query_lower in sub or query_lower in obj or query_lower in pred:
                results.append(edge)
                
        return results

    def clear(self):
        self.bit_array = [0] * self.bit_size
        self._cached_edges.clear()

# Global singleton for the daemon
session_index = SessionSearchIndex()
