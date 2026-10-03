import pytest
from kryptodb.daemon import SessionKeyring

def test_daemon_binds_localhost_only():
    """daemon configuration binds to 127.0.0.1, never 0.0.0.0 or an externally reachable interface."""
    import ast
    from pathlib import Path
    
    app_path = Path("kryptodb/daemon/server.py")
    tree = ast.parse(app_path.read_text())
    
    bind_ip = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute) and node.func.attr == "run":
                for kw in node.keywords:
                    if kw.arg == "host":
                        bind_ip = kw.value.value
                        
    assert bind_ip == "127.0.0.1", "Daemon must bind to 127.0.0.1"

def test_keyring_starts_empty():
    """a fresh daemon process has an empty in-memory session keyring."""
    keyring = SessionKeyring()
    assert len(keyring.get_active_partitions()) == 0
    assert keyring.get_key("any") is None
