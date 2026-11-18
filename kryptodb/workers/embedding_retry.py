"""Background worker for asynchronous embedding retry and catch-up."""

from __future__ import annotations

import logging
from typing import Any, Optional

from kryptodb.core.embeddings.embedder import default_embedder
from kryptodb.core.ports import Embedder
from kryptodb.infrastructure.neo4j.client import neo4j_client

logger = logging.getLogger("kryptodb.workers.embedding_retry")


def reembed_pending_edges(
    client: Any = None,
    embedder: Optional[Embedder] = None,
    limit: int = 100,
) -> int:
    """Finds relationships where embedding_pending = true, computes vectors, and updates properties."""
    cli = client or neo4j_client
    emb = embedder or default_embedder

    if not cli or not getattr(cli, "driver", None):
        return 0

    find_query = """
    MATCH ()-[r:FACT]->()
    WHERE r.embedding_pending = true
    RETURN r.id AS id, r.predicate AS predicate, r.description AS description
    LIMIT $limit
    """

    try:
        if hasattr(cli, "execute_query_with_retry"):
            records, _, _ = cli.execute_query_with_retry(find_query, limit=limit)
        else:
            records, _, _ = cli.driver.execute_query(find_query, limit=limit)
    except Exception as exc:
        logger.error("Error querying pending edge embeddings: %s", exc)
        return 0

    count = 0
    for row in records:
        fact_id = row.get("id")
        desc = row.get("description") or row.get("predicate")
        if not fact_id or not desc:
            continue

        try:
            vector = emb.embed(desc)
            update_query = """
            MATCH ()-[r:FACT {id: $id}]->()
            SET r.embedding = $vector,
                r.embedding_pending = false
            """
            if hasattr(cli, "execute_query_with_retry"):
                cli.execute_query_with_retry(update_query, id=fact_id, vector=vector)
            else:
                cli.driver.execute_query(update_query, id=fact_id, vector=vector)
            count += 1
        except Exception as u_exc:
            logger.warning("Failed updating vector for edge %s: %s", fact_id, u_exc)

    return count


def reembed_pending_entities(
    client: Any = None,
    embedder: Optional[Embedder] = None,
    limit: int = 100,
) -> int:
    """Finds entities where embedding_pending = true, computes vectors, and updates properties."""
    cli = client or neo4j_client
    emb = embedder or default_embedder

    if not cli or not getattr(cli, "driver", None):
        return 0

    find_query = """
    MATCH (n:Entity)
    WHERE n.embedding_pending = true
    RETURN n.id AS id, n.name AS name
    LIMIT $limit
    """

    try:
        if hasattr(cli, "execute_query_with_retry"):
            records, _, _ = cli.execute_query_with_retry(find_query, limit=limit)
        else:
            records, _, _ = cli.driver.execute_query(find_query, limit=limit)
    except Exception as exc:
        logger.error("Error querying pending entity embeddings: %s", exc)
        return 0

    count = 0
    for row in records:
        entity_id = row.get("id")
        name = row.get("name")
        if not entity_id or not name:
            continue

        try:
            vector = emb.embed(name)
            update_query = """
            MATCH (n:Entity {id: $id})
            SET n.embedding = $vector,
                n.embedding_pending = false
            """
            if hasattr(cli, "execute_query_with_retry"):
                cli.execute_query_with_retry(update_query, id=entity_id, vector=vector)
            else:
                cli.driver.execute_query(update_query, id=entity_id, vector=vector)
            count += 1
        except Exception as u_exc:
            logger.warning("Failed updating vector for entity %s: %s", entity_id, u_exc)

    return count


def reembed_pending(
    client: Any = None,
    embedder: Optional[Embedder] = None,
    limit: int = 100,
) -> int:
    """Main worker entry point: re-embeds both pending relationships and entities."""
    return reembed_pending_edges(client, embedder, limit) + reembed_pending_entities(client, embedder, limit)
