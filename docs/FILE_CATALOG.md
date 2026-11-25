# KryptoDB: Complete Codebase File Catalog

**Version:** 2.0.0  
**Classification:** Complete File-by-File Technical Directory Reference  
**Last Updated:** November 2026 (Phase 9 Complete)

This document provides an exhaustive, authoritative catalog detailing the exact purpose, key classes, exports, and dependencies of **every file in the KryptoDB repository**.

---

## Table of Contents

1. [Application Core (`kryptodb/core/`)](#1-application-core-kryptodbcore)
   - [Core Root](#11-core-root)
   - [Memory & Extraction (`kryptodb/core/memory/`)](#12-memory--extraction-kryptodbcorememory)
   - [Retrieval & GraphRAG (`kryptodb/core/retrieval/`)](#13-retrieval--graphrag-kryptodbcoreretrieval)
   - [Embeddings Subsystem (`kryptodb/core/embeddings/`)](#14-embeddings-subsystem-kryptodbcoreembeddings)
   - [Episodic Subsystem (`kryptodb/core/episodic/`)](#15-episodic-subsystem-kryptodbcoreepisodic)
   - [Identity & Security (`kryptodb/core/identity/`)](#16-identity--security-kryptodbcoreidentity)
2. [Infrastructure Layer (`kryptodb/infrastructure/`)](#2-infrastructure-layer-kryptodbinfrastructure)
   - [Neo4j Graph Infrastructure (`kryptodb/infrastructure/neo4j/`)](#21-neo4j-graph-infrastructure-kryptodbinfrastructureneo4j)
   - [Redis Infrastructure (`kryptodb/infrastructure/redis/`)](#22-redis-infrastructure-kryptodbinfrastructureredis)
3. [Interfaces & Ingress (`kryptodb/interfaces/`)](#3-interfaces--ingress-kryptodbinterfaces)
   - [MCP Protocol Adapter (`kryptodb/interfaces/mcp/`)](#31-mcp-protocol-adapter-kryptodbinterfacesmcp)
   - [HTTP REST & WebSocket Gateway (`kryptodb/interfaces/http/`)](#32-http-rest--websocket-gateway-kryptodbinterfaceshttp)
4. [Agent Runtime & HITL State Machine (`kryptodb/agent_runtime/`)](#4-agent-runtime--hitl-state-machine-kryptodbagent_runtime)
5. [Daemon Application (`kryptodb/daemon/`)](#5-daemon-application-kryptodbdaemon)
6. [Background Workers (`kryptodb/workers/`)](#6-background-workers-kryptodbworkers)
7. [Legacy Modules & Compatibility Shims](#7-legacy-modules--compatibility-shims)
8. [Frontend Web Application (`ui/`)](#8-frontend-web-application-ui)
9. [Operational & Benchmark Scripts (`scripts/`)](#9-operational--benchmark-scripts-scripts)
10. [Test Suites & Fixtures (`tests/`)](#10-test-suites--fixtures-tests)

---

## 1. Application Core (`kryptodb/core/`)

### 1.1. Core Root

#### [`kryptodb/core/context.py`](../kryptodb/core/context.py)
- **Purpose**: Defines the immutable security claims and multitenancy container used by every subsystem in KryptoDB.
- **Key Classes**:
  - `RequestContext`: Frozen dataclass containing `tenant_id`, `agent_id`, `user_id`, `session_id`, `active_scopes: FrozenSet[str]`, `now: datetime` (strict UTC), and `is_system: bool`.
- **Factory**: `RequestContext.create(...)` enforces timezone-aware UTC datetime validation and scope set normalization.
- **Used by**: All services, endpoints, filters, and store implementations.

#### [`kryptodb/core/filters.py`](../kryptodb/core/filters.py)
- **Purpose**: The **centralized query choke point** enforcing tenancy, status, scope access, and bi-temporal validity bounds across all Cypher queries.
- **Key Classes**:
  - `FilterBuilder`: Static utility class building parameterized Cypher `WHERE` fragments.
- **Key Methods**:
  - `FilterBuilder.build(context: RequestContext, alias: str = "r", system_mode: bool = False) -> Tuple[str, Dict[str, Any]]`: Returns parameterized Cypher clause ensuring `tenant_id`, `status='active'`, `valid_from <= now < valid_until`, and `scope IN active_scopes`. Emits `"1 = 0"` if scopes are unauthorized.
- **Used by**: `Neo4jMemoryStore`, `MultiHopTraverser`, `HybridSearchEngine`.

#### [`kryptodb/core/ports.py`](../kryptodb/core/ports.py)
- **Purpose**: Formal Protocol definitions establishing the Hexagonal Architecture boundary.
- **Key Protocols & Records**:
  - `MemoryStore(Protocol)`: `write_fact`, `get_fact`, `find_active_by_subject_predicate`, `supersede`, `atomic_supersede`, `revoke`, `revoke_by_user`, `search_vector`, `search_fulltext`, `find_entity`, `search_entities`, `add_entity`, `traverse_2hop`.
  - `EventStore(Protocol)`: `append`, `list_by_session`, `store_summary`.
  - `Embedder(Protocol)`: `embed`, `embed_batch`, `dimension`, `model_name`, `model_version`.
  - `Reranker(Protocol)`: `rerank`.
  - Dataclasses: `EntityRecord`, `ScoredItem`, `PathStep`, `PathResult`, `Event`, `EpisodeSummary`, `CandidateItem`.
- **Used by**: Domain services, `Neo4jMemoryStore`, `FakeMemoryStore`, HTTP routes.

#### [`kryptodb/core/settings.py`](../kryptodb/core/settings.py)
- **Purpose**: Unified configuration management for memory validation, ranking thresholds, and pipeline debugging.
- **Key Classes**:
  - `MemorySettings(BaseModel)`: Centralized pydantic settings including `min_fact_confidence` (default 0.60), `recall_relevance_cutoff` (default 0.20), `recall_max_facts` (default 8), `max_recent_turns` (default 5), and `grounding_check_mode` (`shadow` | `enforce`).
- **Export**: `settings = MemorySettings()`.

#### [`kryptodb/core/observability.py`](../kryptodb/core/observability.py)
- **Purpose**: OpenTelemetry tracer and span instrumentation across memory and retrieval pipelines.
- **Key Exports**: `tracer`, helper decorators for tracing sub-millisecond retrieval and storage operations.

---

### 1.2. Memory & Extraction (`kryptodb/core/memory/`)

#### [`kryptodb/core/memory/models.py`](../kryptodb/core/memory/models.py)
- **Purpose**: Core Pydantic data schemas representing graph facts, lifecycle statuses, and memory types.
- **Key Models**:
  - `MemoryStatus(Enum)`: `PROPOSED`, `ACTIVE`, `SUPERSEDED`, `DEPRECATED`, `REVOKED`.
  - `MemoryType(Enum)`: `FACT`, `PREFERENCE`, `OBSERVATION`, `DECISION`, `HYPOTHESIS`.
  - `MemoryRecord(BaseModel)`: Durable representation of a fact edge with `id`, `subject`, `predicate`, `object`, `description`, `scope`, `tenant_id`, `status`, `version`, `previous_version_id`, `confidence`, `trust_weight`, `valid_from`, `valid_until`, `source_agent_id`, `created_at`, `metadata`.

#### [`kryptodb/core/memory/memory_service.py`](../kryptodb/core/memory/memory_service.py)
- **Purpose**: Domain orchestrator for fact lifecycle: proposing, superseding, correcting, soft-revoking, and user-level data erasure.
- **Key Classes**:
  - `MemoryService`: Calls `PredicateRegistry` and `ConflictEngine`. Free of raw Cypher; strictly delegates to `MemoryStore`.
  - Methods: `propose_memory`, `get_memory`, `list_memories`, `correct_memory`, `forget_memory`, `forget_user`.
- **Export**: `memory_service = MemoryService()`.

#### [`kryptodb/core/memory/lifecycle.py`](../kryptodb/core/memory/lifecycle.py)
- **Purpose**: Formal finite-state-machine (FSM) governing legal lifecycle transitions for `MemoryRecord`.
- **Key Functions**:
  - `transition_status(record: MemoryRecord, new_status: MemoryStatus, context: RequestContext) -> MemoryRecord`: Validates transitions (e.g. `PROPOSED` $\to$ `ACTIVE`, `ACTIVE` $\to$ `SUPERSEDED`, `ACTIVE` $\to$ `REVOKED`), raising `InvalidLifecycleTransitionError` on illegal moves.

#### [`kryptodb/core/memory/predicates.py`](../kryptodb/core/memory/predicates.py)
- **Purpose**: Declarative ontology registry defining known predicates, aliases, and cardinality.
- **Key Classes**:
  - `Cardinality(Enum)`: `ONE` (functional, single active value), `MANY` (relational, multiple active values).
  - `PredicateDefinition`: Holds canonical name, cardinality, aliases, and description.
  - `PredicateRegistry`: Manages predicates with dynamic schema evolution (`auto_register_novel_predicate`).
- **Export**: `predicate_registry = PredicateRegistry()`.

#### [`kryptodb/core/memory/conflicts.py`](../kryptodb/core/memory/conflicts.py)
- **Purpose**: Contradiction detection engine identifying semantic conflicts between proposed facts and active state.
- **Key Classes**:
  - `ConflictEngine`: Inspects `PredicateRegistry`. If `Cardinality.ONE` and a fact already exists for `(subject, predicate)`, executes atomic supersession (`atomic_supersede`), transitioning the old fact to `SUPERSEDED`.

#### [`kryptodb/core/memory/delta_extractor.py`](../kryptodb/core/memory/delta_extractor.py)
- **Purpose**: Extracts stateful deltas, pronoun resolutions, and temporal mathematical modifications from conversational turns using LLM structured output.
- **Key Classes & Functions**:
  - `ExtractedTriple`, `DeltaExtractionResult`: Structured output schemas.
  - `extract_state_deltas(target_text, active_entity_state, temporal_anchor, recent_history, llm)`: Zero-shot prompt with strict canonical predicates (`RUNS`, `USES`, `DEPLOYED_ON`) and relative date calculation rules.

#### [`kryptodb/core/memory/entity_resolution.py`](../kryptodb/core/memory/entity_resolution.py)
- **Purpose**: Conservative identity resolution preventing incorrect node merges and identity bleed.
- **Key Classes & Functions**:
  - `normalize_entity_name(name: str) -> str`: Normalizes whitespace, casing, and punctuation.
  - `EntityResolver`: Performs exact normalized match for auto-merge. For near-matches ($\ge 0.70$ sequence ratio or prefix match), marks `is_unknown_entity=True` and generates `"Did you mean X?"` clarification suggestions.
- **Export**: `entity_resolver = EntityResolver()`.

#### [`kryptodb/core/memory/turn_analysis.py`](../kryptodb/core/memory/turn_analysis.py)
- **Purpose**: Discourse parsing decomposing complex multi-clause turns into typed atomic segments.
- **Key Classes & Functions**:
  - `SegmentType(Enum)`: `STATEMENT`, `QUESTION`, `PREFERENCE`, `OPINION`, `COMMAND`, `CHITCHAT`, `HYPOTHETICAL`.
  - `AnalyzedSegment`, `TurnAnalysisResult`: Discourse schemas.
  - `LLMTurnAnalyzer(TurnAnalyzer)`: Analyzes messages via structured Pydantic schema; enforces that questions are non-durable and statements without ungrounded coreferences are durable.
- **Export**: `turn_analyzer = LLMTurnAnalyzer()`.

#### [`kryptodb/core/memory/write_policy.py`](../kryptodb/core/memory/write_policy.py)
- **Purpose**: Strict validation gate checking incoming fact proposals before they reach the graph.
- **Key Functions**:
  - `validate_proposal(proposal, context, source_message_role, full_turn_text)`:
    - Enforces verbatim quotation match against `full_turn_text`.
    - Enforces `source_message_role == "user"`.
    - Rejects placeholder objects (`Object`, `None`, `null`, `value`, `Unknown`).
    - Rejects vague predicates (`associated with`, `person knows`).
    - Enforces confidence threshold ($\ge 0.60$).

#### [`kryptodb/core/memory/rejection_store.py`](../kryptodb/core/memory/rejection_store.py)
- **Purpose**: In-memory and durable hash registry tracking human-in-the-loop reviewer rejections.
- **Key Functions**:
  - `compute_proposal_hash(agent_id, proposal) -> str`: Computes SHA-256 hash of `(agent_id, subject, predicate, object, scope)`.
  - `record_rejection(...)`, `is_proposal_rejected(...)`, `clear_rejections()`.

---

### 1.3. Retrieval & GraphRAG (`kryptodb/core/retrieval/`)

#### [`kryptodb/core/retrieval/service.py`](../kryptodb/core/retrieval/service.py)
- **Purpose**: Unified entry point for GraphRAG retrieval combining hybrid search, 2-hop traversal, retrospective temporal edge fetching, and 3-stage ranking.
- **Key Classes**:
  - `RetrievalService`: Coordinates `HybridSearchEngine`, `MultiHopTraverser`, and `PathReranker`. Detects retrospective temporal intent (`"before"`, `"previously"`, `"history"`), fetching superseded edges with validity timestamps.
- **Export**: `retrieval_service = RetrievalService()`.

#### [`kryptodb/core/retrieval/hybrid_search.py`](../kryptodb/core/retrieval/hybrid_search.py)
- **Purpose**: Dual vector and fulltext BM25 search engine with reciprocal rank fusion and semantic fallback.
- **Key Classes**:
  - `HybridSearchEngine`: Executes parallel queries against Neo4j indexes and fuses candidate lists via RRF.
  - `find_semantic_fallback_seeds(query, tenant_id, top_k, active_scopes)`: Executes cosine similarity search over entity and relationship embeddings in the active scope when lexical search yields 0 seeds.
- **Export**: `hybrid_search_engine = HybridSearchEngine()`.

#### [`kryptodb/core/retrieval/rrf.py`](../kryptodb/core/retrieval/rrf.py)
- **Purpose**: Canonical implementation of Reciprocal Rank Fusion ($k=60$) deduplicating and combining ranked search result sets.
- **Key Functions**:
  - `reciprocal_rank_fusion(*ranked_lists: Sequence[List[Dict[str, Any]]], k: int = 60) -> List[Dict[str, Any]]`.

#### [`kryptodb/core/retrieval/traversal.py`](../kryptodb/core/retrieval/traversal.py)
- **Purpose**: Cycle-safe, bounded 1-to-2 hop graph expansion with hub degree suppression.
- **Key Classes**:
  - `MultiHopTraverser`: Executes `TRAVERSAL_1HOP_QUERY` and `TRAVERSAL_2HOP_EXPANSION_QUERY`, filtering out hub entities ($\text{degree} > 50$) and cycle paths ($s \ne t \land s \ne \text{mid} \land \text{mid} \ne t$).
- **Export**: `multihop_traverser = MultiHopTraverser()`.

#### [`kryptodb/core/retrieval/ranking.py`](../kryptodb/core/retrieval/ranking.py)
- **Purpose**: Scale-harmonized composite reranking pipeline.
- **Key Classes & Models**:
  - `AttributedMemoryResult`: Final structured memory item with provenance steps, confidence, final score, and citation tag.
  - `PathReranker`: Computes normalized cross-encoder semantic score, mean path confidence, exponential recency decay, and a single controlled hop penalty.
- **Export**: `path_reranker = PathReranker()`.

#### [`kryptodb/core/retrieval/memory_context.py`](../kryptodb/core/retrieval/memory_context.py)
- **Purpose**: Formats retrieved memories into canonical prompt injection XML blocks with explicit citation tags.
- **Key Functions**:
  - `format_injected_memory_context(recalled_facts)`: Wraps memories in `<retrieved_memories>` data tags and `<memory_rules>`, instructing LLMs that memories are stored data, not current turn statements.
  - `check_grounding(response_text, retrieved_facts)`: Verifies whether entities mentioned in LLM answers originate from the injected context.

#### [`kryptodb/core/retrieval/provenance.py`](../kryptodb/core/retrieval/provenance.py)
- **Purpose**: Formatting of verifiable causal citation tags and human-readable graph paths.
- **Key Functions**:
  - `format_citation(tenant_id, scopes, edge_ids) -> str`: Emits `[tenant:..., scope:..., edges:e1->e2]`.
  - `format_provenance_chain(steps: Sequence[ProvenanceStep]) -> str`: Emits `(A)-[PRED]->(B)-[PRED]->(C)`.

#### [`kryptodb/core/retrieval/query.py`](../kryptodb/core/retrieval/query.py)
- **Purpose**: Deterministic, sub-millisecond query parsing extracting cleaned tokens, quoted terms, and temporal keywords without external LLM calls.
- **Key Functions**:
  - `parse_query(raw_query: str) -> ParsedQuery`.

---

### 1.4. Embeddings Subsystem (`kryptodb/core/embeddings/`)

#### [`kryptodb/core/embeddings/embedder.py`](../kryptodb/core/embeddings/embedder.py)
- **Purpose**: Dense vector embedder implementing `Embedder` protocol using `sentence-transformers` (`all-MiniLM-L6-v2`, 384 dimensions) with deterministic offline fallback.
- **Key Classes**: `LocalSentenceEmbedder`.

#### [`kryptodb/core/embeddings/versioning.py`](../kryptodb/core/embeddings/versioning.py)
- **Purpose**: Embedder model version metadata tracking and dimension compatibility validation.
- **Key Classes**: `EmbeddingMetadata`.

#### [`kryptodb/core/embeddings/queue.py`](../kryptodb/core/embeddings/queue.py)
- **Purpose**: Resilient memory queue for deferred background embedding generation when external embedding providers fail or operate in offline mode.
- **Key Classes**: `EmbeddingQueue`.

---

### 1.5. Episodic Subsystem (`kryptodb/core/episodic/`)

#### [`kryptodb/core/episodic/events.py`](../kryptodb/core/episodic/events.py)
- **Purpose**: Episodic conversation turn logging.
- **Key Functions**:
  - `record_event(session_id, role, content, context, store, scope, event_id) -> Event`.

#### [`kryptodb/core/episodic/summaries.py`](../kryptodb/core/episodic/summaries.py)
- **Purpose**: Conversation session summarization, topic extraction, and durable episode registration.
- **Key Functions**:
  - `summarize_session(session_id, context, store, summarizer, scope, episode_id) -> EpisodeSummary`.

---

### 1.6. Identity & Security (`kryptodb/core/identity/`)

#### [`kryptodb/core/identity/policy.py`](../kryptodb/core/identity/policy.py)
- **Purpose**: Centralized role-based access control (RBAC), action authorization (`memory:read`, `memory:write`, `memory:revoke`, `chat:read`, `admin:*`), and scope intersection gating.
- **Key Classes**:
  - `PolicyEngine`: Authorizes actions by intersecting requested scopes with token permissions and roles (`reader`, `writer`, `admin`).
  - `PolicyConfig`, `AgentPolicyEntry`: Declarative policy schema for bootstrapping agent permissions via JSON (`KRYPTODB_POLICY_FILE`).
- **Export**: `policy_engine = PolicyEngine()`, `PolicyDeniedError`.

#### [`kryptodb/core/identity/authentication.py`](../kryptodb/core/identity/authentication.py)
- **Purpose**: Persistent, fail-closed API key verification backed by SQLite (`data/api_keys.db`). Keys are hashed with SHA-256 and verified using constant-time comparison.
- **Key Classes**:
  - `Authenticator`: Refuses startup if `KRYPTODB_ADMIN_API_KEY` is missing; issues and revokes agent keys across server restarts with thread locking.
- **Export**: `authenticator = Authenticator()`.

#### [`kryptodb/core/identity/session_context.py`](../kryptodb/core/identity/session_context.py)
- **Purpose**: Manages active multi-turn agent session state and runtime dynamic scope activation/deactivation.
- **Key Classes**: `SessionManager`, `SessionContext`.
- **Export**: `session_manager = SessionManager()`.

---

## 2. Infrastructure Layer (`kryptodb/infrastructure/`)

#### [`kryptodb/infrastructure/audit_ledger.py`](../kryptodb/infrastructure/audit_ledger.py)
- **Purpose**: Tamper-evident, append-only SQLite audit ledger (`data/audit.db`) using SHA-256 hash chains to record all writes, revocations, and administrative actions.
- **Key Classes**: `AuditLedger`, `AuditEntry`.
- **Export**: `audit_ledger = AuditLedger()`.

### 2.1. Neo4j Graph Infrastructure (`kryptodb/infrastructure/neo4j/`)

#### [`kryptodb/infrastructure/neo4j/client.py`](../kryptodb/infrastructure/neo4j/client.py)
- **Purpose**: Resilient Neo4j Bolt driver wrapper featuring exponential backoff retries on transient connection and lock acquisition failures.
- **Key Classes**:
  - `Neo4jClient`: Exposes `execute_query_with_retry(query, **params)` and manages driver connection pooling.
- **Export**: `neo4j_client = Neo4jClient()`.

#### [`kryptodb/infrastructure/neo4j/encrypted_store.py`](../kryptodb/infrastructure/neo4j/encrypted_store.py)
- **Purpose**: v3 Encrypted Neo4j memory store writing encrypted envelopes and HMAC blind indexes to Neo4j.
- **Key Classes**: `EncryptedNeo4jMemoryStore`.

#### [`kryptodb/infrastructure/neo4j/encrypted_retrieval.py`](../kryptodb/infrastructure/neo4j/encrypted_retrieval.py)
- **Purpose**: v3 Encrypted retrieval engine coordinating blind-index keyword matching, decrypted in-memory `RAMVectorIndex` dense search, and 2-hop traversal.
- **Key Classes**: `EncryptedRetrievalService`.

#### [`kryptodb/infrastructure/neo4j/encrypted_queries.py`](../kryptodb/infrastructure/neo4j/encrypted_queries.py)
- **Purpose**: Parameterized Cypher queries for encrypted fact storage, atomic supersession, and blind-index searches.

### 2.2. Cryptographic Storage Subsystem (`kryptodb/infrastructure/crypto/`)

#### [`kryptodb/infrastructure/crypto/envelope.py`](../kryptodb/infrastructure/crypto/envelope.py)
- **Purpose**: AES-256-GCM authenticated encryption with associated data (AAD) binding tenant, fact ID, version, and scope.
- **Key Classes**: `AESGCMEnvelopeCipher`.

#### [`kryptodb/infrastructure/crypto/blind_index.py`](../kryptodb/infrastructure/crypto/blind_index.py)
- **Purpose**: HMAC-SHA256 blind indexing for exact predicate, subject, and token keyword searches over encrypted properties without leaking plaintext to Neo4j.
- **Key Classes**: `BlindIndexEngine`, `TokenSearchIndexEngine`.

#### [`kryptodb/infrastructure/crypto/keyring.py`](../kryptodb/infrastructure/crypto/keyring.py)
- **Purpose**: Cryptographic key management supporting key rotation, master key derivation via HKDF, and local key file persistence.
- **Key Classes**: `LocalKeyFileProvider`, `LocalKeyring`.

#### [`kryptodb/infrastructure/crypto/ram_vector_index.py`](../kryptodb/infrastructure/crypto/ram_vector_index.py)
- **Purpose**: In-memory dense cosine vector index holding embeddings of decrypted active facts in process RAM to prevent storing plaintext vector embeddings in Neo4j.
- **Key Classes**: `RAMVectorIndex`.

#### [`kryptodb/infrastructure/neo4j/queries.py`](../kryptodb/infrastructure/neo4j/queries.py)
- **Purpose**: **Centralized Cypher query repository**. Contains all parameterized Cypher query strings used across the entire application:
  - `WRITE_FACT_QUERY`, `ATOMIC_SUPERSEDE_QUERY`, `SUPERSEDE_FACT_QUERY`, `REVOKE_FACT_QUERY`
  - `REVOKE_BY_USER_QUERY`, `SEARCH_ENTITIES_PREFIX_QUERY`, `ADD_ENTITY_QUERY`, `FIND_ENTITY_QUERY`
  - `GET_FACT_QUERY`, `FIND_ACTIVE_BY_SUBJECT_PREDICATE_QUERY`
  - `SEARCH_FACT_VECTOR_QUERY`, `SEARCH_ENTITY_VECTOR_QUERY`
  - `SEARCH_FACT_FULLTEXT_QUERY`, `SEARCH_ENTITY_FULLTEXT_QUERY`
  - `TRAVERSE_2HOP_QUERY`, `TRAVERSAL_1HOP_QUERY`, `TRAVERSAL_2HOP_EXPANSION_QUERY`
  - `RETROSPECTIVE_FACTS_QUERY`
  - `HYBRID_VECTOR_ENTITY_QUERY`, `HYBRID_VECTOR_ENTITY_ALT_QUERY`, `HYBRID_FULLTEXT_ENTITY_QUERY`
  - `SEMANTIC_SEED_FALLBACK_ENTITY_QUERY`, `SEMANTIC_SEED_FALLBACK_FACT_QUERY`
  - `APPEND_EVENT_QUERY`, `LIST_EVENTS_BY_SESSION_QUERY`, `STORE_EPISODE_SUMMARY_QUERY`

#### [`kryptodb/infrastructure/neo4j/store.py`](../kryptodb/infrastructure/neo4j/store.py)
- **Purpose**: Durable Neo4j implementation of the `MemoryStore` and `EventStore` protocols.
- **Key Classes**:
  - `Neo4jMemoryStore`: Implements all graph write, read, supersede, revocation, entity search, and event logging operations.
- **Export**: `Neo4jMemoryStore`.

#### [`kryptodb/infrastructure/neo4j/schema.py`](../kryptodb/infrastructure/neo4j/schema.py)
- **Purpose**: Automated database schema initialization. Creates native 384-dim vector indexes, Lucene fulltext indexes, uniqueness constraints, and composite tenant indexes on boot.
- **Key Functions**: `ensure_schema(driver) -> Dict[str, Any]`.

---

### 2.2. Redis Infrastructure (`kryptodb/infrastructure/redis/`)

#### [`kryptodb/infrastructure/redis/client.py`](../kryptodb/infrastructure/redis/client.py)
- **Purpose**: Redis connection client managing lock acquisition and embedding queues.
- **Export**: `redis_client`.

---

## 3. Interfaces & Ingress (`kryptodb/interfaces/`)

### 3.1. MCP Protocol Adapter (`kryptodb/interfaces/mcp/`)

#### [`kryptodb/interfaces/mcp/server.py`](../kryptodb/interfaces/mcp/server.py)
- **Purpose**: FastMCP protocol server exposing KryptoDB memory tools directly to LLM agents (Claude Desktop, Cursor, Windsurf). Contains zero domain logic; strictly translates arguments and delegates to `MemoryService` and `RetrievalService`.
- **Exposed Tools**:
  - `propose_memory`: Proposes a candidate fact with quote verification and cardinality conflict resolution.
  - `query_memory`: GraphRAG search with multi-hop traversal and attribution.
  - `list_memories`: Scoped fact listing.
  - `correct_memory`: Corrects/supersedes a fact.
  - `forget_memory`: Soft-revokes a fact.
  - `record_event`: Logs an episodic conversation event.

---

### 3.2. HTTP REST & WebSocket Gateway (`kryptodb/interfaces/http/`)

#### [`kryptodb/interfaces/http/middleware/context.py`](../kryptodb/interfaces/http/middleware/context.py)
- **Purpose**: FastAPI dependency extracting HTTP headers (`X-Tenant-ID`, `X-Agent-ID`, `X-Scopes`, `X-User-ID`, `X-Session-ID`) and constructing the validated `RequestContext`.
- **Key Functions**: `get_request_context(...) -> RequestContext`.

#### [`kryptodb/interfaces/http/routes/v1_routes.py`](../kryptodb/interfaces/http/routes/v1_routes.py)
- **Purpose**: Official unified GraphRAG REST endpoints mounted under `/v1`:
  - `POST /v1/memory/propose`: Proposes and validates candidate facts.
  - `POST /v1/memory/search`: GraphRAG search (RRF + 2-hop + composite reranking).
  - `GET /v1/memory`: Filtered listing of memory facts.
  - `GET /v1/memory/{id}`: Memory inspection by ID.
  - `PATCH /v1/memory/{id}`: Memory fact correction.
  - `DELETE /v1/memory/{id}`: Memory revocation.
  - `DELETE /v1/user/{user_id}`: GDPR user data revocation.
  - `POST /v1/events`: Episodic event recording.

#### [`kryptodb/interfaces/http/routes/chat.py`](../kryptodb/interfaces/http/routes/chat.py)
- **Purpose**: Real-time conversational streaming and Human-In-The-Loop approval endpoints:
  - `WebSocket /chat/{thread_id}/stream`: Bi-directional streaming yielding token chunks, `fact_pills`, and `pending_approval` interrupts.
  - `POST /chat/{thread_id}/approve`: Approves pending sensitive memory proposal and resumes agent execution.
  - `POST /chat/{thread_id}/reject`: Rejects pending proposal, registers hash in `RejectionStore`, and resumes execution.
  - `POST /chat/{thread_id}/message`: HTTP conversational ingress.

#### [`kryptodb/interfaces/http/routes/graph.py`](../kryptodb/interfaces/http/routes/graph.py)
- **Purpose**: Visualization snapshot endpoint (`GET /graph/snapshot`) supplying node coordinates and edge metadata to the Next.js WebGL canvas.

#### [`kryptodb/interfaces/http/routes/admin.py`](../kryptodb/interfaces/http/routes/admin.py)
- **Purpose**: Administrative endpoints for managing agent permissions, Allowed Scopes, and API keys (`POST /admin/agents`).

#### [`kryptodb/interfaces/http/routes/health.py`](../kryptodb/interfaces/http/routes/health.py)
- **Purpose**: System health check (`GET /healthz`) probing Neo4j connectivity and schema readiness.

#### [`kryptodb/interfaces/http/routes/memory.py`](../kryptodb/interfaces/http/routes/memory.py)
- **Purpose**: Backward-compatible route aliases supporting legacy `/api/v1/memory/*` paths.

---

## 4. Agent Runtime & HITL State Machine (`kryptodb/agent_runtime/`)

#### [`kryptodb/agent_runtime/langgraph/graph.py`](../kryptodb/agent_runtime/langgraph/graph.py)
- **Purpose**: The complete LangGraph conversational memory state graph.
- **Key Nodes**:
  - `intent_classifier_node`: Analyzes turn discourse via `turn_analyzer`.
  - `recall_node` & `tools: query_memory_tool`: Executes GraphRAG search.
  - `reasoning_node`: Synthesizes final assistant answer using `<retrieved_memories>` context.
  - `extraction_node`: Extracts durable facts from user statements.
  - `conflict_resolution_node`: Resolves cardinality supersession.
  - `direct_write_node`: Commits approved facts to Neo4j and publishes to `state.system_events`.
  - `hitl_interrupt`: Halts graph on sensitive scopes (`CONFIDENTIAL`, `RESTRICTED`) using LangGraph `interrupt()`.
  - `rejection_node`: Logs user rejection to `RejectionStore`.
- **Durable Checkpointer**: Supports SQLite and Memory checkpointers (`get_agent_checkpointer`).

---

## 5. Daemon Application (`kryptodb/daemon/`)

#### [`kryptodb/daemon/app.py`](../kryptodb/daemon/app.py)
- **Purpose**: FastAPI application factory (`create_app()`). Configures CORS, lifecycle hooks (ensuring Neo4j schema on boot), and mounts all REST routers.
- **Export**: `app = create_app()`.

#### [`kryptodb/daemon/server.py`](../kryptodb/daemon/server.py)
- **Purpose**: Entry point running Uvicorn server for local development.

#### [`kryptodb/daemon/auth.py`, `sessions.py`, `mcp_server.py`](../kryptodb/daemon/)
- **Purpose**: Backward-compatibility re-export shims forwarding legacy imports to their canonical locations in `core/identity/` and `interfaces/mcp/`.

---

## 6. Background Workers (`kryptodb/workers/`)

#### [`kryptodb/workers/embedding_retry.py`](../kryptodb/workers/embedding_retry.py)
- **Purpose**: Background daemon worker polling the `EmbeddingQueue` to compute and attach dense vector embeddings to newly created entities and facts.

#### [`kryptodb/workers/plaintext_audit.py`](../kryptodb/workers/plaintext_audit.py)
- **Purpose**: Zero-crypto isolation audit scanner inspecting Neo4j for unencrypted fact leaks and validating property allowlists.

---

## 7. Configuration & Persistent Data Stores (`data/`)

- **`data/api_keys.db`**: Persistent SQLite database holding SHA-256 hashed API keys, roles, and allowed scopes.
- **`data/audit.db`**: Tamper-evident append-only SQLite hash chain recording every memory mutation.
- **`data/ontology.toml`**: Canonical declarative schema definitions for entity types and predicate cardinalities.
- **`data/agents.json`**: Initial declarative agent roster and baseline trust configurations.

---

## 8. Frontend Web Application (`ui/`)

#### [`ui/src/app/page.tsx`](../ui/src/app/page.tsx)
- **Purpose**: Landing page and overview dashboard displaying live system status, active agents, and schema ontology.

#### [`ui/src/app/playground/page.tsx`](../ui/src/app/playground/page.tsx)
- **Purpose**: Primary interactive console embedding the Sigma.js graph visualizer alongside the chat popup.

#### [`ui/src/app/GraphExplorer.tsx`](../ui/src/app/GraphExplorer.tsx)
- **Purpose**: WebGL canvas using Sigma.js and Graphology. Renders nodes and edges with ForceAtlas2 physics layout, color-coded by scope, with real-time edge highlight animations.

#### [`ui/src/app/ChatPopup.tsx`](../ui/src/app/ChatPopup.tsx)
- **Purpose**: Real-time streaming conversational chat interface communicating via WebSockets. Supports interactive HITL approval cards and fact attribution tags.

#### [`ui/src/components/earth-graph.tsx`](../ui/src/components/earth-graph.tsx)
- **Purpose**: Three.js/WebGL 3D orbital globe visualization projecting multi-region entity infrastructure and cross-tenant relationships.

#### [`ui/src/app/layout.tsx`](../ui/src/app/layout.tsx)
- **Purpose**: Global HTML layout and metadata configuration for the Next.js application.

---

## 9. Operational & Benchmark Scripts (`scripts/`)

#### [`scripts/eval_kryptodb_strict.py`](../scripts/eval_kryptodb_strict.py)
- **Purpose**: Authoritative 5-test autonomous agent evaluation benchmark testing Meta-Llama 3.3 70B Instruct against coreference resolution, directed polarity, 2-hop bridge discovery, bi-temporal supersession, and 500-event hub distractor suppression.

#### [`scripts/run_eval_benchmark.py`](../scripts/run_eval_benchmark.py)
- **Purpose**: Runs the 50-query gold standard retrieval benchmark, calculating Recall@10, Precision@10, nDCG@10, and MRR.

#### [`scripts/migrate_to_plaintext.py`](../scripts/migrate_to_plaintext.py)
- **Purpose**: Migration utility decrypting legacy encrypted `:FACT` blobs into native plaintext Neo4j properties (`--dry-run` and `--live`).

#### [`scripts/reembed.py`](../scripts/reembed.py)
- **Purpose**: Utility computing dense vector embeddings for all existing plaintext entities and facts in Neo4j.

#### [`scripts/eval_ablations.py`](../scripts/eval_ablations.py)
- **Purpose**: Step 4 multi-hop GraphRAG ablation runner comparing Flat Vector-Only vs Hybrid (Vector+BM25) vs Full Pipeline (Hybrid+2-Hop Traversal+Cross-Encoder). Also supports `--storage-mode encrypted`.

#### [`scripts/replay_transcript.py`](../scripts/replay_transcript.py)
- **Purpose**: Diagnostic replay script reproducing multi-turn conversational transcripts to detect memory bleed or synthetic feedback loops.

#### [`scripts/check_neo4j_capabilities.py`](../scripts/check_neo4j_capabilities.py)
- **Purpose**: Diagnostic probe inspecting active Neo4j indexes, procedures, and vector capabilities.

---

## 10. Test Suites & Fixtures (`tests/`)

#### [`tests/fakes.py`](../tests/fakes.py)
- **Purpose**: High-fidelity in-memory test doubles implementing all core protocols:
  - `FakeMemoryStore`: Implements `MemoryStore` with full support for vector scoring, 2-hop traversal, entity search, and revocation.
  - `FakeEventStore`: Implements `EventStore`.
  - `FakeEmbedder`: Deterministic embedding generator.
  - `FakeReranker`: Jaccard-based deterministic reranker.
  - `FakeTurnAnalyzer`: Deterministic discourse analyzer with test fixture overrides.
  - `StubExtractionLLM`: Deterministic mock LLM returning pre-configured triples for testing.

#### [`tests/eval/`](../tests/eval/)
- **`test_gold_benchmark.py`**: Executes the 50-query gold standard retrieval evaluation.
- **`test_tiered_benchmark.py`**: 8-tier benchmark evaluating latency, multihop, and cardinality constraints (T0 to T7).
- **`test_memory_engine_e2e.py`**: End-to-end memory engine validation verifying directionality, temporal bounds, and scope isolation.
- **`metrics.py`**: Mathematical definitions for Recall@k, Precision@k, and nDCG@k.

#### [`tests/integration/`](../tests/integration/)
- **`test_architectural_remediation.py`**: Verifies elimination of architectural defects (stateless extraction, output stack clobbering, command boolean gating).
- **`test_multi_thread_scope_isolation.py`**: Tests concurrent multi-agent thread isolation and cross-thread access denial.
- **`test_phase1.py` - `test_phase3.py`**: Integration tests across schema bootstrap, write paths, and traversal.
- **`mcp/test_phase4.py`**: Integration tests for FastMCP server tool execution.

#### [`tests/memory_repair/test_memory_repair.py`](../tests/memory_repair/test_memory_repair.py)
- **Purpose**: 12 comprehensive regression tests validating turn analysis, quote verification, unknown entity resolution, groundness checking, rejection store persistence, and prompt injection formatting.

#### [`tests/unit/`](../tests/unit/)
- **`security/`**: P0 security, fail-closed auth, persistent SQLite key store, and unified `PolicyEngine` enforcement (`test_p0_security_and_correctness.py`, `test_step1_unified_access_control.py`).
- **`crypto/`**: Encrypted memory v3 gate suites verifying zero-plaintext leaks, HMAC blind indexing, AES-256-GCM envelope integrity, `RAMVectorIndex`, and key rotation (`test_encrypted_phase1.py`, `test_encrypted_phase2.py`, `test_encrypted_phase3.py`).
- **`agent/`**: 9 unit tests covering state graph routing, HITL checkpointers, intent classification, and streaming transport.
- **`daemon/`**: Tests for session management, API key authentication, and observability.
- **`graph/`**: Tests for concurrency locks, atomic Cypher writes, and relationship indexing.
- **`interfaces/`**: Tests verifying bit-for-bit contract parity between FastMCP tools and REST `/v1` endpoints.
- **`memory/`**: Tests for `MemoryService`, `ConflictEngine`, `EntityResolver`, and `WritePolicy`.
- **`retrieval/`**: Tests for `FilterBuilder` choke point, hybrid RRF, 2-hop traversal, and cross-encoder ranking.
- **`storage/`**: Tests for raw storage client drivers and resilience.
