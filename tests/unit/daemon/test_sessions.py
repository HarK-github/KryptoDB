from kryptodb.daemon.sessions import SessionStore


def test_sessions_have_isolated_keys():
    store = SessionStore()
    first = store.create("agent-a", "key-a", {"scope-a"})
    second = store.create("agent-b", "key-b", {"scope-b"})

    first.keys["scope-a"] = b"a"

    assert second.keys == {}


def test_expired_session_clears_keys():
    store = SessionStore()
    session = store.create("agent-a", "key-a", {"scope-a"})
    session.keys["scope-a"] = b"a"
    session.expires_at = session.expires_at.replace(year=2000)

    assert store.get(session.session_id) is None
    assert session.keys == {}


def test_agent_scope_change_revokes_all_agent_sessions():
    store = SessionStore()
    first = store.create("agent-a", "key-a", {"scope-a"})
    second = store.create("agent-a", "key-b", {"scope-a"})
    other = store.create("agent-b", "key-c", {"scope-b"})

    store.revoke_for_agent("agent-a")

    assert store.get(first.session_id) is None
    assert store.get(second.session_id) is None
    assert store.get(other.session_id) is not None
