"""Multi-hop graph traversal with cycle suppression, hub suppression, and stratified exploration."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from kryptodb.core.context import RequestContext
from kryptodb.core.filters import FilterBuilder
from kryptodb.infrastructure.neo4j.client import neo4j_client

logger = logging.getLogger("kryptodb.core.retrieval.traversal")


class MultiHopTraverser:
    """Executes cycle-safe 1-to-2 hop traversals with hub suppression and stratified candidate pooling."""

    def __init__(self, client: Any = None, max_hub_degree: int = 50):
        self.client = client or neo4j_client
        self.max_hub_degree = max_hub_degree

    def traverse_from_seeds(
        self,
        seed_entity_ids: List[str],
        context: Optional[RequestContext] = None,
        tenant_id: str = "default",
        active_scopes: Optional[List[str]] = None,
        max_1hop: int = 10,
        max_2hop: int = 15,
        now: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        """Executes stratified 1-hop and 2-hop graph expansion from seed entities."""
        if not seed_entity_ids or not self.client:
            return []
        if not getattr(self.client, "driver", None) and not hasattr(self.client, "execute_query_with_retry"):
            return []

        ctx = context or RequestContext.create(
            tenant_id=tenant_id,
            agent_id="retrieval-agent",
            active_scopes=active_scopes or (["default"] if active_scopes is None else []),
            now=now or datetime.now(timezone.utc),
            is_system=(active_scopes is None),
        )

        paths: List[Dict[str, Any]] = []

        # 1. 1-hop expansion
        filter_1hop, params_1hop = FilterBuilder.build(ctx, alias="e")
        query_1hop = f"""
        MATCH (s:Entity)-[e:FACT]-(t:Entity)
        WHERE (s.id IN $seed_ids OR s.name IN $seed_ids OR t.id IN $seed_ids OR t.name IN $seed_ids)
          AND {filter_1hop}
        RETURN startNode(e).id AS s_id, startNode(e).name AS s_name,
               endNode(e).id AS t_id, endNode(e).name AS t_name,
               e.id AS edge_id, e.predicate AS predicate,
               e.description AS description, e.confidence AS confidence,
               e.trust_weight AS trust_weight, e.scope AS scope,
               e.created_at AS created_at
        ORDER BY e.confidence DESC, e.created_at DESC
        LIMIT $limit_1hop
        """

        try:
            if hasattr(self.client, "execute_query_with_retry"):
                records_1hop, _, _ = self.client.execute_query_with_retry(
                    query_1hop, seed_ids=seed_entity_ids, limit_1hop=max_1hop, **params_1hop
                )
            else:
                records_1hop, _, _ = self.client.driver.execute_query(
                    query_1hop, seed_ids=seed_entity_ids, limit_1hop=max_1hop, **params_1hop
                )

            for r in records_1hop:
                paths.append({
                    "hops": 1,
                    "source_id": r["s_id"],
                    "source_name": r["s_name"],
                    "target_id": r["t_id"],
                    "target_name": r["t_name"],
                    "summary": f"{r['s_name']} {r['predicate']} {r['t_name']}",
                    "confidence": float(r["confidence"] or 1.0),
                    "trust_weight": float(r["trust_weight"] or 1.0),
                    "created_at": r["created_at"],
                    "path_edges": [{
                        "hop": 1,
                        "source": r["s_name"],
                        "predicate": r["predicate"],
                        "target": r["t_name"],
                        "edge_id": r["edge_id"],
                        "description": r["description"],
                        "scope": r["scope"],
                        "confidence": float(r["confidence"] or 1.0),
                    }],
                })
        except Exception as exc:
            logger.error("Error executing 1-hop traversal: %s", exc)

        # 2. 2-hop associative expansion with cycle control and hub suppression
        filter_e1, params_e1 = FilterBuilder.build(ctx, alias="e1")
        filter_e2, params_e2 = FilterBuilder.build(ctx, alias="e2")

        query_2hop = f"""
        MATCH (s:Entity)-[e1:FACT]-(mid:Entity)-[e2:FACT]-(t:Entity)
        WHERE (s.id IN $seed_ids OR s.name IN $seed_ids)
          AND s.id <> t.id AND s.id <> mid.id AND mid.id <> t.id
          AND s <> t AND s <> mid AND mid <> t AND e1 <> e2
          AND {filter_e1}
          AND {filter_e2}
          AND size([(mid)-[:FACT]-() | 1]) <= $max_hub_degree
        RETURN s.id AS s_id, s.name AS s_name,
               mid.id AS mid_id, mid.name AS mid_name,
               t.id AS t_id, t.name AS t_name,
               startNode(e1).name AS e1_src, endNode(e1).name AS e1_dst,
               e1.id AS e1_id, e1.predicate AS e1_pred, e1.confidence AS e1_conf,
               e1.scope AS e1_scope, e1.created_at AS e1_created, e1.description AS e1_desc,
               startNode(e2).name AS e2_src, endNode(e2).name AS e2_dst,
               e2.id AS e2_id, e2.predicate AS e2_pred, e2.confidence AS e2_conf,
               e2.scope AS e2_scope, e2.created_at AS e2_created, e2.description AS e2_desc
        ORDER BY (coalesce(e1.confidence, 1.0) + coalesce(e2.confidence, 1.0)) DESC
        LIMIT $limit_2hop
        """

        merged_params = {
            **params_e1,
            **params_e2,
            "seed_ids": seed_entity_ids,
            "max_hub_degree": self.max_hub_degree,
            "limit_2hop": max_2hop,
        }

        try:
            if hasattr(self.client, "execute_query_with_retry"):
                records_2hop, _, _ = self.client.execute_query_with_retry(query_2hop, **merged_params)
            else:
                records_2hop, _, _ = self.client.driver.execute_query(query_2hop, **merged_params)

            for r in records_2hop:
                e1_c = float(r.get("e1_conf") or 1.0)
                e2_c = float(r.get("e2_conf") or 1.0)
                e1_pred = r.get("e1_pred") or r.get("e1_predicate") or ""
                e2_pred = r.get("e2_pred") or r.get("e2_predicate") or ""
                e1_created = r.get("e1_created") or r.get("e1_created_at")
                e2_created = r.get("e2_created") or r.get("e2_created_at")
                mean_conf = (e1_c + e2_c) / 2.0
                src1 = r.get("e1_src") or r.get("s_name")
                dst1 = r.get("e1_dst") or r.get("mid_name")
                src2 = r.get("e2_src") or r.get("mid_name")
                dst2 = r.get("e2_dst") or r.get("t_name")
                paths.append({
                    "hops": 2,
                    "source_id": r.get("s_id"),
                    "source_name": r.get("s_name"),
                    "target_id": r.get("t_id"),
                    "target_name": r.get("t_name"),
                    "summary": f"{src1} {e1_pred} {dst1} -> {src2} {e2_pred} {dst2}",
                    "confidence": mean_conf,
                    "trust_weight": 1.0,
                    "created_at": e2_created or e1_created,
                    "path_edges": [
                        {
                            "hop": 1,
                            "source": src1,
                            "predicate": e1_pred,
                            "target": dst1,
                            "edge_id": r.get("e1_id"),
                            "description": r.get("e1_desc"),
                            "scope": r.get("e1_scope", "default"),
                            "confidence": e1_c,
                        },
                        {
                            "hop": 2,
                            "source": src2,
                            "predicate": e2_pred,
                            "target": dst2,
                            "edge_id": r.get("e2_id"),
                            "description": r.get("e2_desc"),
                            "scope": r.get("e2_scope", "default"),
                            "confidence": e2_c,
                        }
                    ],
                })
        except Exception as exc:
            logger.error("Error executing 2-hop traversal: %s", exc)

        return paths


multihop_traverser = MultiHopTraverser()
