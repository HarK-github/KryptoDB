import os
import pytest

# Ensure standard test admin key and deterministic test LLM are configured
os.environ.setdefault("KRYPTODB_ADMIN_API_KEY", "test-admin-key")
os.environ.setdefault("LLM_MODEL", "deterministic-engine")

# Wire Cypher query strings into core/ retrieval modules for unit tests
from kryptodb.core.retrieval.hybrid_search import configure_hybrid_search
from kryptodb.core.retrieval.service import configure_retrieval_queries
from kryptodb.core.retrieval.traversal import configure_traversal
from kryptodb.infrastructure.neo4j import queries as _q

configure_hybrid_search(
    client=None,
    queries={
        "HYBRID_VECTOR_ENTITY_QUERY": getattr(_q, "HYBRID_VECTOR_ENTITY_QUERY", ""),
        "HYBRID_FULLTEXT_ENTITY_QUERY": getattr(_q, "HYBRID_FULLTEXT_ENTITY_QUERY", ""),
        "SEMANTIC_SEED_FALLBACK_ENTITY_QUERY": getattr(_q, "SEMANTIC_SEED_FALLBACK_ENTITY_QUERY", ""),
        "SEMANTIC_SEED_FALLBACK_FACT_QUERY": getattr(_q, "SEMANTIC_SEED_FALLBACK_FACT_QUERY", ""),
    },
)
configure_traversal(
    traversal_1hop=getattr(_q, "TRAVERSAL_1HOP_QUERY", ""),
    traversal_2hop=getattr(_q, "TRAVERSAL_2HOP_EXPANSION_QUERY", ""),
)
configure_retrieval_queries(retrospective_query=getattr(_q, "RETROSPECTIVE_FACTS_QUERY", ""))


@pytest.fixture(autouse=True)
def ensure_default_test_api_keys():
    """Ensures test admin key is restored after tests that temporarily modify env."""
    yield
    if "KRYPTODB_ADMIN_API_KEY" not in os.environ:
        os.environ["KRYPTODB_ADMIN_API_KEY"] = "test-admin-key"
