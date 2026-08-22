"""
Phase 4: Custom BaseStore Subclass for KryptoDB Encrypted Memory Substrate.
Maps LangGraph (scope, subject) store namespaces directly to encrypted Neo4j partitions.
"""

from typing import Sequence, List, Any, Optional, Tuple, Dict
from datetime import datetime, timezone
import json
from langgraph.store.base import (
    BaseStore,
    Item,
    SearchItem,
    GetOp,
    PutOp,
    SearchOp,
    ListNamespacesOp,
)
from kryptodb.agent.tools import query_memory_tool, propose_fact_tool


def sanitize_label(label: str) -> str:
    """Replaces invalid characters (like dots) in namespace labels for LangGraph store compatibility."""
    return label.replace(".", "_")


class KryptoDBStore(BaseStore):
    """
    Custom LangGraph BaseStore subclass connecting store operations (get/put/search)
    to KryptoDB's encrypted partition substrate via query_memory_tool and propose_fact_tool.
    The store namespace (scope, subject) enforces scope permission boundaries structurally.
    """

    def __init__(
        self,
        agent_id: str = "agent-1",
        authorized_scopes: Optional[List[str]] = None,
        daemon_url: Optional[str] = None
    ):
        super().__init__()
        self.agent_id = agent_id
        self.authorized_scopes = authorized_scopes
        self.daemon_url = daemon_url
        self._local_cache: Dict[Tuple[Tuple[str, ...], str], Item] = {}

    @staticmethod
    def format_namespace(scope: str, subject: Optional[str] = None) -> Tuple[str, ...]:
        """Formats a scope and optional subject into a valid, dot-sanitized LangGraph store namespace tuple."""
        clean_scope = sanitize_label(scope)
        if subject:
            return (clean_scope, sanitize_label(subject))
        return (clean_scope,)

    def _is_authorized(self, scope: str) -> bool:
        """Enforces structural access control checks for requested scopes."""
        if not self.authorized_scopes:
            return True
        return any(
            scope == s or scope == "GLOBAL" or scope.startswith(s + ":")
            for s in self.authorized_scopes
        )

    def batch(self, ops: Sequence[Any]) -> List[Any]:
        """Synchronous batch execution for GetOp, PutOp, SearchOp, and ListNamespacesOp."""
        results = []
        for op in ops:
            if isinstance(op, GetOp):
                ns = tuple(op.namespace)
                scope = ns[0] if len(ns) > 0 else "GLOBAL"
                subject = ns[1] if len(ns) > 1 else op.key

                if not self._is_authorized(scope):
                    # Unauthorized scope access structurally fails by returning None
                    results.append(None)
                    continue

                # Query memory substrate
                res_str = query_memory_tool.invoke({
                    "subject_id": subject,
                    "requested_scope": scope,
                    "mode": "local"
                })

                try:
                    payload = json.loads(res_str) if isinstance(res_str, str) else res_str
                    if payload.get("status") in ("denied_permission", "denied_inactive", "not_found"):
                        results.append(self._local_cache.get((ns, op.key)))
                    else:
                        facts = payload.get("facts", [])
                        if facts:
                            fact_val = facts[0] if isinstance(facts[0], dict) else {"fact": facts[0]}
                            item = Item(
                                namespace=list(ns),
                                key=op.key,
                                value=fact_val,
                                created_at=datetime.now(timezone.utc).isoformat(),
                                updated_at=datetime.now(timezone.utc).isoformat()
                            )
                            results.append(item)
                        else:
                            results.append(self._local_cache.get((ns, op.key)))
                except Exception:
                    results.append(self._local_cache.get((ns, op.key)))

            elif isinstance(op, PutOp):
                ns = tuple(op.namespace)
                scope = ns[0] if len(ns) > 0 else "GLOBAL"
                subject = ns[1] if len(ns) > 1 else self.agent_id

                if op.value is None:
                    self._local_cache.pop((ns, op.key), None)
                    results.append(None)
                    continue

                val = op.value
                predicate = val.get("predicate", "PROCESSED_QUERY")
                obj = val.get("obj") or val.get("object", f"scope:{scope}")

                # Commit fact proposal to encrypted memory substrate
                propose_fact_tool.invoke({
                    "subject": subject,
                    "predicate": predicate,
                    "obj": obj,
                    "scope": scope,
                    "description": val.get("description", "Stored via KryptoDBStore")
                })

                item = Item(
                    namespace=list(ns),
                    key=op.key,
                    value=val,
                    created_at=datetime.now(timezone.utc).isoformat(),
                    updated_at=datetime.now(timezone.utc).isoformat()
                )
                self._local_cache[(ns, op.key)] = item
                results.append(None)

            elif isinstance(op, SearchOp):
                ns_prefix = tuple(op.namespace_prefix)
                scope = ns_prefix[0] if len(ns_prefix) > 0 else "GLOBAL"

                if not self._is_authorized(scope):
                    results.append([])
                    continue

                query_text = op.query or ""
                res_str = query_memory_tool.invoke({
                    "query": query_text,
                    "requested_scope": scope,
                    "mode": "local"
                })

                search_items = []
                try:
                    payload = json.loads(res_str) if isinstance(res_str, str) else res_str
                    facts = payload.get("facts", [])
                    for idx, fact in enumerate(facts):
                        fact_val = fact if isinstance(fact, dict) else {"fact": fact}
                        search_items.append(SearchItem(
                            namespace=list(ns_prefix),
                            key=f"fact_{idx}",
                            value=fact_val,
                            created_at=datetime.now(timezone.utc).isoformat(),
                            updated_at=datetime.now(timezone.utc).isoformat()
                        ))
                except Exception:
                    pass

                results.append(search_items)

            elif isinstance(op, ListNamespacesOp):
                namespaces = list({list(k[0]) for k in self._local_cache.keys()})
                results.append(namespaces)

            else:
                results.append(None)

        return results

    async def abatch(self, ops: Sequence[Any]) -> List[Any]:
        """Asynchronous batch execution delegating to synchronous batch."""
        return self.batch(ops)
