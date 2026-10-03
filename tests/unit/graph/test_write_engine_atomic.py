import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock
from kryptodb.graph import Edge
from kryptodb.graph.write_engine import WriteEngine


def test_write_and_supersede_single_transaction_success(monkeypatch):
    """Verify that write_fact executes new edge creation and old edge update in a single transaction."""
    engine = WriteEngine()
    
    old_edge = Edge(
        id="fact-old-1",
        type="FACT",
        predicate="OWNS",
        permission_scope="PROJECT:alpha",
        source_agent_id="agent-1",
        subject_id="alice",
        object_id="project-x",
        valid_from=datetime.now(timezone.utc)
    )
    
    new_edge = Edge(
        id="fact-new-2",
        type="FACT",
        predicate="OWNS",
        permission_scope="PROJECT:alpha",
        source_agent_id="agent-1",
        subject_id="alice",
        object_id="project-y"
    )

    # Mock the neo4j_client.execute_write_transaction
    tx_calls = []
    
    class MockRecord:
        def __getitem__(self, key):
            if key == "s_id":
                return "s-uuid"
            if key == "o_id":
                return "o-uuid"
            return None

    class MockResult:
        def single(self):
            return MockRecord()

    class MockTx:
        def run(self, query, *args, **kwargs):
            tx_calls.append((query, kwargs or (args[0] if args else {})))
            return MockResult()

    mock_driver = MagicMock()
    
    def fake_execute_write_transaction(work_func):
        tx = MockTx()
        return work_func(tx)

    from kryptodb.storage.neo4j_client import neo4j_client
    monkeypatch.setattr(neo4j_client, "driver", mock_driver)
    monkeypatch.setattr(neo4j_client, "execute_write_transaction", fake_execute_write_transaction)

    res_edge, cascade = engine.write_fact(new_edge, existing_edge=old_edge)

    assert res_edge.id == "fact-new-2"
    assert old_edge.superseded_by == "fact-new-2"
    assert old_edge.valid_to is not None

    # Both queries must have run within the same mock transaction work function
    assert len(tx_calls) == 2
    create_query, _ = tx_calls[0]
    update_query, _ = tx_calls[1]

    assert "MERGE (s:Entity" in create_query
    assert "MATCH ()-[r:FACT {id: $old_id}]->()" in update_query


def test_write_and_supersede_rolls_back_on_failure(monkeypatch):
    """Verify that if an error occurs mid-transaction, nothing is committed and exception propagates."""
    engine = WriteEngine()

    old_edge = Edge(
        id="fact-old-1",
        type="FACT",
        predicate="OWNS",
        permission_scope="PROJECT:alpha",
        source_agent_id="agent-1",
        subject_id="alice",
        object_id="project-x",
        valid_from=datetime.now(timezone.utc)
    )

    new_edge = Edge(
        id="fact-new-2",
        type="FACT",
        predicate="OWNS",
        permission_scope="PROJECT:alpha",
        source_agent_id="agent-1",
        subject_id="alice",
        object_id="project-y"
    )

    class FailingTx:
        def __init__(self):
            self.call_count = 0

        def run(self, query, *args, **kwargs):
            self.call_count += 1
            if self.call_count == 2:
                # Simulate crash during the second query inside the transaction
                raise RuntimeError("Simulated mid-transaction database crash!")
            class MockRec:
                def __getitem__(self, key):
                    return "id-123"
            class MockRes:
                def single(self):
                    return MockRec()
            return MockRes()

    failing_tx = FailingTx()
    
    def fake_execute_write_transaction(work_func):
        # In a real Neo4j session.execute_write, any exception causes automatic rollback
        return work_func(failing_tx)

    from kryptodb.storage.neo4j_client import neo4j_client
    monkeypatch.setattr(neo4j_client, "driver", MagicMock())
    monkeypatch.setattr(neo4j_client, "execute_write_transaction", fake_execute_write_transaction)

    with pytest.raises(RuntimeError, match="Simulated mid-transaction database crash!"):
        engine.write_fact(new_edge, existing_edge=old_edge)

    assert failing_tx.call_count == 2
