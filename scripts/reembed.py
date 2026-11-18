#!/usr/bin/env python3
"""
scripts/reembed.py
Resumable batch re-embedding script for KryptoDB graph entities, facts, and episodes.
Usage:
    PYTHONPATH=. uv run python scripts/reembed.py [--dry-run] [--batch-size 50] [--target-model all-MiniLM-L6-v2]
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from typing import List, Dict, Any

from kryptodb.core.embeddings.embedder import LocalSentenceEmbedder
from kryptodb.core.embeddings.versioning import EmbeddingMetadata
from kryptodb.infrastructure.neo4j.client import neo4j_client
from kryptodb.storage import bootstrap_storage

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("reembed")


def parse_args():
    parser = argparse.ArgumentParser(description="KryptoDB Resumable Batch Re-Embedder")
    parser.add_argument("--dry-run", action="store_true", help="Inspect and report without modifying database")
    parser.add_argument("--batch-size", type=int, default=50, help="Number of records to process per batch")
    parser.add_argument("--target-model", type=str, default="all-MiniLM-L6-v2", help="Target embedding model name")
    parser.add_argument("--target-version", type=str, default="1.0.0", help="Target embedding version")
    parser.add_argument("--dimension", type=int, default=384, help="Embedding dimension")
    return parser.parse_args()


def reembed_entities(driver, embedder: LocalSentenceEmbedder, model: str, version: str, batch_size: int, dry_run: bool) -> int:
    query_find = """
    MATCH (n:Entity)
    WHERE n.embedding IS NULL OR n.embedding_model <> $model OR n.embedding_version <> $version
    RETURN n.id AS id, n.name AS text
    LIMIT $batch_size
    """

    query_update = """
    UNWIND $updates AS u
    MATCH (n:Entity {id: u.id})
    SET n.embedding = u.vector,
        n.embedding_model = $model,
        n.embedding_version = $version
    """

    total_updated = 0
    while True:
        records, _, _ = driver.execute_query(query_find, model=model, version=version, batch_size=batch_size)
        if not records:
            break

        texts = [r["text"] or "" for r in records]
        ids = [r["id"] for r in records]

        if dry_run:
            logger.info("[Dry Run] Would re-embed %d entities (e.g. %s)", len(records), ids[:3])
            total_updated += len(records)
            break

        vectors = embedder.embed(texts)
        updates = [{"id": rid, "vector": vec} for rid, vec in zip(ids, vectors)]

        driver.execute_query(query_update, updates=updates, model=model, version=version)
        total_updated += len(updates)
        logger.info("Updated %d Entity records (total: %d)", len(updates), total_updated)

    return total_updated


def reembed_facts(driver, embedder: LocalSentenceEmbedder, model: str, version: str, batch_size: int, dry_run: bool) -> int:
    query_find = """
    MATCH ()-[r:FACT]->()
    WHERE r.embedding IS NULL OR r.embedding_model <> $model OR r.embedding_version <> $version
    RETURN r.id AS id, coalesce(r.statement, r.description, r.predicate, '') AS text
    LIMIT $batch_size
    """

    query_update = """
    UNWIND $updates AS u
    MATCH ()-[r:FACT {id: u.id}]->()
    SET r.embedding = u.vector,
        r.embedding_model = $model,
        r.embedding_version = $version
    """

    total_updated = 0
    while True:
        records, _, _ = driver.execute_query(query_find, model=model, version=version, batch_size=batch_size)
        if not records:
            break

        texts = [r["text"] or "" for r in records]
        ids = [r["id"] for r in records]

        if dry_run:
            logger.info("[Dry Run] Would re-embed %d facts (e.g. %s)", len(records), ids[:3])
            total_updated += len(records)
            break

        vectors = embedder.embed(texts)
        updates = [{"id": rid, "vector": vec} for rid, vec in zip(ids, vectors)]

        driver.execute_query(query_update, updates=updates, model=model, version=version)
        total_updated += len(updates)
        logger.info("Updated %d FACT records (total: %d)", len(updates), total_updated)

    return total_updated


def reembed_episodes(driver, embedder: LocalSentenceEmbedder, model: str, version: str, batch_size: int, dry_run: bool) -> int:
    query_find = """
    MATCH (e:Episode)
    WHERE e.embedding IS NULL OR e.embedding_model <> $model OR e.embedding_version <> $version
    RETURN e.id AS id, coalesce(e.summary, '') AS text
    LIMIT $batch_size
    """

    query_update = """
    UNWIND $updates AS u
    MATCH (e:Episode {id: u.id})
    SET e.embedding = u.vector,
        e.embedding_model = $model,
        e.embedding_version = $version
    """

    total_updated = 0
    while True:
        records, _, _ = driver.execute_query(query_find, model=model, version=version, batch_size=batch_size)
        if not records:
            break

        texts = [r["text"] or "" for r in records]
        ids = [r["id"] for r in records]

        if dry_run:
            logger.info("[Dry Run] Would re-embed %d episodes (e.g. %s)", len(records), ids[:3])
            total_updated += len(records)
            break

        vectors = embedder.embed(texts)
        updates = [{"id": rid, "vector": vec} for rid, vec in zip(ids, vectors)]

        driver.execute_query(query_update, updates=updates, model=model, version=version)
        total_updated += len(updates)
        logger.info("Updated %d Episode records (total: %d)", len(updates), total_updated)

    return total_updated


def main():
    args = parse_args()
    logger.info("Connecting to Neo4j...")
    bootstrap_storage()

    driver = neo4j_client.driver
    if not driver:
        logger.error("Could not connect to Neo4j driver.")
        sys.exit(1)

    meta = EmbeddingMetadata(model_name=args.target_model, model_version=args.target_version, dimension=args.dimension)
    embedder = LocalSentenceEmbedder(metadata=meta)

    logger.info("Target Model: %s (version: %s, dim: %d)", args.target_model, args.target_version, args.dimension)
    logger.info("Batch Size: %d | Dry Run: %s", args.batch_size, args.dry_run)

    t0 = time.perf_counter()
    n_ent = reembed_entities(driver, embedder, args.target_model, args.target_version, args.batch_size, args.dry_run)
    n_fact = reembed_facts(driver, embedder, args.target_model, args.target_version, args.batch_size, args.dry_run)
    n_ep = reembed_episodes(driver, embedder, args.target_model, args.target_version, args.batch_size, args.dry_run)
    elapsed = time.perf_counter() - t0

    logger.info("Re-embedding completed in %.2fs", elapsed)
    logger.info("Summary: Entities=%d, Facts=%d, Episodes=%d", n_ent, n_fact, n_ep)


if __name__ == "__main__":
    main()
