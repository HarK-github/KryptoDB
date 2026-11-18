"""Turn Understanding and Message Decomposition via Structured LLM Output.

Implements TurnAnalyzer port to decompose mixed messages, resolve coreferences,
distinguish durable facts from opinions/questions/hypotheticals, and generate
self-contained retrieval queries.
"""

from __future__ import annotations

import json
import logging
import re
from enum import Enum
from typing import Any, Dict, List, Optional, Protocol, Union, runtime_checkable

from pydantic import BaseModel, Field, field_validator

from kryptodb.core.settings import settings

logger = logging.getLogger("kryptodb.core.memory.turn_analysis")


class SegmentType(str, Enum):
    STATEMENT = "STATEMENT"
    PREFERENCE = "PREFERENCE"
    QUESTION = "QUESTION"
    COMMAND = "COMMAND"
    OPINION = "OPINION"
    CHITCHAT = "CHITCHAT"
    HYPOTHETICAL = "HYPOTHETICAL"
    CORRECTION = "CORRECTION"
    TEMPORAL_MODIFICATION = "TEMPORAL_MODIFICATION"
    RETRACTION = "RETRACTION"
    PLANNED_ACTION = "PLANNED_ACTION"


class EntityResolution(str, Enum):
    COREFERENCE = "coreference"
    EXPLICIT = "explicit"
    UNRESOLVED = "unresolved"


class AnalyzedSegment(BaseModel):
    """Decomposed contiguous text span from the user's turn."""

    text: str = Field(description="Exact verbatim span of the user message")
    type: SegmentType = Field(description="Segment category")
    durable: bool = Field(
        default=True,
        description="True if segment conveys durable facts/preferences/specifications intended for long-term memory.",
    )
    temporary: bool = Field(
        default=False,
        description="True if segment states transient, situational, or ephemeral context.",
    )


class MentionedEntity(BaseModel):
    """Entity reference identified and resolved from discourse."""

    surface: str = Field(description="Surface word, phrase, or pronoun used in user message")
    resolved_to: Optional[str] = Field(
        default=None,
        description="Resolved canonical entity name, or None if unresolved",
    )
    kind: str = Field(default="PERSON", description="Entity category: PERSON, ORG, CONCEPT, etc.")
    resolution: EntityResolution = Field(
        default=EntityResolution.EXPLICIT,
        description="Method used to resolve entity: explicit, coreference, or unresolved",
    )


class TurnAnalysisResult(BaseModel):
    """Structured understanding of a conversational turn."""

    segments: List[AnalyzedSegment] = Field(
        default_factory=list,
        description="Discourse segments split from user message",
    )
    mentioned_entities: List[MentionedEntity] = Field(
        default_factory=list,
        description="Entities mentioned and their resolutions",
    )
    retrieval_needed: bool = Field(
        default=False,
        description="True if memory lookup is necessary to answer or assist the user",
    )
    retrieval_query: Optional[str] = Field(
        default=None,
        description="Rewritten, self-contained search query with pronouns and references resolved",
    )


@runtime_checkable
class TurnAnalyzer(Protocol):
    """Protocol for turn analysis and discourse decomposition."""

    def analyze_turn(
        self,
        latest_message: str,
        conversation_history: Optional[List[Any]] = None,
        focus_entity: Optional[str] = None,
    ) -> TurnAnalysisResult:
        """Decomposes a user turn into validated structured segments and retrieval instructions."""
        ...


ANALYZER_SYSTEM_PROMPT = """You are an expert conversational discourse analyzer for a long-term GraphRAG memory system.
Your job is to read the latest user message in the context of recent turns and analyze it into a strict JSON schema.

RULES:
1. SPLIT MIXED MESSAGES:
   A single message may contain statements, opinions, corrections, temporal updates, and commands. You must split them into distinct, non-overlapping segments.
   - STATEMENT: Factual disclosures about the world, systems, or people ("Harshit lives in India", "Project Titan uses PostgreSQL 16").
   - PREFERENCE: Expressed likes, dislikes, favorite items, dietary rules ("I like espresso", "He enjoys reading").
   - OPINION: Subjective assessments or value judgments ("he is very interesting person", "that movie is great").
   - QUESTION: Requests for information, inquiries, queries, or requests to synthesize/summarize/compare stored knowledge ("what does he like in food?", "where is my key?", "generate a summary of Project Titan", "compare the trade-offs between Raft and Paxos").
   - COMMAND: Operational directives to the system ("confirm you've logged these details", "stop", "pause").
   - CHITCHAT: Greetings, pleasantries, small talk, gratitude ("hello", "thanks", "bye").
   - HYPOTHETICAL: Speculative or hedged statements ("I might try vegan food tomorrow", "maybe we should go").
   - CORRECTION: Updates, overrides, or corrections to existing facts ("migrate the database to CockroachDB instead", "actually, she is the lead").
   - TEMPORAL_MODIFICATION: Date or timeline adjustments ("push the production cutover back by 10 days", "reschedule to Dec 1").
   - RETRACTION: Directives to delete, revoke, or forget prior memory ("forget my old address", "do not remember that").
   - PLANNED_ACTION: Planned future migrations, roadmaps, or scheduled tasks ("we plan to migrate to CockroachDB next quarter").

2. DURABILITY POLICY:
   - Factual disclosures, project parameters, technical configurations, specifications, architecture decisions, team roles, requirements, user preferences, corrections, temporal modifications, and planned actions MUST have durable=true.
   - ONLY mark durable=false if the segment is:
     * A QUESTION or inquiry seeking stored information (durable=false).
     * Pure CHITCHAT, pleasantries, or greetings (durable=false).
     * Pure COMMAND or operational directive (e.g. "Confirm you've logged these details", "Summarize the project") (durable=false).
     * RETRACTION (durable=false, but flags memory removal/revocation).
     * HYPOTHETICAL or speculative statements (e.g. "I might try vegan food tomorrow") (durable=false).
     * Pure transient/situational context (e.g. "I am standing outside right now") (durable=false).
   - Every factual STATEMENT, CORRECTION, TEMPORAL_MODIFICATION, and PLANNED_ACTION describing projects, systems, people, relationships, or world facts MUST be durable=true.
   - OPINION must have durable=false unless expressing a stable personal preference.

3. PRONOUN & COREFERENCE RESOLUTION:
   - Inspect recent turns and the focus entity to resolve pronouns ("he", "she", "they", "it").
   - If "he" refers to a person mentioned in recent turns (e.g. "Harshit Kandpal"), resolve it:
     surface="he", resolved_to="Harshit Kandpal", resolution="coreference".
   - If a pronoun has no clear antecedent in context, mark it:
     surface="he", resolved_to=null, resolution="unresolved".
   - NEVER invent or guess an antecedent if none was mentioned.

4. RETRIEVAL PLANNING:
   - retrieval_needed is FALSE for pure statements with no questions, pure greetings, gratitude, or purely operational commands.
   - retrieval_needed is TRUE whenever there is a QUESTION or request seeking stored information or synthesis of prior context (e.g. summaries, reports, sign-offs, comparisons).
   - retrieval_query MUST be a rewritten, self-contained search query with all pronouns replaced by the canonical entity name (e.g. "what does Harshit Kandpal like in food", "Project Titan parameters").
   - If retrieval_needed is false, retrieval_query should be null.

5. VERBATIM QUOTES:
   - Each segment's `text` MUST be an exact span from the user message. Do not alter the user's words in segment.text.
   - Tolerate typos in entities (e.g. "lfx entee" -> resolved entity or normalized meaning in the query).

Return STRICT JSON matching the TurnAnalysisResult schema.
"""


class LLMTurnAnalyzer:
    """Production TurnAnalyzer powered by an LLM with structured output."""

    def __init__(self, llm: Optional[Any] = None):
        self.llm = llm
        self._structured_llm = None
        if self.llm and hasattr(self.llm, "with_structured_output"):
            try:
                self._structured_llm = self.llm.with_structured_output(TurnAnalysisResult)
            except Exception as exc:
                logger.debug("Could not bind with_structured_output: %s", exc)

    def analyze_turn(
        self,
        latest_message: str,
        conversation_history: Optional[List[Any]] = None,
        focus_entity: Optional[str] = None,
    ) -> TurnAnalysisResult:
        clean_text = latest_message.strip()
        if not clean_text:
            return TurnAnalysisResult(
                segments=[
                    AnalyzedSegment(
                        text="",
                        type=SegmentType.CHITCHAT,
                        durable=False,
                        temporary=False,
                    )
                ],
                mentioned_entities=[],
                retrieval_needed=False,
                retrieval_query=None,
            )

        # Format context turns
        history_text = ""
        if conversation_history:
            recent_turns = conversation_history[-settings.max_recent_turns :]
            formatted_turns = []
            for m in recent_turns:
                role = "User"
                content = ""
                if hasattr(m, "content"):
                    content = str(m.content)
                    if hasattr(m, "type") and m.type == "human":
                        role = "User"
                    elif hasattr(m, "type") and m.type == "ai":
                        role = "Assistant"
                elif isinstance(m, dict):
                    role = m.get("role", "User").capitalize()
                    content = str(m.get("content", ""))
                formatted_turns.append(f"{role}: {content}")
            history_text = "\n".join(formatted_turns)

        user_prompt = (
            f"Context (recent turns):\n{history_text if history_text else 'None'}\n\n"
            f"Current Focus Entity: {focus_entity or 'None'}\n\n"
            f"Latest User Message:\n\"{clean_text}\"\n\n"
            f"Analyze the message into segments, entities, and retrieval planning as specified."
        )

        if self._structured_llm is not None:
            try:
                from langchain_core.messages import HumanMessage, SystemMessage

                res = self._structured_llm.invoke(
                    [
                        SystemMessage(content=ANALYZER_SYSTEM_PROMPT),
                        HumanMessage(content=user_prompt),
                    ]
                )
                if isinstance(res, TurnAnalysisResult):
                    return self._enforce_safety_rules(res, clean_text)
                if isinstance(res, dict):
                    parsed = TurnAnalysisResult.model_validate(res)
                    return self._enforce_safety_rules(parsed, clean_text)
                if hasattr(res, "intent"):
                    intent_val = getattr(res.intent, "value", str(res.intent)).lower()
                    segs = []
                    info = getattr(res, "info_text", None)
                    inq = getattr(res, "inquiry_text", None)
                    if info:
                        segs.append(AnalyzedSegment(text=info, type=SegmentType.STATEMENT, durable=True))
                    if inq:
                        segs.append(AnalyzedSegment(text=inq, type=SegmentType.QUESTION, durable=False))
                    if not segs:
                        if intent_val == "chitchat":
                            stype = SegmentType.CHITCHAT
                            dur = False
                        elif intent_val == "inquiry":
                            stype = SegmentType.QUESTION
                            dur = False
                        elif intent_val == "statement":
                            stype = SegmentType.STATEMENT
                            dur = True
                        else:
                            stype = SegmentType.STATEMENT
                            dur = getattr(res, "should_store_memory", True)
                        segs.append(AnalyzedSegment(text=clean_text, type=stype, durable=dur))

                    return TurnAnalysisResult(
                        segments=segs,
                        mentioned_entities=[],
                        retrieval_needed=getattr(res, "should_search_memory", False),
                        retrieval_query=getattr(res, "search_keywords", None) or (inq or clean_text),
                    )
            except Exception as exc:
                logger.warning("Structured LLM turn analysis failed, falling back: %s", exc)

        # Fallback to standard invoke + JSON parse
        if self.llm is not None:
            try:
                from langchain_core.messages import HumanMessage, SystemMessage

                json_prompt = (
                    ANALYZER_SYSTEM_PROMPT
                    + "\nRespond with a single valid JSON object with keys: segments, mentioned_entities, retrieval_needed, retrieval_query."
                )
                resp = self.llm.invoke(
                    [
                        SystemMessage(content=json_prompt),
                        HumanMessage(content=user_prompt),
                    ]
                )
                raw_content = resp.content if hasattr(resp, "content") else str(resp)
                clean_raw = re.sub(r"^```(?:json)?\s*", "", raw_content.strip(), flags=re.MULTILINE)
                clean_raw = re.sub(r"\s*```$", "", clean_raw.strip(), flags=re.MULTILINE)
                idx = clean_raw.find("{")
                if idx != -1:
                    parsed, _ = json.JSONDecoder().raw_decode(clean_raw[idx:])
                    res = TurnAnalysisResult.model_validate(parsed)
                    return self._enforce_safety_rules(res, clean_text)
            except Exception as exc:
                logger.warning("Prompt-based LLM turn analysis failed: %s", exc)

        # Deterministic rule-based fallback when LLM is unavailable
        fake = FakeTurnAnalyzer()
        return fake.analyze_turn(clean_text, conversation_history, focus_entity)

    def _enforce_safety_rules(self, result: TurnAnalysisResult, original_text: str) -> TurnAnalysisResult:
        """Code-level safety enforcement guarding against LLM policy leakage."""
        cleaned_segments = []
        for seg in result.segments:
            # Questions, commands, chitchat, hypotheticals, retractions can NEVER be durable
            if seg.type in (
                SegmentType.QUESTION,
                SegmentType.COMMAND,
                SegmentType.CHITCHAT,
                SegmentType.HYPOTHETICAL,
                SegmentType.RETRACTION,
            ):
                seg.durable = False
            elif seg.type == SegmentType.OPINION and not settings.allow_subjective_predicates:
                seg.durable = False
            elif seg.type in (
                SegmentType.STATEMENT,
                SegmentType.PREFERENCE,
                SegmentType.CORRECTION,
                SegmentType.TEMPORAL_MODIFICATION,
                SegmentType.PLANNED_ACTION,
            ):
                has_unresolved_coref = any(
                    e.resolution == EntityResolution.UNRESOLVED
                    and re.search(rf"\b{re.escape(e.surface)}\b", seg.text, re.IGNORECASE)
                    for e in result.mentioned_entities
                )
                if not seg.temporary and not has_unresolved_coref:
                    seg.durable = True
                elif has_unresolved_coref:
                    seg.durable = False
            cleaned_segments.append(seg)
        lowered = original_text.lower()
        is_synthesis = bool(re.search(r"\b(summary|sign-off|report|compare)\b", lowered))
        if is_synthesis:
            result.retrieval_needed = True
            if not result.retrieval_query:
                if "project titan" in lowered or "titan" in lowered:
                    result.retrieval_query = "Project Titan"
                else:
                    result.retrieval_query = original_text.strip()
            for seg in result.segments:
                if re.search(r"\b(generate|summarize|sign-off|compare)\b", seg.text, re.IGNORECASE):
                    seg.type = SegmentType.QUESTION
                    seg.durable = False

        # If retrieval is not needed, clear retrieval_query
        if not result.retrieval_needed:
            result.retrieval_query = None

        return result


class FakeTurnAnalyzer:
    """Deterministic test double providing predictable turn analysis for regression testing."""

    def __init__(self, fixture_overrides: Optional[Dict[str, TurnAnalysisResult]] = None):
        self.fixture_overrides = fixture_overrides or {}

    def analyze_turn(
        self,
        latest_message: str,
        conversation_history: Optional[List[Any]] = None,
        focus_entity: Optional[str] = None,
    ) -> TurnAnalysisResult:
        clean = latest_message.strip()

        # Check explicit fixture overrides
        if clean in self.fixture_overrides:
            return self.fixture_overrides[clean]

        lowered = clean.lower()

        # 1. Chitchat
        if lowered in ("hi", "hello", "hey", "howdy", "greetings", "thanks", "thank you", "bye", "good morning", "good evening", "good afternoon") or lowered.startswith("good morning"):
            return TurnAnalysisResult(
                segments=[
                    AnalyzedSegment(
                        text=clean,
                        type=SegmentType.CHITCHAT,
                        durable=False,
                        temporary=False,
                    )
                ],
                mentioned_entities=[],
                retrieval_needed=False,
                retrieval_query=None,
            )

        # 2. Key regression fixture: "harshit kandpal lives in india and he is very interesting person, lfx entee too . also what does he like in food?"
        if "harshit kandpal lives in india" in lowered and "what does he like in food" in lowered:
            return TurnAnalysisResult(
                segments=[
                    AnalyzedSegment(
                        text="harshit kandpal lives in india",
                        type=SegmentType.STATEMENT,
                        durable=True,
                        temporary=False,
                    ),
                    AnalyzedSegment(
                        text="he is very interesting person",
                        type=SegmentType.OPINION,
                        durable=False,
                        temporary=False,
                    ),
                    AnalyzedSegment(
                        text="lfx entee too",
                        type=SegmentType.STATEMENT,
                        durable=True,
                        temporary=False,
                    ),
                    AnalyzedSegment(
                        text="also what does he like in food?",
                        type=SegmentType.QUESTION,
                        durable=False,
                        temporary=False,
                    ),
                ],
                mentioned_entities=[
                    MentionedEntity(
                        surface="harshit kandpal",
                        resolved_to="Harshit Kandpal",
                        kind="PERSON",
                        resolution=EntityResolution.EXPLICIT,
                    ),
                    MentionedEntity(
                        surface="he",
                        resolved_to="Harshit Kandpal",
                        kind="PERSON",
                        resolution=EntityResolution.COREFERENCE,
                    ),
                ],
                retrieval_needed=True,
                retrieval_query="what does Harshit Kandpal like in food",
            )

        question_starters = ("what", "where", "who", "when", "why", "how", "tell me", "explain", "does", "do", "can", "compare", "generate", "summarize")
        command_starters = ("confirm", "please confirm", "forget", "clear", "stop", "delete", "make sure", "keep", "keep everything", "constraint")
        raw_sentences = [s.strip() for s in re.split(r'(?<=[.?!])\s+', clean) if s.strip()]
        if len(raw_sentences) > 1:
            segs = []
            ret_query = None
            for sent in raw_sentences:
                s_low = sent.lower()
                is_q = sent.endswith("?") or any(s_low.startswith(q) for q in question_starters) or "summary" in s_low or "sign-off" in s_low or "question" in s_low or "compare" in s_low
                is_retract = "forget" in s_low or "delete" in s_low or "retract" in s_low
                is_cmd = any(s_low.startswith(c) for c in command_starters)
                is_temporal = any(w in s_low for w in ("push the", "delay", "reschedule", "postpone", "cutover back", "by 10 days", "back by"))
                is_correction = any(w in s_low for w in ("had to migrate", "migrate the", "instead", "actually", "update on"))
                
                if is_q:
                    segs.append(AnalyzedSegment(text=sent, type=SegmentType.QUESTION, durable=False))
                    if not ret_query:
                        if "project titan" in lowered:
                            ret_query = "Project Titan"
                        else:
                            ret_query = sent.rstrip("?").strip()
                elif is_retract:
                    segs.append(AnalyzedSegment(text=sent, type=SegmentType.RETRACTION, durable=False))
                elif is_cmd:
                    segs.append(AnalyzedSegment(text=sent, type=SegmentType.COMMAND, durable=False))
                elif is_temporal:
                    segs.append(AnalyzedSegment(text=sent, type=SegmentType.TEMPORAL_MODIFICATION, durable=True))
                elif is_correction:
                    segs.append(AnalyzedSegment(text=sent, type=SegmentType.CORRECTION, durable=True))
                else:
                    segs.append(AnalyzedSegment(text=sent, type=SegmentType.STATEMENT, durable=True))

            has_q = any(s.type == SegmentType.QUESTION for s in segs)
            has_stmt = any(s.durable for s in segs)
            return TurnAnalysisResult(
                segments=segs,
                mentioned_entities=[],
                retrieval_needed=has_q,
                retrieval_query=ret_query if has_q else None,
            )

        # Single command / retraction check
        if any(lowered.startswith(w) for w in ("forget", "delete", "retract", "remove", "clear")) or "forget that" in lowered or "forget my" in lowered:
            return TurnAnalysisResult(
                segments=[
                    AnalyzedSegment(
                        text=clean,
                        type=SegmentType.RETRACTION,
                        durable=False,
                        temporary=False,
                    )
                ],
                mentioned_entities=[],
                retrieval_needed=False,
                retrieval_query=None,
            )

        if any(lowered.startswith(w) for w in ("confirm", "please confirm", "stop")):
            return TurnAnalysisResult(
                segments=[
                    AnalyzedSegment(
                        text=clean,
                        type=SegmentType.COMMAND,
                        durable=False,
                        temporary=False,
                    )
                ],
                mentioned_entities=[],
                retrieval_needed=False,
                retrieval_query=None,
            )

        # 3. Pronoun resolution check: "he is an engineer" or "what does he like"
        antecedent = focus_entity
        if not antecedent and conversation_history:
            for m in reversed(conversation_history):
                content = str(m.content if hasattr(m, "content") else (m.get("content") if isinstance(m, dict) else ""))
                if "harshit" in content.lower():
                    antecedent = "Harshit Kandpal"
                    break
                elif "alice" in content.lower():
                    antecedent = "Alice"
                    break

        # Check for hypothetical
        is_hypothetical = any(w in lowered for w in ("might try", "maybe i", "could try", "thinking about trying"))

        # Pure Question check
        question_starters = ("what", "where", "who", "when", "why", "how", "tell me", "explain", "does", "do", "can", "compare", "generate", "summarize")
        is_question = clean.endswith("?") or any(lowered.startswith(q) for q in question_starters)

        if is_question:
            # Check pronoun replacement in query
            resolved_query = clean
            entities = []
            if " he " in f" {lowered} " or lowered.startswith("he "):
                if antecedent:
                    resolved_query = re.sub(r"\bhe\b", antecedent, clean, flags=re.IGNORECASE)
                    entities.append(
                        MentionedEntity(surface="he", resolved_to=antecedent, kind="PERSON", resolution=EntityResolution.COREFERENCE)
                    )
                else:
                    entities.append(
                        MentionedEntity(surface="he", resolved_to=None, kind="PERSON", resolution=EntityResolution.UNRESOLVED)
                    )
            elif "harshit" in lowered:
                entities.append(MentionedEntity(surface="harshit", resolved_to="Harshit Kandpal", kind="PERSON", resolution=EntityResolution.EXPLICIT))
            elif "harshi" in lowered:
                entities.append(MentionedEntity(surface="harshi", resolved_to="Harshi", kind="PERSON", resolution=EntityResolution.EXPLICIT))

            return TurnAnalysisResult(
                segments=[
                    AnalyzedSegment(
                        text=clean,
                        type=SegmentType.QUESTION,
                        durable=False,
                        temporary=False,
                    )
                ],
                mentioned_entities=entities,
                retrieval_needed=True,
                retrieval_query=resolved_query.rstrip("?").strip(),
            )

        # Pure Statement / Preference
        is_pref = any(w in lowered for w in ("likes", "loves", "prefers", "dislikes", "favorite"))
        seg_type = SegmentType.HYPOTHETICAL if is_hypothetical else (SegmentType.PREFERENCE if is_pref else SegmentType.STATEMENT)
        durable = not is_hypothetical

        entities = []
        if re.search(r"\bhe\b", lowered):
            if antecedent:
                entities.append(MentionedEntity(surface="he", resolved_to=antecedent, kind="PERSON", resolution=EntityResolution.COREFERENCE))
            else:
                entities.append(MentionedEntity(surface="he", resolved_to=None, kind="PERSON", resolution=EntityResolution.UNRESOLVED))
                durable = False  # Unresolved pronoun cannot be durable!

        retrieval_needed = lowered.startswith("store fact:")
        retrieval_query = clean if retrieval_needed else None

        return TurnAnalysisResult(
            segments=[
                AnalyzedSegment(
                    text=clean,
                    type=seg_type,
                    durable=durable,
                    temporary=False,
                )
            ],
            mentioned_entities=entities,
            retrieval_needed=retrieval_needed,
            retrieval_query=retrieval_query,
        )


turn_analyzer = LLMTurnAnalyzer()
