# Memory Layer Failure Diagnosis & Root Cause Analysis

**Date:** 2026-11-18 (Remediated: 2026-11-19)  
**Author:** Senior Memory Systems Engineer  
**Component:** KryptoDB GraphRAG Memory Substrate (`agent_runtime/langgraph`, `core/retrieval`, `core/memory`)  
**Status:** **REMEDIATED & VERIFIED** (197/197 Pytest Passing, 5/5 Strict Evaluation Passing)

---

## Executive Summary

An investigation was conducted to diagnose the critical failures observed during conversational GraphRAG sessions:
1. **Misclassification of mixed messages** (cramming questions and statements into a single entity).
2. **Junk facts proposed** (`Harshi, person knows, Object` with placeholders and no quote verification).
3. **Name confusion** (`Harshi` resolving to `Harshit`).
4. **Weak recall and hallucination** (returning facts when no relevant entity exists).
5. **Recall count explosion** ($6 \to 8 \to 12$) caused by cumulative state retention and feedback loop of synthetic query edges.
6. **Misleading UI messages** (declined writes labeled as `"recall denied"`).

All six root causes have been identified with exact code references and reproduced via [scripts/replay_transcript.py](file:///mnt/DE94962594960067/Users/kandp/Documents/Work/Git_Files/AIAgent/scripts/replay_transcript.py).

---

## Diagnostic Findings & Evidence

### 1. Where is "what to store" decided today: regex, keywords, a small model, or an LLM? Show the code.

**Finding:** It is currently a brittle hybrid spread across multiple layers:
1. First, an LLM call or naive regex fallback in `create_intent_classifier_node`:
   [graph.py:576-589](file:///mnt/DE94962594960067/Users/kandp/Documents/Work/Git_Files/AIAgent/kryptodb/agent_runtime/langgraph/graph.py#L576-L589):
   ```python
   # Fallback when llm is None (or fails)
   has_question = "?" in latest_clean or any(w in lowered for w in ("what ", "where ", "who ", "how ", "can you "))
   has_statement = any(w in lowered for w in (" is ", " works ", " likes ", " loves ", " prefer", " live", " have ", " moved "))

   if has_question and has_statement:
       intent_val = MessageIntent.MIXED.value
       parts = [p.strip() for p in re.split(r"[.!?]\s*", latest_clean) if p.strip()]
       q_parts = [p for p in parts if any(w in p.lower() for w in ("what", "where", "who", "how", "can")) or p.endswith("?")]
       s_parts = [p for p in parts if p not in q_parts]
       inquiry_t = " ".join(q_parts) if q_parts else latest_clean
       info_t = " ".join(s_parts) if s_parts else latest_clean
   ```
2. Second, in `extract_facts_from_messages` [graph.py:232-260](file:///mnt/DE94962594960067/Users/kandp/Documents/Work/Git_Files/AIAgent/kryptodb/agent_runtime/langgraph/graph.py#L232-L260):
   It uses greedy regexes like:
   ```python
   rel_patterns = [
       (r"^([A-Za-z0-9_\s]+?)\s+(likes|loves|prefers|dislikes|hates)\s+(.+)$", ...),
       (r"^([A-Za-z0-9_\s]+?)\s+(lives in|resides in|based in)\s+(.+)$", lambda m: (m.group(1).strip().title(), "LIVES_IN", m.group(3).strip())),
       ...
   ]
   ```
   For `"harshit kandpal lives in india and he is very interesting person, lfx entee too"`, the regex matched `group(1)="Harshit Kandpal"`, `predicate="LIVES_IN"`, and captured everything else into `group(3)="india and he is very interesting person, lfx entee too"`.

3. Finally, when neither the prompt nor the regex produces triples, it falls back to:
   [graph.py:293-301](file:///mnt/DE94962594960067/Users/kandp/Documents/Work/Git_Files/AIAgent/kryptodb/agent_runtime/langgraph/graph.py#L293-L301):
   ```python
   fact = FactItem(
       subject=agent_id,
       predicate="PROCESSED_QUERY",
       obj=f"scope:{resolved_scope}",
       scope=resolved_scope,
       description=f"Extracted fact for query '{clean_query}'"
   )
   return [fact.model_dump()]
   ```

---

### 2. Is the user's question text ever written as a fact? Which function writes it?

**Finding:** **YES.**
1. In `extract_facts_from_messages()` ([graph.py:293-301](file:///mnt/DE94962594960067/Users/kandp/Documents/Work/Git_Files/AIAgent/kryptodb/agent_runtime/langgraph/graph.py#L293-L301)):
   When a user query is not filtered out or is treated as a statement, `clean_query` is placed directly into:
   `description=f"Extracted fact for query '{clean_query}'"`
   with `predicate="PROCESSED_QUERY"`.
2. In `create_extraction_node()` ([graph.py:884-889](file:///mnt/DE94962594960067/Users/kandp/Documents/Work/Git_Files/AIAgent/kryptodb/agent_runtime/langgraph/graph.py#L884-L889)):
   If `extract_facts_from_messages` returns empty, the fallback creates:
   ```python
   candidate = extracted_facts[0] if extracted_facts else {
       "subject": state.agent_id,
       "predicate": "PROCESSED_QUERY",
       "obj": f"scope:{state.user_scope}",
       "scope": state.user_scope
   }
   ```
3. `create_direct_write_node()` ([graph.py:974-988](file:///mnt/DE94962594960067/Users/kandp/Documents/Work/Git_Files/AIAgent/kryptodb/agent_runtime/langgraph/graph.py#L974-L988)) issues a tool call:
   `propose_fact_tool(subject, predicate, obj, scope, description)`
   which calls `POST /memory`, executing `write_fact()` to create a `(:Entity)-[:FACT]->(:Entity)` relationship in Neo4j containing the entire raw query text in `r.description`.

---

### 3. Are assistant messages ever used as a source for proposed facts or ever written to the graph?

**Finding:** **YES.**
In `extract_facts_from_messages()` ([graph.py:189-220](file:///mnt/DE94962594960067/Users/kandp/Documents/Work/Git_Files/AIAgent/kryptodb/agent_runtime/langgraph/graph.py#L189-L220)):
```python
if target_text and target_text.strip():
    clean_query = target_text.strip()
    messages_to_use = [HumanMessage(content=clean_query)]
else:
    ...
    messages_to_use = messages  # <-- CONTAINS ALL MESSAGES INCLUDING AIMessage!
```
When `target_text` is `None` (the default whenever the message was not successfully decomposed by the classifier), `messages_to_use` contains the entire chat history, including the assistant's previous turns (`AIMessage`).
In Turn 1, the assistant hallucinated:
> *"Harshi is a person I know and she has a background in technology and often speaks up at tech conferences..."*

In Turn 2, `messages_to_use` contained that `AIMessage`. The LLM extractor read the assistant's hallucination as source material and proposed:
`Subject: Harshi | Predicate: person knows | Object: value / Object`.

---

### 4. Why does the recalled count grow $6 \to 8 \to 12$? List the IDs recalled at each turn.

**Finding:** Two root causes compound this:
1. **Cumulative State Accumulation Bug in `graph.py`:**
   In [graph.py:743-748](file:///mnt/DE94962594960067/Users/kandp/Documents/Work/Git_Files/AIAgent/kryptodb/agent_runtime/langgraph/graph.py#L743-L748):
   ```python
   combined_facts = list(state.recalled_facts or []) + parsed_facts
   return {
       "recalled_facts": _dedupe_facts(combined_facts),
       "denied_recalls": denials,
       "status": "recalled"
   }
   ```
   `state.recalled_facts` is **never cleared between turns**. When turn 2 runs, it takes all facts recalled in turn 1 and adds the new ones returned by the retrieval tool.
2. **Synthetic Feedback Loop (`PROCESSED_QUERY`):**
   In previous sessions, every turn wrote a `PROCESSED_QUERY` edge (`test-agent PROCESSED_QUERY scope:research` with description containing the query).
   In live Neo4j right now, we verified the following active edges:
   - `fact-0356a7e56419`: `Harshi PERSON_KNOWS value`
   - `fact-68dee055fdb6`: `cookies LIKES Harshit`
   - `fact-e6467d279d75`: `Harshit LIKES cookies`
   - `fact-d7d8e97a16ce`: `Harshit HAS_NAME Kandpal`
   - `fact-87b00ff622f0`: `test-agent PROCESSED_QUERY scope:research` (description: *"Extracted fact for query 'I am looking for a vegetarian meal plan for two people'"*)
   - `fact-d7205729a042`: `vegetarian meal FOR_TWO_PEOPLE value`

Because 2-hop traversal ([traversal.py:104-121](file:///mnt/DE94962594960067/Users/kandp/Documents/Work/Git_Files/AIAgent/kryptodb/core/retrieval/traversal.py#L104-L121)) links through shared hub entities (like `value` or `Harshit`), each query unearths both the direct facts and the synthetic query nodes, expanding the result set turn-by-turn.

---

### 5. How does "Harshi" get matched to "Harshit" (fulltext fuzziness, vector similarity, or an entity-resolution rule)?

**Finding:** Through two distinct search mechanics:
1. **Fulltext Lucene Prefix Wildcard:**
   In [hybrid_search.py:142](file:///mnt/DE94962594960067/Users/kandp/Documents/Work/Git_Files/AIAgent/kryptodb/core/retrieval/hybrid_search.py#L142):
   ```python
   clean_terms = [t for t in safe_lucene.split() if t.lower() not in STOP_WORDS and len(t) > 1]
   lucene_query = " OR ".join([f"{term}*" for term in clean_terms]) if clean_terms else "*"
   ```
   For `"explain about harshi"`, `clean_terms` is `["harshi"]`.
   `lucene_query` becomes `"harshi*"`.
   In Neo4j's Lucene fulltext index (`entity_name_ft`), the wildcard `"harshi*"` matches `"harshit"` and `"harshit kandpal"`.
2. **Lexical Reranker Substring Match:**
   In [ranking.py:75-81](file:///mnt/DE94962594960067/Users/kandp/Documents/Work/Git_Files/AIAgent/kryptodb/core/retrieval/ranking.py#L75-L81):
   ```python
   for q in q_tokens:
       if len(q) >= 4:
           for t in t_tokens:
               if len(t) >= 4 and (q.startswith(t) or t.startswith(q)) and q != t:
                   prefix_match += 0.5
   ```
   `"harshit".startswith("harshi")` is `True`, giving `"harshit"` an inflated prefix-match score of 0.31.
3. **No Minimum Relevance Cutoff:**
   In [service.py:122](file:///mnt/DE94962594960067/Users/kandp/Documents/Work/Git_Files/AIAgent/kryptodb/core/retrieval/service.py#L122):
   `return ranked_results[:top_k]` returns the top-k results unconditionally, even if the score is low (e.g. 0.05 - 0.31) and completely irrelevant.

---

### 6. What exactly is injected into the agent prompt, and how is it labeled?

**Finding:**
In [graph.py:808-825](file:///mnt/DE94962594960067/Users/kandp/Documents/Work/Git_Files/AIAgent/kryptodb/agent_runtime/langgraph/graph.py#L808-L825):
```python
system_prompt = (
    f"You are KryptoDB Agent ({state.agent_id}), an assistant whose factual source is KryptoDB's knowledge graph.\n"
    f"Your authorized knowledge partitions: [{scopes_str}].\n"
    f"Retrieved graph facts: {json.dumps(real_facts)}.\n\n"
)
if state.info_text:
    system_prompt += (
        f"The user also shared this new factual information/preference in this turn:\n"
        f"\"{state.info_text}\"\n"
        f"Acknowledge this new information and incorporate it while answering their question.\n\n"
    )
system_prompt += (
    "Grounding rules (mandatory):\n"
    "- Answer the user's question accurately using retrieved graph facts and newly stated user preferences.\n"
    "- Mention relationships and source scopes when relevant.\n"
    "- Do NOT use general world knowledge or hallucinate details not in the graph facts or user statements.\n"
    "- If no facts in memory answer the question, state that the requested information was not found in your accessible knowledge graphs."
)
```

**Issues with this formatting:**
1. Facts are dumped as raw JSON dictionaries without clear demarcation (`<retrieved_memories>`).
2. There are no memory citation tags (e.g. `[m1]`, `[m2]`) with timestamps or provenance.
3. If `real_facts` is empty, nothing tells the agent that memory was checked and found to be `none`; the agent is left to guess or hallucinate.
4. If `state.info_text` erroneously captures a question (or opinion), the prompt explicitly orders the agent:
   *"The user also shared this new factual information/preference in this turn: ... Acknowledge this new information..."*, forcing the agent to echo and confirm the statement as truth.

---

### 7. Misleading UI Messages

In [ui/src/app/ChatPopup.tsx](file:///mnt/DE94962594960067/Users/kandp/Documents/Work/Git_Files/AIAgent/ui/src/app/ChatPopup.tsx#L484-L502):
```tsx
const handleReject = async () => {
    setPendingState('rejected')
    setMessages(prev => [
        ...prev,
        { id: String(Date.now()), role: 'system', type: 'denied', scope: pendingFact?.scope || currentScope, reason: 'user_declined' },
        { id: String(Date.now() + 1), role: 'agent', text: 'Fact write rejected by human reviewer. Memory graph remains unchanged.' }
    ])
```
And in rendering [ChatPopup.tsx:770](file:///mnt/DE94962594960067/Users/kandp/Documents/Work/Git_Files/AIAgent/ui/src/app/ChatPopup.tsx#L770):
```tsx
↳ {msg.type === 'recalled' ? `recalled ${msg.count || 1} fact${(msg.count || 1) > 1 ? 's' : ''}` : 'recall denied'}
```
Because the UI assumes any system message that is not `type === 'recalled'` must be a `"recall denied"`, when a user rejects a write, it renders:
`↳ recall denied, reason: user_declined`.
Furthermore, upon approval ([ChatPopup.tsx:469](file:///mnt/DE94962594960067/Users/kandp/Documents/Work/Git_Files/AIAgent/ui/src/app/ChatPopup.tsx#L469)), it outputs the static text `"Fact successfully authorized and committed to graph memory."` without specifying what fact was actually committed.

---

## Action Plan for Steps 1 through 5

1. **Step 1: Turn Understanding (`core/memory/turn_analysis.py`)**:
   Implement `TurnAnalyzer` port and Pydantic schema with temperature 0 LLM call and deterministic test double.
2. **Step 2: Fact Extraction and Write Validation (`core/memory/write_policy.py`, `core/memory/entity_resolution.py`)**:
   Validate source is user event, quote is exact substring, reject placeholders (`Object`, `value`, etc.), enforce predicate registry, and require exact normalized matching for entity resolution.
3. **Step 3: Recall with Relevance Control (`core/retrieval/`)**:
   Enforce `retrieval_needed`, apply cross-encoder cutoff threshold, exclude synthetic query nodes, clear cumulative `recalled_facts` across turns.
4. **Step 4: Memory Context Sent to Agent**:
   Inject structured `<retrieved_memories>` block with citation tags `[m1]` or `<retrieved_memories>none</retrieved_memories>`.
5. **Step 5: UI & Write Honesty**:
   Async write path, explicit fact confirmation cards, accurate labels (`"Write rejected by reviewer"`, `"Fact committed: <statement>"`), and proposal deduplication.

---

## 3. Remediation Status

All 5 steps above were fully implemented, followed by the Phase 9 protocol decoupling and Cypher centralization:
- **`tests/memory_repair/test_memory_repair.py`**: All 12 comprehensive regression tests pass.
- **`uv run pytest`**: 197/197 tests passing (100% green).
- **`scripts/eval_kryptodb_strict.py`**: 5/5 passed on Meta-Llama 3.3 70B Instruct.

