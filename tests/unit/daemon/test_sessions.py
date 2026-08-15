from kryptodb.daemon.sessions import SessionStore


def test_sessions_have_isolated_keys_and_search_indexes():
    store = SessionStore()
    first = store.create("agent-a", "key-a", {"scope-a"})
    second = store.create("agent-b", "key-b", {"scope-b"})

    first.keys["scope-a"] = b"a"
    first.search_index.add_edge({"edge_id": "fact-a", "subject": "Alice", "object": "X", "predicate": "OWNS"})

    assert second.keys == {}
    assert second.search_index.search("Alice") == []


def test_expired_session_clears_search_state():
    store = SessionStore()
    session = store.create("agent-a", "key-a", {"scope-a"})
    session.search_index.add_edge({"edge_id": "fact-a", "subject": "Alice", "object": "X", "predicate": "OWNS"})
    session.expires_at = session.expires_at.replace(year=2000)

    assert store.get(session.session_id) is None
    assert session.keys == {}
    assert session.search_index.search("Alice") == []


def test_agent_scope_change_revokes_all_agent_sessions():
    store = SessionStore()
    first = store.create("agent-a", "key-a", {"scope-a"})
    second = store.create("agent-a", "key-b", {"scope-a"})
    other = store.create("agent-b", "key-c", {"scope-b"})

    store.revoke_for_agent("agent-a")

    assert store.get(first.session_id) is None
    assert store.get(second.session_id) is None
    assert store.get(other.session_id) is not None
