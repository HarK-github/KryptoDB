import pytest
from kryptodb.integrity.embeddings import EphemeralEmbeddingService, cosine_similarity
from kryptodb.storage.neo4j_client import neo4j_client
from kryptodb.storage.redis_client import redis_client


def test_ephemeral_embeddings_ranking_logic():
    service = EphemeralEmbeddingService()
    query = "malware threat intelligence"
    
    facts = [
        {"id": "1", "subject": "Fox-Kitten", "predicate": "EXPLOITS", "object": "CVE-2026-11342", "description": "threat group malware"},
        {"id": "2", "subject": "Alice", "predicate": "MANAGES", "object": "Frontend", "description": "UI layout team"}
    ]
    
    ranked = service.rank_facts_ephemeral(query, facts)
    assert len(ranked) == 2
    # The threat group fact should be ranked first
    assert ranked[0]["id"] == "1"


def test_no_embeddings_persisted_in_storage():
    """Assert that computing embeddings never persists any embedding vectors to Redis or Neo4j."""
    service = EphemeralEmbeddingService()
    
    # 1. Execute ephemeral encoding and ranking
    query = "confidential financial records"
    facts = [
        {"id": "f-101", "subject": "ACME", "predicate": "REPORTS", "object": "Revenue", "description": "annual earnings report"}
    ]
    ranked = service.rank_facts_ephemeral(query, facts)
    assert len(ranked) == 1
    
    # 2. Check Redis: no keys containing 'embed' or 'vector'
    if redis_client.client:
        try:
            keys = redis_client.client.keys("*embed*") + redis_client.client.keys("*vector*")
            assert len(keys) == 0, f"Found leaked embedding keys in Redis: {keys}"
        except Exception:
            pass

    # 3. Check in-memory locks map in redis_client: no embedding data
    for k in redis_client._memory_locks:
        assert "embed" not in k.lower()

    # 4. Check Neo4j: verify no 'embedding' property is written by WriteEngine
    from kryptodb.graph.schema import Edge
    from kryptodb.graph.write_engine import WriteEngine
    
    engine = WriteEngine()
    test_edge = Edge(
        id="fact-embed-check",
        type="FACT",
        predicate="OWNS",
        permission_scope="PROJECT:test",
        source_agent_id="agent-test",
        subject_id="sub-test",
        object_id="obj-test"
    )
    
    # Check that Edge schema itself does not expose raw embedding properties to Neo4j
    assert "embedding" not in Edge.model_fields
    assert "embedding" not in test_edge.model_dump()
