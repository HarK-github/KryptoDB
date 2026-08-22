import pytest
from langchain_core.messages import HumanMessage
from kryptodb.agent.store import KryptoDBStore, sanitize_label
from kryptodb.agent.extractor import extract_facts_from_messages, FactItem
from kryptodb.agent.graph import get_compiled_graph, AgentState


def test_kryptodb_store_formatting():
    """Verify namespace formatting and dot sanitization for BaseStore compliance."""
    ns = KryptoDBStore.format_namespace("RESEARCH.malware", "IP.10.0.0.1")
    assert ns == ("RESEARCH_malware", "IP_10_0_0_1")
    assert "." not in ns[0]
    assert "." not in ns[1]


def test_kryptodb_store_get_put_search():
    """Verify KryptoDBStore put, get, search and structural scope authorization."""
    store = KryptoDBStore(
        agent_id="agent-007",
        authorized_scopes=["RESEARCH:malware", "GLOBAL"]
    )

    ns_auth = KryptoDBStore.format_namespace("RESEARCH:malware", "host-1")
    fact_val = {
        "subject": "host-1",
        "predicate": "INFECTED_BY",
        "obj": "Ransomware_X",
        "scope": "RESEARCH:malware"
    }

    # 1. Put item
    store.put(ns_auth, "fact_threat", fact_val)

    # 2. Get item in authorized scope
    item = store.get(ns_auth, "fact_threat")
    assert item is not None
    assert "subject" in item.value
    assert "predicate" in item.value

    # 3. Get item in unauthorized scope (structural enforcement -> None)
    ns_unauth = KryptoDBStore.format_namespace("CLASSIFIED:top_secret", "host-1")
    unauth_item = store.get(ns_unauth, "fact_threat")
    assert unauth_item is None

    # 4. Search in authorized scope
    search_res = store.search(KryptoDBStore.format_namespace("RESEARCH:malware"), query="host-1")
    assert isinstance(search_res, list)


def test_structured_fact_extraction():
    """Verify Trustcall / Pydantic structured fact extraction."""
    messages = [HumanMessage(content="Host 10.0.0.5 was infected with Trojan.AA")]
    facts = extract_facts_from_messages(
        messages=messages,
        agent_id="malware-detector",
        user_scope="RESEARCH:malware"
    )

    assert len(facts) > 0
    first_fact = facts[0]
    assert "subject" in first_fact
    assert "predicate" in first_fact
    assert "obj" in first_fact
    assert first_fact["scope"] == "RESEARCH:malware"


def test_graph_with_kryptodb_store():
    """Verify end-to-end graph execution wired with KryptoDBStore."""
    store = KryptoDBStore(agent_id="malware-detector", authorized_scopes=["RESEARCH:malware"])
    graph = get_compiled_graph(store=store)

    initial_state = {
        "messages": [HumanMessage(content="Scan active infected hosts")],
        "user_scope": "RESEARCH:malware",
        "agent_id": "malware-detector"
    }

    result = graph.invoke(initial_state)

    assert result["status"] == "written"
    assert result["user_scope"] == "RESEARCH:malware"
    assert len(result["pending_facts"]) > 0
