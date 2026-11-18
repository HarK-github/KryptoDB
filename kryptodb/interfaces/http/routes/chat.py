"""HTTP and WebSocket endpoints for LangGraph Agent Activity and Chat streaming."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.request
import uuid
from typing import Any, Dict, List, Optional
from dotenv import load_dotenv

load_dotenv()

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from kryptodb.core.identity.authentication import require_auth

logger = logging.getLogger("kryptodb.interfaces.http.routes.chat")

router = APIRouter(tags=["Agent Chat"])

try:
    from langgraph.types import Command
    from langgraph.errors import GraphInterrupt
except ImportError:
    Command = None

    class GraphInterrupt(Exception):  # type: ignore
        pass


# ── Active LLM & Model Registry ─────────────────────────────────────────────

ACTIVE_LLM_MODEL = os.environ.get("LLM_MODEL", "gemini-2.5-flash-lite")
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")


def get_active_llm():
    global ACTIVE_LLM_MODEL
    if ACTIVE_LLM_MODEL == "deterministic-engine":
        return None

    if ACTIVE_LLM_MODEL.startswith("gemini"):
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
            api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
            return ChatGoogleGenerativeAI(model=ACTIVE_LLM_MODEL, google_api_key=api_key)
        except Exception as exc:
            logger.warning("Failed to instantiate Gemini model '%s': %s", ACTIVE_LLM_MODEL, exc)
            return None

    if ACTIVE_LLM_MODEL.startswith("gpt-"):
        try:
            from langchain_openai import ChatOpenAI
            return ChatOpenAI(model=ACTIVE_LLM_MODEL)
        except Exception as exc:
            logger.warning("Failed to instantiate OpenAI model '%s': %s", ACTIVE_LLM_MODEL, exc)
            return None

    installed_models = []
    try:
        req = urllib.request.Request(f"{OLLAMA_BASE_URL}/api/tags", headers={"User-Agent": "KryptoDB"})
        with urllib.request.urlopen(req, timeout=2) as response:
            if response.status == 200:
                data = json.loads(response.read().decode())
                installed_models = [m["name"] for m in data.get("models", []) if "embed" not in m["name"]]
    except Exception as err:
        logger.debug("Could not query Ollama tags: %s", err)

    target_model = ACTIVE_LLM_MODEL
    if installed_models and target_model not in installed_models:
        matched = [m for m in installed_models if target_model in m or m.startswith(target_model)]
        if matched:
            target_model = matched[0]
        else:
            tool_capable = sorted([m for m in installed_models if "qwen" in m or "llama" in m or "mistral" in m], reverse=True)
            target_model = tool_capable[0] if tool_capable else installed_models[0]
        logger.info("Requested model '%s' not pulled in Ollama. Auto-selected '%s'.", ACTIVE_LLM_MODEL, target_model)
        ACTIVE_LLM_MODEL = target_model

    try:
        try:
            from langchain_ollama import ChatOllama
        except ImportError:
            from langchain_community.chat_models import ChatOllama
        return ChatOllama(model=target_model, base_url=OLLAMA_BASE_URL)
    except Exception as exc:
        logger.warning("Failed to instantiate ChatOllama model '%s': %s", target_model, exc)
        return None


# ── Durable Checkpointer ───────────────────────────────────────────────────

_agent_checkpointer = None


def get_agent_checkpointer():
    global _agent_checkpointer
    if _agent_checkpointer is None:
        try:
            from langgraph.checkpoint.memory import MemorySaver
            _agent_checkpointer = MemorySaver()
        except Exception as exc:
            logger.error("Could not initialize MemorySaver: %s", exc)
            return None
    return _agent_checkpointer


# ── Schemas ────────────────────────────────────────────────────────────────

class AgentChatRequest(BaseModel):
    agent_id: str
    user_scope: str = "research"
    message: str
    thread_id: Optional[str] = None


class AgentApproveRequest(BaseModel):
    thread_id: str
    approved: bool


class ModelConfigChange(BaseModel):
    model: str


# ── HTTP Endpoints ─────────────────────────────────────────────────────────

@router.get("/models")
def list_available_models():
    """Returns active model, Ollama URL, and detected/available LLM models."""
    global ACTIVE_LLM_MODEL
    ollama_models = []
    try:
        req = urllib.request.Request(f"{OLLAMA_BASE_URL}/api/tags", headers={"User-Agent": "KryptoDB"})
        with urllib.request.urlopen(req, timeout=2) as response:
            if response.status == 200:
                data = json.loads(response.read().decode())
                ollama_models = [m["name"] for m in data.get("models", [])]
    except Exception:
        pass

    defaults = [
        "gemini-2.5-flash",
        "gemini-1.5-flash",
        "gemini-2.0-flash",
        "gemini-1.5-pro",
        "llama3.2",
        "llama3.1",
        "mistral",
        "qwen2.5",
        "gpt-4o-mini",
        "deterministic-engine",
    ]
    all_models = list(dict.fromkeys(ollama_models + defaults))

    return {
        "active_model": ACTIVE_LLM_MODEL,
        "ollama_url": OLLAMA_BASE_URL,
        "available_models": all_models,
    }


@router.post("/config/model")
def set_active_model(cfg: ModelConfigChange):
    """Dynamically switch the active LLM model."""
    global ACTIVE_LLM_MODEL
    ACTIVE_LLM_MODEL = cfg.model.strip()
    logger.info("Active LLM model set to: %s", ACTIVE_LLM_MODEL)
    return {"status": "success", "active_model": ACTIVE_LLM_MODEL}


@router.get("/chat/{thread_id}/history")
def get_chat_history(thread_id: str, auth: Dict[str, Any] = Depends(require_auth)):
    """Retrieve checkpointed conversation history for a given thread_id."""
    from kryptodb.agent_runtime.langgraph.graph import get_compiled_graph

    config = {"configurable": {"thread_id": thread_id}}
    graph = get_compiled_graph(checkpointer=get_agent_checkpointer())

    state_snapshot = graph.get_state(config)
    messages = []
    if state_snapshot.values and "messages" in state_snapshot.values:
        for m in state_snapshot.values["messages"]:
            role = "user" if type(m).__name__ == "HumanMessage" else "agent"
            content = m.content if hasattr(m, "content") else str(m)
            messages.append({"role": role, "content": content})

    return {
        "thread_id": thread_id,
        "messages": messages,
        "status": state_snapshot.values.get("status", "idle") if state_snapshot.values else "idle",
        "pending_facts": state_snapshot.values.get("pending_facts", []) if state_snapshot.values else [],
    }


@router.post("/agent/chat")
async def agent_chat(req: AgentChatRequest, auth: Dict[str, Any] = Depends(require_auth)):
    """Invoke or continue conversation with the KryptoDB Agent Graph."""
    from kryptodb.agent_runtime.langgraph.graph import get_compiled_graph
    from langchain_core.messages import HumanMessage, AIMessage

    thread_id = req.thread_id or f"session_{uuid.uuid4().hex[:6]}"
    config = {"configurable": {"thread_id": thread_id}}

    llm = get_active_llm()
    graph = get_compiled_graph(llm=llm, checkpointer=get_agent_checkpointer())

    input_state = {
        "messages": [HumanMessage(content=req.message)],
        "agent_id": req.agent_id,
        "user_scope": req.user_scope,
    }

    try:
        res = await asyncio.to_thread(graph.invoke, input_state, config=config)
        state_snapshot = await asyncio.to_thread(graph.get_state, config)

        pending_interrupts = []
        if state_snapshot.tasks:
            for task in state_snapshot.tasks:
                if hasattr(task, "interrupts") and task.interrupts:
                    for intr in task.interrupts:
                        pending_interrupts.append(intr.value if hasattr(intr, "value") else str(intr))

        last_msg = ""
        if res.get("messages"):
            for m in reversed(res["messages"]):
                if isinstance(m, AIMessage) or (isinstance(m, dict) and m.get("role") == "assistant"):
                    txt = m.content if hasattr(m, "content") else (m.get("content") if isinstance(m, dict) else str(m))
                    if txt and not txt.startswith("Fact committed:") and not txt.startswith("Conflict Resolution:"):
                        last_msg = txt
                        break

        final_resp = res.get("final_response") or res.get("reasoning_output") or last_msg

        return {
            "thread_id": thread_id,
            "status": res.get("status", "completed"),
            "agent_response": final_resp,
            "reasoning": res.get("reasoning_output"),
            "recalled_facts": res.get("recalled_facts", []),
            "denied_recalls": res.get("denied_recalls", []),
            "pending_facts": res.get("pending_facts", []),
            "system_events": res.get("system_events", []),
            "pending_interrupts": pending_interrupts,
        }
    except GraphInterrupt:
        state_snapshot = await asyncio.to_thread(graph.get_state, config)
        pending_interrupts = []
        if state_snapshot.tasks:
            for task in state_snapshot.tasks:
                if hasattr(task, "interrupts") and task.interrupts:
                    for intr in task.interrupts:
                        pending_interrupts.append(intr.value if hasattr(intr, "value") else str(intr))

        return {
            "thread_id": thread_id,
            "status": "pending_approval",
            "agent_response": "Pending Human Approval: This memory write involves a sensitive scope and requires human authorization.",
            "pending_interrupts": pending_interrupts,
            "pending_facts": state_snapshot.values.get("pending_facts", []) if state_snapshot.values else [],
        }
    except Exception as exc:
        logger.error("Error in agent_chat: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/agent/approve")
async def agent_approve(req: AgentApproveRequest, auth: Dict[str, Any] = Depends(require_auth)):
    """Resume agent execution following HITL write approval or rejection."""
    from kryptodb.agent_runtime.langgraph.graph import get_compiled_graph

    config = {"configurable": {"thread_id": req.thread_id}}
    graph = get_compiled_graph(checkpointer=get_agent_checkpointer())

    try:
        if Command is None:
            raise HTTPException(status_code=500, detail="LangGraph is not available")
        res = await asyncio.to_thread(graph.invoke, Command(resume={"approved": req.approved}), config=config)

        last_msg = ""
        if res.get("messages"):
            m = res["messages"][-1]
            last_msg = m.content if hasattr(m, "content") else str(m)

        return {
            "thread_id": req.thread_id,
            "status": res.get("status", "completed"),
            "agent_response": last_msg or res.get("reasoning_output"),
            "approved": req.approved,
            "pending_facts": res.get("pending_facts", []),
        }
    except GraphInterrupt:
        state_snapshot = await asyncio.to_thread(graph.get_state, config)
        pending_interrupts = []
        if state_snapshot.tasks:
            for task in state_snapshot.tasks:
                if hasattr(task, "interrupts") and task.interrupts:
                    for intr in task.interrupts:
                        pending_interrupts.append(intr.value if hasattr(intr, "value") else str(intr))

        return {
            "thread_id": req.thread_id,
            "status": "pending_approval",
            "agent_response": "Pending Human Approval: This memory write involves a sensitive scope and requires human authorization.",
            "pending_interrupts": pending_interrupts,
            "pending_facts": state_snapshot.values.get("pending_facts", []) if state_snapshot.values else [],
        }
    except Exception as exc:
        logger.error("Error in agent_approve: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ── WebSocket Streaming ────────────────────────────────────────────────────

@router.websocket("/chat/{thread_id}/stream")
async def chat_stream_websocket(websocket: WebSocket, thread_id: str):
    """Live event stream WebSocket emitting agent_token, recall_event, denial_event, pending_approval."""
    await websocket.accept()
    logger.info("WebSocket client connected for thread: %s", thread_id)

    from kryptodb.agent_runtime.langgraph.graph import get_compiled_graph

    config = {"configurable": {"thread_id": thread_id}}
    llm = get_active_llm()
    graph = get_compiled_graph(llm=llm, checkpointer=get_agent_checkpointer())

    try:
        while True:
            data = await websocket.receive_json()
            user_msg = data.get("message", "")
            agent_id = data.get("agent_id", "Agent-1")
            user_scope = data.get("user_scope", "research")

            from langchain_core.messages import HumanMessage
            input_state = {
                "messages": [HumanMessage(content=user_msg)],
                "agent_id": agent_id,
                "user_scope": user_scope,
            }

            try:
                async for mode, payload in graph.astream(input_state, config, stream_mode=["messages", "updates"]):
                    if mode == "messages":
                        token, metadata = payload
                        msg_class = type(token).__name__
                        if msg_class not in ("AIMessageChunk", "AIMessage"):
                            continue
                        message_node = metadata.get("langgraph_node") if isinstance(metadata, dict) else None
                        if message_node not in ("reasoning_node", "denied_handling"):
                            continue
                        await websocket.send_json({
                            "type": "agent_token",
                            "content": token.content if hasattr(token, "content") else str(token),
                            "is_chunk": msg_class == "AIMessageChunk",
                        })
                    elif mode == "updates":
                        for node_name, state_update in payload.items():
                            if node_name == "intent_classifier" and isinstance(state_update, dict):
                                intent_val = state_update.get("intent", "inquiry")
                                should_search = state_update.get("should_search_memory", True)
                                should_store = state_update.get("should_store_memory", False)
                                inq_t = state_update.get("inquiry_text")
                                inf_t = state_update.get("info_text")
                                extra = ""
                                if inq_t and inf_t:
                                    extra = f" [Inquiry: \"{inq_t}\" | Info: \"{inf_t}\"]"
                                elif inq_t:
                                    extra = f" [Inquiry: \"{inq_t}\"]"
                                elif inf_t:
                                    extra = f" [Info: \"{inf_t}\"]"
                                await websocket.send_json({
                                    "type": "agent_status",
                                    "status": f"Intent detected: {intent_val} (search={should_search}, store={should_store}){extra}"
                                })
                            elif node_name != "tools":
                                await websocket.send_json({"type": "agent_status", "status": f"Executing step: {node_name}..."})
                            if isinstance(state_update, dict):
                                _msgs = state_update.get("messages") or []
                                if not isinstance(_msgs, (list, tuple)):
                                    _msgs = [_msgs]
                                for _m in _msgs:
                                    if hasattr(_m, "tool_calls") and _m.tool_calls:
                                        for tc in _m.tool_calls:
                                            args_str = json.dumps(tc.get("args", {}))
                                            await websocket.send_json({
                                                "type": "agent_status",
                                                "status": f"Calling tool `{tc.get('name')}` with args: {args_str}",
                                            })
                                if node_name in ("reasoning_node", "denied_handling", "direct_write_node"):
                                    update_messages = state_update.get("messages") or []
                                    if not isinstance(update_messages, (list, tuple)):
                                        update_messages = [update_messages]
                                    for update_message in reversed(update_messages):
                                        content = getattr(update_message, "content", None)
                                        if content is None and isinstance(update_message, dict):
                                            content = update_message.get("content")
                                        if isinstance(content, list):
                                            content = "".join(
                                                block.get("text", "") if isinstance(block, dict) else str(block)
                                                for block in content
                                            )
                                        if content:
                                            await websocket.send_json({
                                                "type": "agent_token",
                                                "content": str(content),
                                                "is_chunk": False,
                                            })
                                            break
                                if state_update.get("denied_recalls"):
                                    await websocket.send_json({
                                        "type": "denial_event",
                                        "scope": state_update["denied_recalls"][0].get("scope", user_scope),
                                        "reason": state_update["denied_recalls"][0].get("status", "no_key"),
                                    })
                                elif state_update.get("recalled_facts"):
                                    await websocket.send_json({
                                        "type": "recall_event",
                                        "count": len(state_update["recalled_facts"]),
                                        "scope": state_update["recalled_facts"][0].get("scope", user_scope),
                                    })
                await websocket.send_json({"type": "done"})
            except GraphInterrupt as gi:
                payload = gi.args[0] if gi.args and isinstance(gi.args[0], dict) else {}
                await websocket.send_json({
                    "type": "pending_approval",
                    "status": "pending_approval",
                    "reason": payload.get("reason", "high_sensitivity_write_approval_required"),
                    "fact": payload.get("candidate_fact"),
                })
            except Exception as exc:
                logger.exception("Chat stream failed for thread %s", thread_id)
                await websocket.send_json({
                    "type": "error",
                    "message": "The agent could not complete this request.",
                    "detail": str(exc),
                })
    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected for thread: %s", thread_id)
    except Exception as exc:
        logger.error("WebSocket error in chat_stream: %s", exc)
