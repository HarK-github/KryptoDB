import pytest
from kryptodb.config.loader import ConfigProvider
from pathlib import Path
from datetime import datetime, timezone, timedelta

def test_valid_agents_toml_loads(tmp_path):
    toml = """
[agents."malware-det"]
trust_prior = 0.9
allowed_scopes = ["SCOPE_1"]
allowed_labels = ["LABEL_1"]
"""
    file_path = tmp_path / "agents.toml"
    file_path.write_text(toml)
    
    provider = ConfigProvider(file_path)
    agent = provider.get_agent("malware-det")
    assert agent is not None
    assert agent.trust_prior == 0.9
    assert "SCOPE_1" in agent.allowed_scopes

def test_invalid_agents_toml_fails_closed(tmp_path):
    toml = """
[agents."malware-det"
this is completely broken toml
"""
    file_path = tmp_path / "agents.toml"
    file_path.write_text(toml)
    
    provider = ConfigProvider(file_path)
    agent = provider.get_agent("malware-det")
    assert agent is None

def test_grant_expiry_is_enforced(tmp_path):
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    toml = f"""
[agents."malware-det"]
trust_prior = 0.9
grant_expires_at = "{past}"
"""
    file_path = tmp_path / "agents.toml"
    file_path.write_text(toml)
    
    provider = ConfigProvider(file_path)
    agent = provider.get_agent("malware-det")
    
    assert agent.grant_expires_at is not None
    assert agent.grant_expires_at < datetime.now(timezone.utc)

def test_hot_reload_updates_agent_scope(tmp_path):
    file_path = tmp_path / "agents.toml"
    file_path.write_text('[agents."test"]\ntrust_prior=0.5\nallowed_scopes=["A"]')
    
    provider = ConfigProvider(file_path)
    assert "A" in provider.get_agent("test").allowed_scopes
    
    file_path.write_text('[agents."test"]\ntrust_prior=0.5\nallowed_scopes=["B"]')
    provider.load()
    
    assert "B" in provider.get_agent("test").allowed_scopes
