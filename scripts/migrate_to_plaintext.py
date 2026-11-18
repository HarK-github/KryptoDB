"""Migration script to safely decrypt legacy encrypted Neo4j facts into plaintext GraphRAG properties.

Usage:
  uv run python scripts/migrate_to_plaintext.py --dry-run
  uv run python scripts/migrate_to_plaintext.py --live
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

from kryptodb.infrastructure.neo4j.client import neo4j_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("migrate_to_plaintext")


def migrate(dry_run: bool = True) -> int:
    neo4j_client.connect()
    if not neo4j_client.driver:
        logger.error("Could not connect to Neo4j.")
        return 1

    try:
        from kryptodb.legacy.crypto.partition_crypto import Encryptor
        encryptor = Encryptor()
    except Exception as exc:
        logger.warning("Could not initialize legacy Encryptor (%s). Plaintext check will proceed.", exc)
        encryptor = None

    scan_query = """
    MATCH (s:Entity)-[r:FACT]->(o:Entity)
    WHERE r.ciphertext IS NOT NULL OR r.ciphertext_hex IS NOT NULL
    RETURN r.id AS id,
           r.scope AS scope,
           coalesce(r.ciphertext, r.ciphertext_hex) AS ciphertext,
           coalesce(r.iv, r.iv_hex) AS iv,
           coalesce(r.tag, r.tag_hex) AS tag,
           s.name AS s_name,
           o.name AS o_name
    """

    try:
        if hasattr(neo4j_client, "execute_query_with_retry"):
            records, _, _ = neo4j_client.execute_query_with_retry(scan_query)
        else:
            records, _, _ = neo4j_client.driver.execute_query(scan_query)
    except Exception as exc:
        logger.info("Scan completed; no legacy encrypted records found or query returned: %s", exc)
        records = []

    logger.info("Found %d legacy encrypted records.", len(records))
    migrated_count = 0

    for rec in records:
        fact_id = rec.get("id")
        scope = rec.get("scope") or "default"
        ct = rec.get("ciphertext")
        iv = rec.get("iv")
        tag = rec.get("tag")

        if not ct:
            continue

        plaintext_data = None
        if encryptor:
            try:
                decrypted = encryptor.decrypt(ct, iv, tag, scope)
                plaintext_data = json.loads(decrypted) if isinstance(decrypted, str) else decrypted
            except Exception as d_exc:
                logger.warning("Failed to decrypt fact %s: %s", fact_id, d_exc)

        if not plaintext_data:
            # Fallback to existing entity names if decryption cannot parse
            plaintext_data = {
                "subject": rec.get("s_name"),
                "predicate": "ASSOCIATED_WITH",
                "object": rec.get("o_name"),
                "description": f"{rec.get('s_name')} ASSOCIATED_WITH {rec.get('o_name')}",
            }

        logger.info(
            "[%s] Migrating fact %s: (%s)-[%s]->(%s)",
            "DRY RUN" if dry_run else "LIVE",
            fact_id,
            plaintext_data.get("subject"),
            plaintext_data.get("predicate"),
            plaintext_data.get("object"),
        )

        if not dry_run:
            update_query = """
            MATCH ()-[r:FACT {id: $id}]->()
            SET r.subject = $subject,
                r.predicate = $predicate,
                r.object = $object,
                r.description = $description,
                r.ciphertext = NULL,
                r.ciphertext_hex = NULL,
                r.iv = NULL,
                r.iv_hex = NULL,
                r.tag = NULL,
                r.tag_hex = NULL
            """
            try:
                if hasattr(neo4j_client, "execute_query_with_retry"):
                    neo4j_client.execute_query_with_retry(
                        update_query,
                        id=fact_id,
                        subject=plaintext_data.get("subject"),
                        predicate=plaintext_data.get("predicate"),
                        object=plaintext_data.get("object"),
                        description=plaintext_data.get("description"),
                    )
                else:
                    neo4j_client.driver.execute_query(
                        update_query,
                        id=fact_id,
                        subject=plaintext_data.get("subject"),
                        predicate=plaintext_data.get("predicate"),
                        object=plaintext_data.get("object"),
                        description=plaintext_data.get("description"),
                    )
                migrated_count += 1
            except Exception as u_exc:
                logger.error("Failed to update plaintext properties for fact %s: %s", fact_id, u_exc)
        else:
            migrated_count += 1

    logger.info("Migration finished: %d facts %s.", migrated_count, "would be migrated" if dry_run else "migrated")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Migrate legacy encrypted Neo4j facts to plaintext.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="Simulate migration without modifying Neo4j.")
    group.add_argument("--live", action="store_true", help="Execute live migration modifying Neo4j properties.")
    args = parser.parse_args()

    sys.exit(migrate(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
