import pytest
import sqlite3
import hashlib
from kryptodb.integrity.audit_log import AuditLogger
from kryptodb.integrity.content_hash import hash_entity, hash_fact
from kryptodb.graph.schema import Edge, Node

def test_audit_entry_hash_is_sha256_of_previous_hash_and_entry(tmp_path):
    db_path = tmp_path / "audit.db"
    logger = AuditLogger(db_path)
    logger.log_mutation("CREATE", "agent1", "edge1", "scope1")
    assert logger.verify_chain() == True

def test_audit_chain_detects_modified_entry(tmp_path):
    db_path = tmp_path / "audit.db"
    logger = AuditLogger(db_path)
    logger.log_mutation("CREATE", "agent1", "edge1", "scope1")
    logger.log_mutation("CREATE", "agent2", "edge2", "scope2")
    
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE audit_log SET entry_json = '{}' WHERE seq = 2")
        
    assert logger.verify_chain() == False

def test_audit_chain_detects_reordered_entries(tmp_path):
    db_path = tmp_path / "audit.db"
    logger = AuditLogger(db_path)
    logger.log_mutation("CREATE", "agent1", "edge1", "scope1")
    logger.log_mutation("CREATE", "agent2", "edge2", "scope2")
    
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE audit_log SET seq = 99 WHERE seq = 2")
        conn.execute("UPDATE audit_log SET seq = 2 WHERE seq = 3")
        conn.execute("UPDATE audit_log SET seq = 3 WHERE seq = 99")
        
    assert logger.verify_chain() == False

def test_first_audit_entry_has_defined_genesis_state(tmp_path):
    db_path = tmp_path / "audit.db"
    logger = AuditLogger(db_path)
    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute("SELECT seq, entry_json, entry_hash FROM audit_log WHERE seq = 1")
        row = cursor.fetchone()
        assert row[0] == 1
        assert row[1] == "{}"
        assert row[2] == hashlib.sha256(b"GENESIS").hexdigest()

def test_same_entity_content_hash_matches():
    n1 = Node(id="1", type="FACT", predicate="PERSON", permission_scope="TEST", name_ct="A", description_ct="B")
    n2 = Node(id="1", type="FACT", predicate="PERSON", permission_scope="TEST", name_ct="A", description_ct="B")
    assert hash_entity(n1) == hash_entity(n2)

def test_changed_entity_content_hash_differs():
    n1 = Node(id="1", type="FACT", predicate="PERSON", permission_scope="TEST", name_ct="A", description_ct="B")
    n2 = Node(id="1", type="FACT", predicate="PERSON", permission_scope="TEST", name_ct="A", description_ct="C")
    assert hash_entity(n1) != hash_entity(n2)

def test_same_fact_identity_hash_matches():
    e1 = Edge(id="1", type="FACT", predicate="KNOWS", permission_scope="TEST", source_agent_id="agent1", subject_id="A", object_id="B")
    e2 = Edge(id="1", type="FACT", predicate="KNOWS", permission_scope="TEST", source_agent_id="agent1", subject_id="A", object_id="B")
    assert hash_fact(e1) == hash_fact(e2)

def test_conflict_identity_is_deterministic():
    e1 = Edge(id="1", type="FACT", predicate="KNOWS", permission_scope="TEST", source_agent_id="agent1", subject_id="A", object_id="B")
    e2 = Edge(id="1", type="FACT", predicate="KNOWS", permission_scope="TEST", source_agent_id="agent2", subject_id="A", object_id="B")
    assert hash_fact(e1) == hash_fact(e2)
