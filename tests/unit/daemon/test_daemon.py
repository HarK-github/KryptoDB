from kryptodb.daemon import SessionKeyring


def test_keyring_starts_empty():
    """a fresh daemon process has an empty in-memory session keyring."""
    keyring = SessionKeyring()
    assert len(keyring.get_active_partitions()) == 0
    assert keyring.get_key("any") is None
