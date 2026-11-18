#!/usr/bin/env python3
"""
scripts/replay_transcript.py - Replays the observed session transcript turn-by-turn.

Dumps for each turn:
- Raw user text
- Intent classifier decision
- Facts proposed
- Recalled facts (IDs, scores, text)
- Injected agent prompt context
- Facts written / committed

Usage:
    uv run python scripts/replay_transcript.py [--debug] [--live]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from typing import Any, Dict, List, Optional

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from kryptodb.agent_runtime.langgraph.graph import (
    AgentState,
    create_intent_classifier_node,
    create_recall_node,
    create_reasoning_node,
    create_extraction_node,
    create_direct_write_node,
    get_compiled_graph,
    extract_facts_from_messages,
)
from kryptodb.core.retrieval import retrieval_service

logger = logging.getLogger("kryptodb.replay")

TRANSCRIPT_TURNS = [
    {
        "turn": 1,
        "input": "explain about harshi",
        "mock_agent_response": (
            "Harshi is a person I know and she has a background in technology and often speaks up at "
            "tech conferences. There are no factual connections ... between Harshit and cookies ..."
        ),
        "user_decision": "reject",
    },
    {
        "turn": 2,
        "input": "tell me about harshit kandpal",
        "mock_agent_response": (
            "Harshit Kandpal is a software engineer who lives in India..."
        ),
        "user_decision": "none",
    },
    {
        "turn": 3,
        "input": (
            "harshit kandpal lives in india and he is very interesting person, lfx entee too . "
            "also what does he like in food?"
        ),
        "mock_agent_response": (
            "Harshit likes to read books. Based on the retrieved graph facts and personal preference "
            "shared by the user, Harshit also enjoys cookies and has a favorite color of blue."
        ),
        "user_decision": "approve",
    },
    {
        "turn": 4,
        "input": "tell me about harshit what does he like?",
        "mock_agent_response": (
            "Harshit likes cookies and reading books."
        ),
        "user_decision": "reject",
    },
]


def setup_logging(debug: bool = False):
    level = logging.DEBUG if debug else logging.INFO
    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s", datefmt="%H:%M:%S"
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    
    root = logging.getLogger("kryptodb")
    root.setLevel(level)
    root.handlers = [handler]
    
    # Silence verbose 3rd party loggers
    for mod in ("urllib3", "httpx", "httpcore", "neo4j"):
        logging.getLogger(mod).setLevel(logging.WARNING)


def run_replay(live: bool = False, debug: bool = False):
    setup_logging(debug=debug)
    print("=" * 80)
    print("KryptoDB Transcript Replay & Diagnostic Harness")
    print(f"Mode: {'LIVE LLM/DAEMON' if live else 'STUBBED/OFFLINE'} | Debug: {debug}")
    print("=" * 80)

    llm = None
    if live:
        try:
            from kryptodb.infrastructure.neo4j.client import neo4j_client
            neo4j_client.connect()
        except Exception as exc:
            print(f"Could not connect neo4j: {exc}")
        try:
            from kryptodb.interfaces.http.routes.chat import get_active_llm
            llm = get_active_llm()
            print(f"Connected to LLM: {getattr(llm, 'model', 'unknown')}")
        except Exception as exc:
            print(f"Could not load live LLM: {exc}. Running with stubbed fallback.")

    classifier_node = create_intent_classifier_node(llm=llm)
    extraction_node = create_extraction_node(llm=llm)

    conversation_history: List[Any] = []
    accumulated_recalled_facts: List[Dict[str, Any]] = []

    for item in TRANSCRIPT_TURNS:
        turn_num = item["turn"]
        user_text = item["input"]
        mock_reply = item["mock_agent_response"]
        user_dec = item["user_decision"]

        print(f"\n{'─' * 80}")
        print(f"TURN {turn_num}")
        print(f"{'─' * 80}")
        print(f"1. RAW USER TEXT:\n   \"{user_text}\"")

        # 1. Intent Classification
        state = AgentState(
            messages=conversation_history + [HumanMessage(content=user_text)],
            user_scope="research",
            agent_id="test-agent",
            recalled_facts=accumulated_recalled_facts,
        )
        classifier_output = classifier_node(state)
        print("\n2. CLASSIFIER DECISION:")
        print(f"   Intent:              {classifier_output.get('intent')}")
        print(f"   Should Search:       {classifier_output.get('should_search_memory')}")
        print(f"   Should Store:        {classifier_output.get('should_store_memory')}")
        print(f"   Inquiry Text:        {classifier_output.get('inquiry_text')}")
        print(f"   Info Text:           {classifier_output.get('info_text')}")
        print(f"   Search Keywords:     {classifier_output.get('search_keywords')}")

        # Update state with classifier decision
        for k, v in classifier_output.items():
            setattr(state, k, v)

        # 2. Retrieval / Recall
        search_query = state.search_keywords or state.inquiry_text or user_text
        print(f"\n3. RECALL EXECUTION (Query: \"{search_query}\"):")
        try:
            results = retrieval_service.search(
                query=search_query,
                tenant_id="default",
                active_scopes=["research", "default"]
            )
            recalled_items = []
            for r in results:
                recalled_items.append({
                    "id": r.fact_id,
                    "summary": r.summary,
                    "hops": r.hops,
                    "final_score": r.final_score,
                    "relevance_score": r.relevance_score,
                })
        except Exception as exc:
            recalled_items = []
            if debug:
                logger.debug("Retrieval service search error: %s", exc)

        print(f"   Recalled Count (this search): {len(recalled_items)}")
        for r in recalled_items:
            print(f"   • [{r['id']}] {r['summary']} (relevance={r['relevance_score']:.2f}, hops={r['hops']})")

        # In current graph.py line 743:
        # combined_facts = list(state.recalled_facts or []) + parsed_facts
        # Show what happens to accumulated count across turns
        new_facts_formatted = [
            {"id": r["id"], "summary": r["summary"], "subject": r["summary"].split()[0], "predicate": "FACT", "object": "Value"}
            for r in recalled_items
        ]
        # Simulate current graph.py state accumulation:
        accumulated_recalled_facts = accumulated_recalled_facts + new_facts_formatted
        unique_accumulated = {}
        for f in accumulated_recalled_facts:
            unique_accumulated[f["id"]] = f
        print(f"   Accumulated In-State Recalled Count (cumulative bug): {len(accumulated_recalled_facts)} (unique: {len(unique_accumulated)})")

        # 3. Agent Prompt Injection
        # Current graph.py format:
        real_facts = [
            f for f in accumulated_recalled_facts
            if f.get("predicate") not in ("QUERY", "PROCESSED_QUERY")
        ]
        injected_block = (
            f"Authorized partitions: [research, default]\n"
            f"Retrieved graph facts: {json.dumps([f.get('summary') for f in real_facts[:6]])}\n"
        )
        if state.info_text:
            injected_block += f"User shared new info: \"{state.info_text}\"\n"
        print(f"\n4. INJECTED PROMPT CONTEXT:\n{injected_block.strip()}")

        # 4. Fact Extraction & Proposal
        # Test what extraction node extracts from messages
        extracted = extract_facts_from_messages(
            messages=state.messages,
            llm=llm,
            agent_id="test-agent",
            user_scope="research",
            target_text=state.info_text,
        )
        print("\n5. EXTRACTED / PROPOSED FACTS:")
        if not extracted:
            print("   None (or question detected)")
        else:
            for ef in extracted:
                print(f"   • Subject: '{ef.get('subject')}' | Predicate: '{ef.get('predicate')}' | Object: '{ef.get('obj')}' | Scope: '{ef.get('scope')}'")
                print(f"     Description: '{ef.get('description')}'")

        # 5. Write Execution Simulation
        print(f"\n6. WRITE OUTCOME (Decision: {user_dec}):")
        if not state.should_store_memory:
            print("   SKIPPED: Classifier flagged should_store_memory=False (no write attempted).")
        elif user_dec == "reject":
            print("   REJECTED by human reviewer. Memory graph remains unchanged.")
        elif user_dec == "approve":
            print(f"   COMMITTED to graph memory: {extracted}")
        else:
            print("   PENDING human approval.")

        # Update conversation history with user message and agent response
        conversation_history.append(HumanMessage(content=user_text))
        conversation_history.append(AIMessage(content=mock_reply))

    print("\n" + "=" * 80)
    print("Replay completed successfully.")
    print("=" * 80)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="KryptoDB Transcript Replay Harness")
    parser.add_argument("--live", action="store_true", help="Run against live LLM & Neo4j")
    parser.add_argument("--debug", action="store_true", help="Enable verbose debug logging")
    args = parser.parse_args()
    run_replay(live=args.live, debug=args.debug)
