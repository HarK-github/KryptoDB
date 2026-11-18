"""Project Titan 4-Turn Stress Benchmark Evaluation Script."""

import asyncio
import json
import uuid
from dotenv import load_dotenv

load_dotenv()
from langchain_core.messages import HumanMessage, AIMessage

from kryptodb.interfaces.http.routes.chat import get_active_llm, get_agent_checkpointer
from kryptodb.agent_runtime.langgraph.graph import get_compiled_graph

TURNS = [
    {
        "turn": 1,
        "name": "Seeding Dense Entities & Constraints",
        "prompt": (
            "Let's set up the parameters for Project Titan. The lead architect is Elena Rostova, "
            "and we are deploying on AWS us-east-1 using PostgreSQL 16. Our strict latency SLA is "
            "under 45ms, and the production cutover date is November 14. Confirm you've logged these details."
        ),
    },
    {
        "turn": 2,
        "name": "State Mutation & Conflict Resolution (Override Test)",
        "prompt": (
            "Quick update on Project Titan: we had to migrate the database to CockroachDB due to "
            "multi-region requirements, so we are now deploying across both us-east-1 and eu-central-1. "
            "Also, push the production cutover back by 10 days. Keep everything else the same."
        ),
    },
    {
        "turn": 3,
        "name": "Distractor / Context Pollution (Needle in a Haystack Test)",
        "prompt": (
            "Unrelated question while I have you: compare the trade-offs between Raft and Paxos consensus "
            "algorithms in distributed systems. Keep it to three bullet points."
        ),
    },
    {
        "turn": 4,
        "name": "Synthesis & Negative Recall (Evaluation Prompt)",
        "prompt": (
            "Generate a pre-deployment sign-off summary for Project Titan. Include the lead architect, "
            "final cloud regions, database engine, latency SLA, and the exact new cutover date. "
            "Constraint: Do not mention any deprecated tech stack choices or old dates from earlier in our conversation."
        ),
    },
]


def run_benchmark():
    thread_id = f"eval_titan_{uuid.uuid4().hex[:6]}"
    config = {"configurable": {"thread_id": thread_id}}
    agent_id = "Agent-1"
    user_scope = "research"

    from kryptodb.core.memory.rejection_store import clear_rejections
    clear_rejections()

    # Clean any prior Titan facts from previous test runs so Turn 1 is a true cold start
    try:
        from kryptodb.infrastructure.neo4j.client import neo4j_client
        neo4j_client.connect()
        neo4j_client.execute_query_with_retry(
            "MATCH (s:Entity)-[r:FACT]->(o:Entity) WHERE s.name = 'Project Titan' OR o.name = 'Project Titan' DELETE r"
        )
        neo4j_client.execute_query_with_retry(
            "MATCH (e:Entity {name: 'Project Titan'}) DETACH DELETE e"
        )
    except Exception as e:
        print(f"Note on DB cleanup: {e}")

    llm = get_active_llm()
    checkpointer = get_agent_checkpointer()
    graph = get_compiled_graph(llm=llm, checkpointer=checkpointer)

    print("=" * 80)
    print(f"STARTING PROJECT TITAN 4-TURN EVALUATION BENCHMARK")
    print(f"Thread ID: {thread_id} | Agent: {agent_id} | Scope: {user_scope}")
    print("=" * 80)

    for item in TURNS:
        turn_num = item["turn"]
        name = item["name"]
        prompt = item["prompt"]

        print(f"\n{'─' * 80}")
        print(f"TURN {turn_num}: {name.upper()}")
        print(f"{'─' * 80}")
        print(f"USER PROMPT:\n\"{prompt}\"\n")

        input_state = {
            "messages": [HumanMessage(content=prompt)],
            "agent_id": agent_id,
            "user_scope": user_scope,
        }

        res = graph.invoke(input_state, config=config)

        # Extract agent response
        last_msg = res.get("final_response") or res.get("reasoning_output") or ""
        if not last_msg and res.get("messages"):
            for m in reversed(res["messages"]):
                content = m.content if hasattr(m, "content") else (m.get("content") if isinstance(m, dict) else "")
                if content and (isinstance(m, AIMessage) or (isinstance(m, dict) and m.get("role") == "assistant")):
                    last_msg = content
                    break

        print(f"AGENT RESPONSE:\n{last_msg.strip()}\n")
        print(f"STATUS:               {res.get('status')}")
        print(f"INTENT:               {res.get('intent')}")
        print(f"SHOULD STORE:         {res.get('should_store_memory')}")
        print(f"SHOULD SEARCH:        {res.get('should_search_memory')}")
        print(f"RECALLED FACTS COUNT: {len(res.get('recalled_facts', []))}")
        if res.get("recalled_facts"):
            print("RECALLED FACTS:")
            for rf in res["recalled_facts"]:
                print(f"  • {rf.get('subject')} -[{rf.get('predicate')}]-> {rf.get('object', rf.get('obj'))} (status: {rf.get('status', 'active')})")
        
        print(f"PENDING / EXTRACTED:  {len(res.get('pending_facts', []))}")
        if res.get("pending_facts"):
            for pf in res["pending_facts"]:
                print(f"  • {pf.get('subject')} -[{pf.get('predicate')}]-> {pf.get('obj')} (supersedes: {pf.get('supersedes_existing', False)})")


if __name__ == "__main__":
    run_benchmark()
