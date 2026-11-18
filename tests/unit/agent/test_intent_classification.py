"""
Tests for LLM-based Intent Classification in the Agent Graph.

Ensures that:
1. Inquiries / prompts ('What is my favorite color?', 'Where does Alice work?')
   are classified as 'inquiry' (should_search_memory=True, should_store_memory=False)
   and routed to retrieval search only—never saving new facts into memory.
2. Factual statements ('My favorite color is blue', 'Alice works for Acme Corp')
   are classified as 'statement' (should_search_memory=True, should_store_memory=True)
   and routed to memory extraction and durable storage.
3. Chit-chat / pleasantries ('Hello!', 'Thanks!') are classified as 'chitchat'
   and skip memory mutations completely.
"""

import pytest
from langchain_core.messages import HumanMessage, AIMessage
from unittest.mock import MagicMock

from kryptodb.agent.graph import (
    AgentState,
    MessageIntent,
    IntentClassificationResult,
    create_intent_classifier_node,
    create_agent_graph,
    get_compiled_graph,
)


def test_intent_classification_inquiry_routes_to_search_only():
    """Verify an inquiry classifies as should_store_memory=False and does NOT save to memory."""
    mock_llm = MagicMock()
    mock_classifier = MagicMock()
    mock_classifier.invoke.return_value = IntentClassificationResult(
        intent=MessageIntent.INQUIRY,
        should_search_memory=True,
        should_store_memory=False,
        search_keywords="favorite color",
        reasoning="User is querying an existing preference.",
    )
    mock_llm.with_structured_output.return_value = mock_classifier
    mock_llm.bind_tools.return_value = None

    # Mock reasoning completion
    mock_llm.invoke.return_value = AIMessage(content="Your favorite color is not in my memory.")

    graph = get_compiled_graph(llm=mock_llm)

    result = graph.invoke({
        "messages": [HumanMessage(content="What is my favorite color?")],
        "user_scope": "research",
        "agent_id": "test-agent"
    })

    # Assert intent and flags
    assert result["intent"] == "inquiry"
    assert result["should_search_memory"] is True
    assert result["should_store_memory"] is False
    assert result["search_keywords"] == "favorite color"
    # Status must be reasoned, NOT written
    assert result["status"] == "reasoned"
    # No pending facts extracted or written
    assert len(result.get("pending_facts", [])) == 0


def test_intent_classification_statement_routes_to_storage():
    """Verify a factual statement classifies as should_store_memory=True and routes to storage."""
    mock_llm = MagicMock()
    mock_classifier = MagicMock()
    mock_classifier.invoke.return_value = IntentClassificationResult(
        intent=MessageIntent.STATEMENT,
        should_search_memory=True,
        should_store_memory=True,
        search_keywords="favorite color blue",
        reasoning="User is sharing a new personal preference.",
    )
    mock_llm.with_structured_output.return_value = mock_classifier
    mock_llm.bind_tools.return_value = None
    mock_llm.invoke.return_value = AIMessage(content="Got it, your favorite color is blue.")

    graph = get_compiled_graph(llm=mock_llm)

    result = graph.invoke({
        "messages": [HumanMessage(content="My favorite color is blue")],
        "user_scope": "research",
        "agent_id": "test-agent"
    })

    # Assert intent and flags
    assert result["intent"] == "statement"
    assert result["should_search_memory"] is True
    assert result["should_store_memory"] is True
    # Must proceed to write
    assert result["status"] in ("written", "writing")
    assert len(result.get("pending_facts", [])) > 0


def test_intent_classification_chitchat_skips_memory():
    """Verify pleasantries classify as chitchat and bypass memory operations."""
    mock_llm = MagicMock()
    mock_classifier = MagicMock()
    mock_classifier.invoke.return_value = IntentClassificationResult(
        intent=MessageIntent.CHITCHAT,
        should_search_memory=False,
        should_store_memory=False,
        search_keywords="",
        reasoning="Social greeting.",
    )
    mock_llm.with_structured_output.return_value = mock_classifier
    mock_llm.invoke.return_value = AIMessage(content="Hello! How can I assist you today?")

    graph = get_compiled_graph(llm=mock_llm)

    result = graph.invoke({
        "messages": [HumanMessage(content="Good morning!")],
        "user_scope": "research",
        "agent_id": "test-agent"
    })

    assert result["intent"] == "chitchat"
    assert result["should_search_memory"] is False
    assert result["should_store_memory"] is False
    assert result["status"] == "reasoned"
    assert len(result.get("pending_facts", [])) == 0


def test_intent_classification_mixed_decomposes_inquiry_and_info():
    """Verify compound input with BOTH inquiry and info decomposes clauses and triggers both recall and storage."""
    mock_llm = MagicMock()
    mock_classifier = MagicMock()
    mock_classifier.invoke.return_value = IntentClassificationResult(
        intent=MessageIntent.MIXED,
        should_search_memory=True,
        should_store_memory=True,
        inquiry_text="What plans do you have for this week?",
        info_text="I am looking for a vegetarian meal plan for two people",
        search_keywords="meal plans",
        reasoning="Message contains both personal dietary preference to store and an inquiry to answer.",
    )
    mock_llm.with_structured_output.return_value = mock_classifier
    mock_llm.bind_tools.return_value = None
    mock_llm.invoke.return_value = AIMessage(content="Got it! I've saved your preference for vegetarian meals for 2.")

    graph = get_compiled_graph(llm=mock_llm)

    result = graph.invoke({
        "messages": [HumanMessage(content="I am looking for a vegetarian meal plan for two people. What plans do you have for this week?")],
        "user_scope": "research",
        "agent_id": "test-agent"
    })

    assert result["intent"] == "mixed"
    assert result["should_search_memory"] is True
    assert result["should_store_memory"] is True
    assert result["inquiry_text"] == "What plans do you have for this week?"
    assert result["info_text"] == "I am looking for a vegetarian meal plan for two people"
    assert result["search_keywords"] == "meal plans"
    # Must proceed through recall and storage
    assert result["status"] in ("written", "writing")
    assert len(result.get("pending_facts", [])) > 0


def test_intent_classification_mixed_offline_heuristic():
    """Verify offline fallback decomposes compound messages containing both statements and questions."""
    node = create_intent_classifier_node(llm=None)
    state = AgentState(
        messages=[HumanMessage(content="Alice works at Acme Corp. Who is the team manager?")],
        user_scope="research",
        agent_id="test-agent"
    )
    result = node(state)

    assert result["intent"] == "mixed"
    assert result["should_search_memory"] is True
    assert result["should_store_memory"] is True
    assert "Who is the team manager" in (result["inquiry_text"] or "")
    assert "Alice works at Acme Corp" in (result["info_text"] or "")

