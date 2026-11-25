"""Neo4j idempotent schema declaration and index bootstrap."""

from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger("kryptodb.infrastructure.neo4j.schema")

COMMUNITY_CONSTRAINTS = [
    """
    CREATE CONSTRAINT entity_tenant_name_unique IF NOT EXISTS
    FOR (n:Entity) REQUIRE (n.tenant_id, n.name) IS UNIQUE
    """,
    """
    CREATE CONSTRAINT entity_tenant_norm_name_unique IF NOT EXISTS
    FOR (n:Entity) REQUIRE (n.tenant_id, n.norm_name) IS UNIQUE
    """,
    """
    CREATE CONSTRAINT event_id_unique IF NOT EXISTS
    FOR (n:Event) REQUIRE n.id IS UNIQUE
    """,
    """
    CREATE CONSTRAINT episode_id_unique IF NOT EXISTS
    FOR (n:Episode) REQUIRE n.id IS UNIQUE
    """,
]

ENTERPRISE_ONLY_CONSTRAINTS = [
    """
    CREATE CONSTRAINT fact_id_unique IF NOT EXISTS
    FOR ()-[r:FACT]-() REQUIRE r.id IS UNIQUE
    """,
    """
    CREATE CONSTRAINT fact_idempotency_unique IF NOT EXISTS
    FOR ()-[r:FACT]-() REQUIRE (r.tenant_id, r.idempotency_key) IS UNIQUE
    """,
]

FULLTEXT_INDEXES = [
    """
    CREATE FULLTEXT INDEX entity_name_ft IF NOT EXISTS
    FOR (n:Entity) ON EACH [n.name]
    """,
    """
    CREATE FULLTEXT INDEX fact_text_ft IF NOT EXISTS
    FOR ()-[r:FACT]-() ON EACH [r.predicate, r.description]
    """,
]


def get_node_vector_index_query(
    index_name: str = "entity_embedding_idx",
    label: str = "Entity",
    prop: str = "embedding",
    dimensions: int = 384,
    similarity: str = "cosine",
) -> str:
    return f"""
    CREATE VECTOR INDEX {index_name} IF NOT EXISTS
    FOR (n:{label}) ON (n.{prop})
    OPTIONS {{indexConfig: {{
        `vector.dimensions`: {dimensions},
        `vector.similarity_function`: '{similarity}'
    }}}}
    """


def get_rel_vector_index_query(
    index_name: str = "fact_embedding_idx",
    dimensions: int = 384,
    similarity: str = "cosine",
) -> str:
    return f"""
    CREATE VECTOR INDEX {index_name} IF NOT EXISTS
    FOR ()-[r:FACT]-() ON (r.embedding)
    OPTIONS {{indexConfig: {{
        `vector.dimensions`: {dimensions},
        `vector.similarity_function`: '{similarity}'
    }}}}
    """


def ensure_schema(driver: Any, dimensions: int = 384) -> Dict[str, Any]:
    """Idempotently initializes Neo4j constraints, fulltext indexes, and vector indexes."""
    report: Dict[str, Any] = {
        "constraints": False,
        "enterprise_rel_constraints": False,
        "fulltext_indexes": False,
        "entity_vector_index": False,
        "fact_vector_index": False,
        "episode_vector_index": False,
        "errors": [],
    }

    if not driver:
        return report

    # 1. Node Constraints (Community + Enterprise)
    node_ok = True
    try:
        with driver.session() as session:
            for query in COMMUNITY_CONSTRAINTS:
                try:
                    session.run(query)
                except Exception as c_exc:
                    node_ok = False
                    report["errors"].append(f"constraint: {c_exc}")
                    logger.warning("Constraint creation failed: %s", c_exc)
        report["constraints"] = node_ok
    except Exception as exc:
        report["errors"].append(f"constraints_session: {exc}")
        logger.warning("Error creating constraints: %s", exc)

    # 2. Enterprise-only Relationship Constraints (best-effort on Community)
    ent_ok = True
    try:
        with driver.session() as session:
            for query in ENTERPRISE_ONLY_CONSTRAINTS:
                try:
                    session.run(query)
                except Exception as e_exc:
                    ent_ok = False
                    logger.info("Enterprise relationship constraint skipped (Community Edition): %s", e_exc)
        report["enterprise_rel_constraints"] = ent_ok
    except Exception as exc:
        logger.info("Enterprise constraint check skipped: %s", exc)

    # 3. Fulltext Indexes
    ft_ok = True
    try:
        with driver.session() as session:
            for query in FULLTEXT_INDEXES:
                try:
                    session.run(query)
                except Exception as f_exc:
                    ft_ok = False
                    report["errors"].append(f"fulltext: {f_exc}")
                    logger.warning("Fulltext index creation failed: %s", f_exc)
        report["fulltext_indexes"] = ft_ok
    except Exception as exc:
        report["errors"].append(f"fulltext_session: {exc}")
        logger.warning("Error creating fulltext indexes: %s", exc)

    # 4. Vector Indexes
    for key, q in [
        ("entity_vector_index", get_node_vector_index_query("entity_embedding_idx", "Entity", "embedding", dimensions)),
        ("episode_vector_index", get_node_vector_index_query("episode_embedding_idx", "Episode", "embedding", dimensions)),
        ("fact_vector_index", get_rel_vector_index_query("fact_embedding_idx", dimensions)),
    ]:
        try:
            with driver.session() as session:
                session.run(q)
                report[key] = True
        except Exception as exc:
            report["errors"].append(f"{key}: {exc}")
            logger.warning("Error creating %s: %s", key, exc)

    return report
