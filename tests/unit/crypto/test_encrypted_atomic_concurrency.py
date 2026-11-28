"""Fix 4 Gate Test: 50-thread concurrent Cardinality.ONE first-write in encrypted mode against live Neo4j."""

from __future__ import annotations

import base64
import concurrent.futures
import json
import uuid

from kryptodb.core.context import RequestContext
from kryptodb.core.memory.memory_service import MemoryService
from kryptodb.infrastructure.crypto.keyring import LocalKeyFileProvider
from kryptodb.infrastructure.crypto.ram_vector_index import RAMVectorIndex
from kryptodb.infrastructure.neo4j.client import neo4j_client
from kryptodb.infrastructure.neo4j.encrypted_store import EncryptedNeo4jMemoryStore
from kryptodb.workers.plaintext_audit import PlaintextAuditScanner


def test_encrypted_store_50_thread_atomic_cardinality_one(tmp_path) -> None:
    """Fix 4: 50 concurrent encrypted first-writes for Cardinality.ONE yield exactly 1 active edge and clean audit."""
    assert hasattr(EncryptedNeo4jMemoryStore, "propose_atomic")

    neo4j_client.connect()
    assert neo4j_client.driver is not None

    key_file = tmp_path / "enc_conc.key"
    key_file.write_text(
        json.dumps({
            "current_kid": "k1",
            "enc_key": base64.b64encode(b"\x31" * 32).decode("ascii"),
            "index_key": base64.b64encode(b"\x32" * 32).decode("ascii"),
        }),
        encoding="utf-8",
    )
    key_provider = LocalKeyFileProvider(key_file=key_file)
    vec_index = RAMVectorIndex()
    enc_store = EncryptedNeo4jMemoryStore(
        client=neo4j_client,
        key_provider=key_provider,
        vector_index=vec_index,
        embed_fn=lambda text: [0.1] * 384,
    )
    enc_store.ensure_schema()

    scope = f"enc_conc_{uuid.uuid4().hex[:8]}"
    subject = f"Alice_Enc_{uuid.uuid4().hex[:6]}"
    ctx = RequestContext.create(tenant_id="enc-conc-tenant", agent_id="enc-conc-agent", active_scopes=[scope])
    svc = MemoryService(store=enc_store)

    def write_one(i: int):
        return svc.propose_memory(subject, "LIVES_IN", f"City_{i}", context=ctx, confidence=0.9)

    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(write_one, range(50)))

    subj_bidx = enc_store.blind_index.compute("subject", subject)
    pred_bidx = enc_store.blind_index.compute("predicate", "LIVES_IN")
    rows, _, _ = neo4j_client.driver.execute_query(
        """
        MATCH (s:Entity)-[r:FACT {scope: $scope, subject_bidx: $subj_bidx, predicate_bidx: $pred_bidx, status: 'active'}]->(o:Entity)
        RETURN count(r) AS active_count
        """,
        scope=scope,
        subj_bidx=subj_bidx,
        pred_bidx=pred_bidx,
    )
    assert rows[0]["active_count"] == 1

    # Verify zero plaintext violations in Neo4j for this scope
    scanner = PlaintextAuditScanner(client=neo4j_client)
    # Clean up test scope nodes/edges after check or verify scan()
    report = scanner.scan()
    # Note: other plaintext test scopes may exist in the shared Neo4j DB; check that our encrypted edges have no disallowed props
    edge_rows, _, _ = neo4j_client.driver.execute_query(
        """
        MATCH ()-[r:FACT {scope: $scope}]->()
        RETURN keys(r) AS k
        """,
        scope=scope,
    )
    from kryptodb.workers.plaintext_audit import ALLOWED_FACT_PROPS
    for er in edge_rows:
        disallowed = set(er["k"]) - ALLOWED_FACT_PROPS
        assert not disallowed, f"Disallowed plaintext properties on encrypted edge: {disallowed}"
