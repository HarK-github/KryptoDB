"""Neo4j idempotent schema declaration and index bootstrap."""

from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger("kryptodb.infrastructure.neo4j.schema")

CONSTRAINTS = [
    """
    CREATE CONSTRAINT entity_tenant_name_unique IF NOT EXISTS
    FOR (n:Entity) REQUIRE (n.tenant_id, n.name) IS UNIQUE
    """,
    """
    CREATE CONSTRAINT event_id_unique IF NOT EXISTS
    FOR (n:Event) REQUIRE n.id IS UNIQUE
    """,
    """
    CREATE CONSTRAINT episode_id_unique IF NOT EXISTS
    FOR (n:Episode) REQUIRE n.id IS UNIQUE
    """,
    """
    CREATE CONSTRAINT fact_id_unique IF NOT EXISTS
    FOR ()-[r:FACT]-() REQUIRE r.id IS UNIQUE
    """
]

FULLTEXT_INDEXES = [
    """
    CREATE FULLTEXT INDEX entity_name_ft IF NOT EXISTS
    FOR (n:Entity) ON EACH [n.name]
    """,
    """
    CREATE FULLTEXT INDEX fact_text_ft IF NOT EXISTS
    FOR ()-[r:FACT]-() ON EACH [r.predicate, r.description]
    """
]

def get_node_vector_index_query(index_name: str = "entity_embedding_idx", label: str = "Entity", prop: str = "embedding", dimensions: int = 384, similarity: str = "cosine") -> str:
    return f"""
    CREATE VECTOR INDEX {index_name} IF NOT EXISTS
    FOR (n:{label}) ON (n.{prop})
    OPTIONS {{indexConfig: {{
        `vector.dimensions`: {dimensions},
        `vector.similarity_function`: '{similarity}'
    }}}}
    """

def get_rel_vector_index_query(index_name: str = "fact_embedding_idx", dimensions: int = 384, similarity: str = "cosine") -> str:
    return f"""
    CREATE VECTOR INDEX {index_name} IF NOT EXISTS
    FOR ()-[r:FACT]-() ON (r.embedding)
    OPTIONS {{indexConfig: {{
        `vector.dimensions`: {dimensions},
        `vector.similarity_function`: '{similarity}'
    }}}}
    """

def ensure_schema(driver: Any, dimensions: int = 384) -> Dict[str, bool]:
    """Idempotently initializes all Neo4j constraints, fulltext indexes, and vector indexes."""
    report = {
        "constraints": False,
        "fulltext_indexes": False,
        "entity_vector_index": False,
        "fact_vector_index": False,
        "episode_vector_index": False,
    }

    if not driver:
        return report

    # 1. Constraints
    try:
        with driver.session() as session:
            for query in CONSTRAINTS:
                try:
                    session.run(query)
                except Exception as c_exc:
                    logger.debug("Constraint creation notice: %s", c_exc)
            report["constraints"] = True
    except Exception as exc:
        logger.warning("Error creating constraints: %s", exc)

    # 2. Fulltext Indexes
    try:
        with driver.session() as session:
            for query in FULLTEXT_INDEXES:
                try:
                    session.run(query)
                except Exception as f_exc:
                    logger.debug("Fulltext index creation notice: %s", f_exc)
            report["fulltext_indexes"] = True
    except Exception as exc:
        logger.warning("Error creating fulltext indexes: %s", exc)

    # 3. Entity Vector Index
    try:
        with driver.session() as session:
            session.run(get_node_vector_index_query("entity_embedding_idx", "Entity", "embedding", dimensions))
            report["entity_vector_index"] = True
    except Exception as exc:
        logger.warning("Error creating entity vector index: %s", exc)

    # 4. Episode Vector Index
    try:
        with driver.session() as session:
            session.run(get_node_vector_index_query("episode_embedding_idx", "Episode", "embedding", dimensions))
            report["episode_vector_index"] = True
    except Exception as exc:
        logger.warning("Error creating episode vector index: %s", exc)

    # 5. Relationship Vector Index
    try:
        with driver.session() as session:
            session.run(get_rel_vector_index_query("fact_embedding_idx", dimensions))
            report["fact_vector_index"] = True
    except Exception as exc:
        logger.info("Relationship vector index creation notice: %s", exc)

    return report
