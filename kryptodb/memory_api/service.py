"""
Unified Internal Memory Service Layer.
Single source of truth for executing memory writes and memory recalls across
both MCP tools and the REST gateway.
"""
from typing import Any, Dict, List, Optional, Tuple
import logging
import json
import uuid
import os
from kryptodb.graph import Edge, is_sensitive_scope
from kryptodb.graph.read_engine import ReadEngine
from kryptodb.graph.write_engine import WriteEngine
from kryptodb.integrity.partition_crypto import Encryptor
from kryptodb.storage.neo4j_client import neo4j_client
from kryptodb.storage.redis_client import redis_client, WriteLockError
from kryptodb.daemon.sessions import sessions, SessionState, keyring
from kryptodb.daemon.auth import AuthContext, api_keys

logger = logging.getLogger(__name__)
read_engine = ReadEngine()
write_engine = WriteEngine()

_DEFAULT_AGENTS = {
    "admin": {"id": "admin", "trust_prior": 1.0, "allowed_scopes": ["*"]},
    "malware-det": {"id": "malware-det", "trust_prior": 0.9, "allowed_scopes": ["RESEARCH:malware-detection", "TEST"]},
    "campus-bot": {"id": "campus-bot", "trust_prior": 0.6, "allowed_scopes": ["PROJECT:campus-marketplace", "PROJECT:kryptodb", "PROJECT:synapse", "TEST"]},
    "Agent-1": {"id": "Agent-1", "trust_prior": 0.9, "allowed_scopes": ["PROJECT:alpha", "SCOPE_ALPHA", "PROJECT:synapse", "PROJECT:kryptodb", "research", "ORG:acme-structure", "RESEARCH:threat-intel"]},
    "Agent-2": {"id": "Agent-2", "trust_prior": 0.9, "allowed_scopes": ["PROJECT:beta"]}
}


def get_agent(agent_id: str) -> Dict[str, Any] | None:
    """Minimal agent registry. Loads from data/agents.json if it exists."""
    agents = _DEFAULT_AGENTS.copy()
    json_path = os.path.join(os.path.dirname(__file__), "..", "..", "data", "agents.json")
    if os.path.exists(json_path):
        try:
            with open(json_path, "r") as f:
                disk_agents = json.load(f)
                agents.update(disk_agents)
        except Exception:
            pass
            
    if agent_id in agents:
        return agents[agent_id]
        
    lower_map = {k.lower(): v for k, v in agents.items()}
    return lower_map.get(agent_id.lower())


def get_all_scopes() -> list[str]:
    """Returns all scopes configured across registered agents."""
    agents = _DEFAULT_AGENTS.copy()
    json_path = os.path.join(os.path.dirname(__file__), "..", "..", "data", "agents.json")
    if os.path.exists(json_path):
        try:
            with open(json_path, "r") as f:
                agents.update(json.load(f))
        except Exception:
            pass
    scopes = set()
    for a in agents.values():
        for s in a.get("allowed_scopes", []):
            if s and s != "*":
                scopes.add(s)
    return sorted(scopes)


def admit_fact(agent_id: str, proposed_edge: Edge, existing_edges: list[Edge]) -> str:
    """Trust lookup, conflict check, and admission decision."""
    agent = get_agent(agent_id)
    if not agent:
        return "deny"

    allowed = agent.get("allowed_scopes", [])
    if agent_id != "admin" and allowed and "*" not in allowed:
        if proposed_edge.permission_scope != "GLOBAL" and proposed_edge.permission_scope not in allowed:
            return "deny"
        
    trust_weight = agent.get("trust_prior", 0.9)
    proposed_edge.trust_weight = trust_weight
    
    if not existing_edges:
        if trust_weight >= 0.5:
            return "admit"
        else:
            return "reject"
            
    active_existing = [e for e in existing_edges if e.valid_to is None]
    max_existing_trust = max((e.trust_weight for e in active_existing), default=0.0)
    
    if trust_weight > max_existing_trust:
        return "admit"
    elif trust_weight == max_existing_trust:
        return "arbitrate"
    else:
        return "reject"


def detect_conflict(subject: str, predicate: str, obj: str, permission_scope: str) -> list[Edge]:
    """Two-tier conflict detection. Returns live conflicting edges."""
    encryptor = Encryptor()
    subject_idx = encryptor.compute_search_idx(permission_scope, "subject", subject)
    predicate_idx = encryptor.compute_search_idx(permission_scope, "predicate", predicate)
    query = """
    MATCH (s)-[r]->(o)
    WHERE (
        (r.subject_search_idx = $subject_idx AND r.predicate_search_idx = $predicate_idx)
        OR (s.id = $subject AND r.predicate = $predicate)
      )
      AND r.permission_scope = $scope
      AND r.valid_to IS NULL
    RETURN s, r, o
    """
    records, _, _ = neo4j_client.execute_query_with_retry(
        query,
        subject=subject,
        predicate=predicate,
        subject_idx=subject_idx,
        predicate_idx=predicate_idx,
        scope=permission_scope,
    )
    
    edges = []
    for record in records:
        r = record["r"]
        edges.append(Edge(
            id=r["id"],
            type=r["type"] if "type" in r else "FACT",
            predicate=predicate,
            permission_scope=r["permission_scope"],
            source_agent_id=r.get("source_agent_id", "encrypted"),
            subject_id=subject,
            object_id=obj,
            trust_weight=r["trust_weight"],
            valid_from=r["valid_from"],
            valid_to=r["valid_to"],
            ingested_at=r["ingested_at"],
            unlock_at=r.get("unlock_at"),
            derived_from=r.get("derived_from") or [],
            superseded_by=r.get("superseded_by")
        ))
        
    return edges


def propose_fact(agent_id: str, edge: Edge, is_derived: bool, existing_edges: list[Edge], trace_id: str | None = None) -> tuple[str, Edge | None, dict | None]:
    """Candidate fact submission and admission workflow."""
    try:
        write_engine.assert_lineage(edge, is_derived)
    except ValueError as e:
        return f"error: {str(e)}", None, None
        
    for e in existing_edges:
        if e.id == edge.id:
            return "already_admitted", e, None
            
    decision = admit_fact(agent_id, edge, existing_edges)
    
    if decision == "admit":
        existing_active = next((e for e in existing_edges if e.valid_to is None), None)
        committed_edge, cascade_args = write_engine.write_fact(edge, existing_active, trace_id=trace_id)
        return "admit", committed_edge, cascade_args
    
    return decision, None, None



class MemoryService:
    @staticmethod
    def write_fact(
        proposer_id: str,
        edge: Edge,
        is_derived: bool = False,
        background_tasks: Any = None,
        trace_id: Optional[str] = None
    ) -> Tuple[str, Optional[Edge], Optional[dict]]:
        """
        Single unified write path.
        Enforces (subject, predicate) concurrency lock, detects conflicts,
        and atomically commits fact + supersession.
        """
        trace_id = trace_id or f"trace-{uuid.uuid4().hex[:12]}"

        # Stage 1: Validate
        logger.info(json.dumps({
            "event": "stage_transition",
            "path": "write",
            "stage": "validate",
            "trace_id": trace_id,
            "agent_id": proposer_id,
            "fact_id": edge.id,
            "scope": edge.permission_scope,
            "status": "passed"
        }))

        lock_key = f"{edge.permission_scope}:{edge.subject_id}:{edge.predicate}"
        with redis_client.acquire_lock(lock_key, ttl_seconds=10, timeout_seconds=0.0):
            # Stage 2: Conflict-check
            existing_edges = detect_conflict(
                edge.subject_id,
                edge.predicate,
                edge.object_id,
                edge.permission_scope
            )
            logger.info(json.dumps({
                "event": "stage_transition",
                "path": "write",
                "stage": "conflict_check",
                "trace_id": trace_id,
                "conflicts_found": len(existing_edges),
                "status": "resolved"
            }))

            status, committed_edge, cascade_args = propose_fact(
                proposer_id,
                edge,
                is_derived,
                existing_edges,
                trace_id=trace_id
            )

        if cascade_args and background_tasks:
            from kryptodb.daemon.server import run_cascade_task
            task_id = str(uuid.uuid4())
            background_tasks.add_task(
                run_cascade_task,
                task_id,
                cascade_args["revoked_edge_id"],
                cascade_args["valid_to_iso"],
                cascade_args["superseded_by_id"]
            )

        return status, committed_edge, cascade_args

    @staticmethod
    def query_facts(
        agent_id: str,
        requested_scope: Optional[str] = None,
        subject_id: Optional[str] = None,
        query: Optional[str] = None,
        session_id: Optional[str] = None,
        session: Optional[Any] = None,
        context: Optional[AuthContext] = None,
        trace_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Single unified read path implementing 4-gate verification.
        """
        from kryptodb.integrity.partition_crypto import Encryptor
        from kryptodb.graph.read_engine import ReadEngine
        encryptor = Encryptor()
        trace_id = trace_id or f"trace-{uuid.uuid4().hex[:12]}"

        # Gate 1: Permission check
        if isinstance(context, AuthContext):
            if not context.is_admin and context.agent_id != agent_id:
                logger.info(json.dumps({
                    "event": "gate_transition",
                    "path": "read",
                    "gate": "permission",
                    "trace_id": trace_id,
                    "agent_id": agent_id,
                    "scope": requested_scope,
                    "status": "denied"
                }))
                return {"status": "forbidden", "detail": "Agent identity is bound to API key", "facts": [], "trace_id": trace_id}
            
            if session is None and session_id:
                session = sessions.get(session_id)
            active_scopes = set(session.keys) if session else set()
            agent = get_agent(context.agent_id) if not context.is_admin else {"id": "admin", "allowed_scopes": list(keyring._keys.keys())}
        else:
            if session is None and session_id:
                session = sessions.get(session_id)
            active_scopes = set(session.keys) if session else set(keyring._keys)
            agent = get_agent(agent_id) or {"id": agent_id, "allowed_scopes": list(keyring._keys.keys())}

        if not agent:
            logger.info(json.dumps({
                "event": "gate_transition",
                "path": "read",
                "gate": "permission",
                "trace_id": trace_id,
                "agent_id": agent_id,
                "scope": requested_scope,
                "status": "denied"
            }))
            return {"status": "forbidden", "detail": "Unknown agent", "facts": [], "trace_id": trace_id}

        allowed_scopes = set(agent.get("allowed_scopes", []))
        if "*" in allowed_scopes or (isinstance(context, AuthContext) and context.is_admin and not allowed_scopes):
            allowed_scopes = set(keyring._keys.keys())

        if requested_scope and requested_scope.lower() not in ("all", "*", "global"):
            req_scopes = [s.strip() for s in requested_scope.split(",") if s.strip()]
            for s in req_scopes:
                if s not in allowed_scopes and not (isinstance(context, AuthContext) and context.is_admin):
                    logger.info(json.dumps({
                        "event": "gate_transition",
                        "path": "read",
                        "gate": "permission",
                        "trace_id": trace_id,
                        "agent_id": agent_id,
                        "scope": s,
                        "status": "denied"
                    }))
                    return {"status": "denied_permission", "facts": [], "trace_id": trace_id}

                logger.info(json.dumps({
                    "event": "gate_transition",
                    "path": "read",
                    "gate": "permission",
                    "trace_id": trace_id,
                    "agent_id": agent_id,
                    "scope": s,
                    "status": "passed"
                }))

                # Gate 2: Activation check
                if s not in active_scopes and not (isinstance(context, AuthContext) and context.is_admin):
                    logger.info(json.dumps({
                        "event": "gate_transition",
                        "path": "read",
                        "gate": "activation",
                        "trace_id": trace_id,
                        "agent_id": agent_id,
                        "scope": s,
                        "status": "denied"
                    }))
                    return {"status": "denied_inactive", "facts": [], "trace_id": trace_id}
            scopes_to_query = req_scopes
        else:
            if isinstance(context, AuthContext) and context.is_admin:
                scopes_to_query = sorted(allowed_scopes or set(keyring._keys.keys()))
            else:
                scopes_to_query = sorted(allowed_scopes & (active_scopes or allowed_scopes))

        logger.info(json.dumps({
            "event": "gate_transition",
            "path": "read",
            "gate": "permission",
            "trace_id": trace_id,
            "agent_id": agent_id,
            "scopes": scopes_to_query,
            "status": "passed"
        }))

        if isinstance(context, AuthContext) and context.is_admin:
            for scope in scopes_to_query:
                if scope not in keyring._keys:
                    try:
                        keyring.add_key(scope, encryptor._get_or_create_key(scope))
                    except Exception as e:
                        logger.debug(f"Scope {scope} key not available: {e}")
                if session and scope not in session._keys:
                    k = keyring.get_key(scope)
                    if k:
                        session.add_key(scope, k)
            effective_scopes = [s for s in scopes_to_query if s in keyring._keys]
        else:
            effective_scopes = list(set(scopes_to_query) & (active_scopes or set(keyring._keys.keys())))

        if not effective_scopes:
            logger.info(json.dumps({
                "event": "gate_transition",
                "path": "read",
                "gate": "activation",
                "trace_id": trace_id,
                "agent_id": agent_id,
                "status": "denied"
            }))
            return {"status": "denied_inactive", "facts": [], "trace_id": trace_id}

        logger.info(json.dumps({
            "event": "gate_transition",
            "path": "read",
            "gate": "activation",
            "trace_id": trace_id,
            "agent_id": agent_id,
            "status": "passed",
            "effective_scopes": effective_scopes
        }))

        # Gate 3 & 4: Execute query via ReadEngine
        engine = ReadEngine(
            session_keyring=session,
        )
        facts = engine.retrieve_active_facts(
            allowed_scopes=effective_scopes,
            subject_id=subject_id,
            query_str=query,
            trace_id=trace_id
        )

        if not facts:
            return {"status": "not_found", "facts": [], "trace_id": trace_id}

        return {"status": "ok", "mode": "local", "facts": facts, "trace_id": trace_id}

    @staticmethod
    def remember(
        agent_id: str,
        fact: Dict[str, Any],
        scope: str,
        session_id: Optional[str] = None,
        context: Optional[AuthContext] = None,
        background_tasks: Any = None,
        trace_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Core Verb: remember(fact, scope, session_id?)
        Proposes and (subject to HITL) commits a fact.
        """
        trace_id = trace_id or f"trace-{uuid.uuid4().hex[:12]}"
        agent = get_agent(agent_id) if not (context and context.is_admin) else {"id": "admin", "allowed_scopes": [scope]}
        if not agent:
            return {"status": "forbidden", "detail": "Unknown agent", "trace_id": trace_id}

        allowed_scopes = set(agent.get("allowed_scopes", []))
        if "*" not in allowed_scopes and not (context and context.is_admin):
            if scope not in allowed_scopes:
                return {"status": "denied_permission", "detail": f"Agent {agent_id} lacks permission for scope {scope}", "trace_id": trace_id}

        # Check sensitive scope approval
        if is_sensitive_scope(scope) and not fact.get("approved"):
            logger.info(json.dumps({
                "event": "stage_transition",
                "path": "write",
                "stage": "hitl",
                "trace_id": trace_id,
                "scope": scope,
                "status": "hitl_required"
            }))
            return {
                "status": "hitl_required",
                "scope": scope,
                "candidate_fact": fact,
                "detail": f"High-sensitivity scope '{scope}' requires explicit human approval.",
                "trace_id": trace_id
            }

        logger.info(json.dumps({
            "event": "stage_transition",
            "path": "write",
            "stage": "hitl",
            "trace_id": trace_id,
            "scope": scope,
            "status": "approved"
        }))

        edge = Edge(
            id=fact.get("id") or str(uuid.uuid4()),
            type="FACT",
            subject_id=fact.get("subject") or fact.get("subject_id") or agent_id,
            predicate=fact.get("predicate", "FACT"),
            object_id=fact.get("object") or fact.get("obj") or fact.get("object_id") or "UNKNOWN",
            permission_scope=scope,
            description=fact.get("description"),
            dataset_id=fact.get("dataset_id"),
            source_agent_id=agent_id
        )

        status, committed_edge, cascade_args = MemoryService.write_fact(
            proposer_id=agent_id,
            edge=edge,
            is_derived=fact.get("is_derived", False),
            background_tasks=background_tasks,
            trace_id=trace_id
        )

        return {
            "status": status,
            "edge": committed_edge.model_dump(mode="json") if committed_edge else None,
            "cascade_args": cascade_args,
            "trace_id": trace_id
        }

    @staticmethod
    def recall(
        agent_id: str,
        query: Optional[str] = None,
        scope: Optional[str] = None,
        subject_id: Optional[str] = None,
        session_id: Optional[str] = None,
        context: Optional[AuthContext] = None,
        trace_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Core Verb: recall(query, scope, session_id?)
        The 4-gate verification read path.
        """
        return MemoryService.query_facts(
            agent_id=agent_id,
            requested_scope=scope,
            subject_id=subject_id,
            query=query,
            session_id=session_id,
            context=context,
            trace_id=trace_id
        )

    @staticmethod
    def forget(
        agent_id: str,
        scope: str,
        fact_id: Optional[str] = None,
        subject: Optional[str] = None,
        predicate: Optional[str] = None,
        session_id: Optional[str] = None,
        context: Optional[AuthContext] = None,
        approved: bool = False,
        trace_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Core Verb: forget(fact_id | subject+predicate, scope)
        Explicit retraction/deletion path.
        - Purges blind-index entries and ciphertext on matching edges so terms become unsearchable.
        - Enforces HITL approval for sensitive scopes.
        - Records an audit ledger entry (who forgot what, when - not forgotten content).
        """
        from datetime import datetime, timezone
        from kryptodb.storage.neo4j_client import neo4j_client
        from kryptodb.daemon import event_bus, GraphEvent
        from kryptodb.integrity.partition_crypto import Encryptor
        trace_id = trace_id or f"trace-{uuid.uuid4().hex[:12]}"

        # Permission verification
        agent = get_agent(agent_id) if not (context and context.is_admin) else {"id": "admin", "allowed_scopes": [scope]}
        if not agent:
            return {"status": "forbidden", "detail": "Unknown agent"}

        allowed_scopes = set(agent.get("allowed_scopes", []))
        if "*" not in allowed_scopes and not (context and context.is_admin):
            if scope not in allowed_scopes:
                return {"status": "denied_permission", "detail": f"Agent {agent_id} lacks permission for scope {scope}"}

        # HITL gating for sensitive scopes
        if is_sensitive_scope(scope) and not approved:
            return {
                "status": "hitl_required",
                "scope": scope,
                "target": {"fact_id": fact_id, "subject": subject, "predicate": predicate},
                "detail": f"Retracting facts in sensitive scope '{scope}' requires explicit human approval."
            }

        if not fact_id and not (subject and predicate):
            return {
                "status": "invalid_request",
                "detail": "Must provide either fact_id or both subject and predicate to forget."
            }

        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        lock_target = fact_id or f"{subject}:{predicate}"
        lock_key = f"forget:{scope}:{lock_target}"

        retracted_ids = []
        with redis_client.acquire_lock(lock_key, ttl_seconds=10, timeout_seconds=0.0):
            if neo4j_client.driver:
                if fact_id:
                    query = """
                    MATCH ()-[r:FACT {id: $fact_id, permission_scope: $scope}]->()
                    WHERE r.valid_to IS NULL
                    SET r.valid_to = $now,
                        r.revoked_at = $now,
                        r.revocation_reason = 'forgotten',
                        r.search_indexes = [],
                        r.subject_search_idx = NULL,
                        r.predicate_search_idx = NULL,
                        r.object_search_idx = NULL,
                        r.knowledge_ct = NULL,
                        r.wrapped_dek = NULL,
                        r.pending_envelope = NULL
                    RETURN r.id AS id
                    """
                    params = {"fact_id": fact_id, "scope": scope, "now": now_iso}
                else:
                    encryptor = Encryptor()
                    sub_idx = encryptor.compute_blind_idx(scope, subject)
                    query = """
                    MATCH (s)-[r:FACT {permission_scope: $scope}]->()
                    WHERE r.valid_to IS NULL
                      AND (s.name_blind_idx = $sub_idx OR r.subject_search_idx = $sub_idx OR ANY(idx IN coalesce(r.search_indexes, []) WHERE idx = $sub_idx))
                    SET r.valid_to = $now,
                        r.revoked_at = $now,
                        r.revocation_reason = 'forgotten',
                        r.search_indexes = [],
                        r.subject_search_idx = NULL,
                        r.predicate_search_idx = NULL,
                        r.object_search_idx = NULL,
                        r.knowledge_ct = NULL,
                        r.wrapped_dek = NULL,
                        r.pending_envelope = NULL
                    RETURN r.id AS id
                    """
                    params = {"sub_idx": sub_idx, "scope": scope, "now": now_iso}

                records, _, _ = neo4j_client.execute_query_with_retry(query, trace_id=trace_id, **params)
                retracted_ids = [r["id"] for r in records if r.get("id")]
            else:
                retracted_ids = [fact_id or "simulated-retracted-id"]

        logger.info(json.dumps({
            "event": "stage_transition",
            "path": "forget",
            "stage": "retraction",
            "trace_id": trace_id,
            "scope": scope,
            "retracted_count": len(retracted_ids),
            "target": fact_id or f"{subject}:{predicate}"
        }))

        # Audit ledger entry (recording actor, scope, target, timestamp - NEVER plaintext content)
        audit_entry = GraphEvent(
            event_type="fact_forgotten",
            permission_scope=scope,
            timestamp=now,
            edge_id=fact_id or f"{subject}:{predicate}",
            source_id=agent_id
        )
        event_bus.record_audit(audit_entry)

        return {
            "status": "forgotten",
            "scope": scope,
            "retracted_count": len(retracted_ids),
            "fact_ids": retracted_ids,
            "timestamp": now_iso,
            "trace_id": trace_id
        }




memory_service = MemoryService()
