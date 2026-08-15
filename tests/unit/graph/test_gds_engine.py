import pytest
from kryptodb.graph.gds_engine import GDSEngine
from kryptodb.storage.neo4j_client import neo4j_client

def test_gds_engine_instantiation():
    gds = GDSEngine()
    assert gds is not None

def test_gds_get_communities_empty_when_no_driver(monkeypatch):
    monkeypatch.setattr(neo4j_client, "driver", None)
    gds = GDSEngine()
    assert gds.run_louvain_community_detection("TEST") is False
    assert gds.get_communities(["TEST"]) == {}
