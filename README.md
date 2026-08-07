# Project Synapse v2

**Subtitle:** A Trust-Brokered, Temporally-Aware Memory Substrate for Multi-Agent Systems

---

## 0. What changed from v1

v1 was a single-agent local GraphRAG memory (Neo4j + SLM extraction + LightRAG retrieval).
That pattern is commoditized — Zep/Graphiti, Mem0/OpenMemory, and Cognee all occupy it.

v2's actual claim: memory written and read by **multiple non-fully-trusted agents**, where
every fact carries provenance, a trust weight, a validity window, and a permission scope —
and all four properties propagate through derivation (summaries, embeddings, extracted facts)
via the same lineage graph. No existing OSS memory layer treats "who wrote this," "is it still
true," and "who can see it" as three faces of one edge property set. That's the differentiator.

---

## 1. Core Thesis

Big labs won't build this. Their memory products (ChatGPT memory, Claude memory) assume one
vendor, one trust domain, one user reading their own writes. This system assumes the opposite:
several independent agents (possibly different models, possibly adversarial or just unreliable)
proposing facts into a shared graph that a single consumer, or another agent, later reads.

**Note on framing:** MCP (Section 9) is the transport agents use to reach this system — it is
not what makes this a memory system, any more than HTTP makes something a website. Swap MCP for
a REST API and nothing in Sections 2–8 changes. The differentiator is entirely in the
consensus/trust gate, bi-temporal validity, and permission/lineage propagation — that is what
should lead any description of this project, not "MCP memory server," which puts it in the same
bucket as Mem0/Zep/Engram for reasons that have nothing to do with what's actually novel here.

That reframes the design problem from "retrieve relevant facts" to "decide which proposed facts
are admissible, how long they stay admissible, and who's allowed to see them."

---

## 2. System Architecture

```
                    ┌─────────────────────────────────────────┐
                    │              WRITE PATH                  │
                    └─────────────────────────────────────────┘

  Agent A          Agent B          Agent C
  (malware-det)    (research-log)   (resume-draft)
     │                 │                 │
     │  proposes facts │                 │
     ▼                 ▼                 ▼
  ┌───────────────────────────────────────────┐
  │         Extraction Layer (per-agent)        │
  │   SLM (Ollama 3B) + Instructor → JSON        │
  │   tags: entity, relation, permission_scope   │
  │   NON-deterministic. Advisory only.          │
  └───────────────────┬───────────────────────┘
                       ▼
  ┌───────────────────────────────────────────┐
  │        Consensus / Trust Gate               │
  │  - source_agent_id → trust_weight lookup     │
  │  - conflict check vs existing valid edges    │
  │  - agreement threshold for contested facts   │
  │  DETERMINISTIC. No LLM in this path.         │
  └───────────────────┬───────────────────────┘
                       ▼
  ┌───────────────────────────────────────────┐
  │        Cypher Write Engine                   │
  │  - opens new edge, closes superseded edge    │
  │  - stamps valid_from / valid_to              │
  │  - stamps permission_scope, unlock_at        │
  │  - writes derived_from edges (lineage)       │
  └───────────────────┬───────────────────────┘
                       ▼
              ┌─────────────────┐
              │   Neo4j Graph     │
              └─────────────────┘
                       │
                    READ PATH
                       │
                       ▼
  ┌───────────────────────────────────────────┐
  │     Permission Bitmap Index (Redis/local KV) │
  │  precomputed from ACL sync stream            │
  │  O(1) allow/deny per node                    │
  │  DETERMINISTIC. No LLM in this path.         │
  └───────────────────┬───────────────────────┘
                       ▼
  ┌───────────────────────────────────────────┐
  │     Trust-Weighted, Time-Filtered Retrieval  │
  │  LightRAG dual-mode (local / global)         │
  │  ranks by: recency, trust_weight, relevance  │
  └───────────────────┬───────────────────────┘
                       ▼
                Requesting Agent / User
                       │
                       ▼
  ┌───────────────────────────────────────────┐
  │     Audit Log (append-only, hash-chained)     │
  │  who / what / when / permission_result       │
  └───────────────────────────────────────────┘
```

---

## 3. Data Schema

### Node types (unchanged from v1, unchanged ontology discipline)
`PROJECT`, `SKILL`, `ORGANIZATION`, `CONCEPT`, `TIMESTAMP`

### Edge properties (this is the actual contribution)

```
(:Fact)-[:RELATION {
    source_agent_id:   string   // who proposed this
    trust_weight:       float    // 0.0–1.0, per-agent, updated by outcome feedback
    valid_from:         datetime // when the fact became true
    valid_to:           datetime // null if still valid; set on supersession
    ingested_at:        datetime // when it entered the graph
    permission_scope:   enum     // e.g. CORE, RESEARCH_SILO, LEADERSHIP
    unlock_at:          datetime // null unless a temporal access rule applies
    derived_from:       [edge_id]  // lineage — empty if this is a primary source fact
    superseded_by:      edge_id   // null unless closed by a newer fact
}]->(:Entity)
```

Every derived artifact (summary, embedding, extracted sub-fact) MUST populate `derived_from`.
Enforcement and revocation both walk this edge, nothing else.

---

## 4. The Three Gates (why this isn't just Graphiti + an ACL bolt-on)

| Gate | Question it answers | Where it runs | Deterministic? |
|---|---|---|---|
| Trust/consensus | Should this fact be admitted at all? | Write path | Yes |
| Temporal validity | Is this fact still true *right now*? | Read path filter | Yes |
| Permission | Is *this reader* allowed to see it? | Read path filter | Yes |

All three are edge-property filters over the same graph, applied in sequence at query time.
None of them invoke an LLM. The LLM's only job is proposing candidate facts and initial
classification tags at write time — it never makes an admission, validity, or access decision.

---

## 5. Revocation & Propagation

Two independent revocation triggers, same mechanism:

- **Trust revocation**: source agent's `trust_weight` drops below threshold (e.g. repeated
  contradicted facts) → all edges with that `source_agent_id` get re-evaluated; low-confidence
  ones are closed (`valid_to = now`), not deleted.
- **Permission revocation**: source ACL changes upstream → sync stream marks the source node
  `permission_scope` as revoked → graph walk over `derived_from` cascades the scope change to
  every derivative, however many hops deep.

Both are graph traversals from a single changed node outward along `derived_from` / reverse
`RELATION` edges — same traversal code path, different trigger and different target property.

---

## 6. Implementation Phases

**Phase 1 — Substrate** (mostly = v1 Phase 1)
Neo4j, Ollama (3B extraction model), bge-small embeddings, Instructor for structured output.

**Phase 2 — Trust & Consensus Gate**
Define `source_agent_id` registry with initial trust priors. Build the deterministic admission
function: agreement threshold for contested facts, trust-weighted acceptance for single-source
facts below a confidence bar. This is the FedAvg-adjacent piece — reuse weighting logic from
prior federated-learning work, applied to fact admission instead of gradient aggregation.

**Phase 3 — Bi-Temporal Write Engine**
Replace v1's `DELETE`-on-update with close-and-open: new conflicting fact never deletes the old
edge, it sets `valid_to` on the old and opens a new edge with its own `valid_from`. Add
`unlock_at` support for temporal access rules.

**Phase 4 — Permission Layer**
ACL sync stream (poll or webhook from source systems, or manually declared scopes for a
single-user build). Precomputed bitmap index for O(1) scope checks. Lineage-cascade revocation
via `derived_from` traversal (Section 5).

**Phase 5 — Retrieval + Audit**
LightRAG dual-mode retrieval, now filtered through trust + temporal + permission gates in
sequence before ranking. Append-only audit log (hash-chained if you want the exercise; a plain
timestamped write-log is sufficient at single-user scale).

**Phase 6 — Interface**
CLI or FastAPI/Streamlit. Neo4j Bloom for visual debugging of lineage and validity chains
specifically — this is the one place a graph visualizer earns its keep, since debugging
"why did this revoke cascade wrong" by eyeballing Cypher output is painful.

---

## 7. Explicit Non-Goals (state these — don't let a reviewer think you missed them)

- **Sub-200ms P99 permission checks at scale**: not attempted. Single-user local deployment
  doesn't need a distributed bitmap cache. Noted as future work if this ever goes multi-tenant.
- **Regulatory-grade audit logging** (SOC 2 / HIPAA-style tamper-evidence): out of scope. A
  plain append-only log demonstrates the pattern; full compliance tooling is a different project.
- **Query-time inference prevention** (detecting cross-document permission leakage via
  triangulation): unsolved even in well-funded commercial systems. Not attempted. Documented as
  a known limitation, not a promised feature.

---

## 8. Open Design Decisions

1. **Trust weight initialization** — cold-start problem for a new agent with no history. Flat
   prior + fast adaptation, or require a manual trust ceiling until N validated facts?
2. **Contested-fact resolution & MoE Routing** — if two equally trusted agents (e.g., both trust 0.8) propose directly contradictory facts, averaging doesn't work for discrete data (e.g., a server IP). **Decision:** Apply a **Mixture-of-Experts (MoE) Top-K Routing** approach to dynamically route the conflict to a specialized "Expert" agent based on the `permission_scope` (e.g., routing codebase conflicts strictly to the `malware-det` agent) to act as the automated arbiter.
3. **Continuous Trust Evaluation** — when adjusting `trust_weight` due to supersession or conflicts, avoid all-or-nothing penalties (which often falsely flag as "cliffs" in capability). Apply continuous, smooth penalty decay to prevent sudden drops in an agent's authority over a single partially-incorrect fact.
4. **Silo permission_scope granularity** — is `permission_scope` per-silo (coarse, matches v1's
   `Unified_Core` / `Research_Silo` split) or per-node? Per-node is more correct but multiplies
   the ACL-sync surface area for a single-user build with limited payoff.
5. **Embedding model scope leakage** — if `bge-small` embeds text from a restricted node, does
   the resulting vector itself need scope tagging in the vector index, or is scope enforcement
   sufficient at the graph layer before embeddings are even generated? Leaning toward: never
   embed a restricted node until it clears the permission gate, so the vector store never holds
   an unscoped copy of restricted content.

---

## 9. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| "Cascade Storm" (Write Amplification) | A single core fact revocation triggers massive graph traversal, locking the database during bulk updates of thousands of descendants | Implement lazy/deferred revocations (asynchronous cascading) instead of blocking the write path, alongside periodic lineage flattening |
| 3B extraction model produces schema-invalid or hallucinated relations | Bad facts enter the consensus gate | Strict Pydantic validation + auto-reject on schema violation (already in v1 Phase 3) |
| Trust weights drift wrong (a good agent gets falsely down-weighted from one bad batch) | Legitimate facts silently suppressed | Decay trust adjustments gradually, log every weight change for manual audit |
| Lineage graph depth grows unbounded (summary-of-a-summary-of-a-summary) | Revocation traversal cost grows, audit trail gets murky | Cap derivation depth or periodically "flatten" lineage by re-deriving from primary sources |
| Permission scope and trust weight get conflated in practice | A trusted agent's output effectively bypasses scope, or vice versa | Keep them as strictly independent edge properties, enforce both gates unconditionally in the read path, never short-circuit one based on the other |

---

## 10. One-line pitch

A shared memory graph where every fact knows who said it, whether it's still true, and who's
allowed to see it — and losing any one of those three propagates automatically to everything
derived from it.

---

## 11. Tech Stack

### Backend / data layer

| Component | Pick | Why |
|---|---|---|
| Graph DB | **FalkorDB** | Redis-module, single process, no JVM GC pauses. GraphBLAS sparse-matrix ops beat Neo4j on throughput at this project's scale. Speaks Cypher — schema in Sections 3–5 carries over unchanged. |
| Vector index | **FalkorDB's built-in HNSW** | Native vector search — embeddings and graph live in one store, no separate vector DB to keep in sync. |
| Permission bitmap store | **Same Redis instance FalkorDB runs on** | Redis bitmaps / sorted sets (by `unlock_at`) give O(1) permission checks with zero extra process or network hop. |
| SLM extraction | **Ollama — Qwen2.5/3 3B or Llama 3.2 3B, quantized (Q4_K_M)** | Both run on consumer hardware with decent structured-output compliance via Instructor. To combat KV-cache inference bottlenecks, strictly prioritize architectures utilizing **Grouped-Query Attention (GQA)** (like Llama 3) or **Multi-Head Latent Attention (MLA)** to drastically reduce VRAM overhead during long-context memory extraction. |
| Lineage Auditing | **State-Space Model (e.g., Mamba / Jamba)** | (Optional) Standard Transformers hit a quadratic memory wall on long sequences. If an agent needs to audit or summarize an unbounded chain of `derived_from` lineage edges, using an SSM provides linear time and constant memory, preventing Out-Of-Memory (OOM) crashes. |
| Structured extraction | **Instructor + Pydantic** | Unchanged from v1. |
| Retrieval orchestration | **LightRAG**, wired directly to FalkorDB rather than its default backend | Keeps dual-mode (local/global) retrieval without inheriting a Neo4j assumption. |
| Backend API | **FastAPI** | Async-native — both write path (extraction → consensus gate → Cypher write) and read path (permission check → retrieval) should be non-blocking. |
| Audit log | **SQLite, hash-chained** (`sha256(prev_hash + entry)`) | Tamper-evidence without ledger/blockchain overhead — proportionate to Section 7's non-goal on regulatory-grade logging. |

**Efficiency notes**
- Batch Cypher writes — FalkorDB's throughput edge over Neo4j is largest at batch sizes ~1000–5000, not single-row writes. Buffer extraction output per turn, flush in batches.
- Quantize the SLM aggressively (Q4) — the consensus gate is the correctness backstop, not the extraction model, so latency matters more than extraction precision here.
- Never embed a node before it clears the permission gate (open decision #4) — saves compute and keeps the vector store free of unscoped restricted content.

### Interface — build order, not a single choice

The UI is not load-bearing for what makes this idea unique (Section 1's thesis is entirely backend logic). Interface work is staged by actual need, cheapest-first:

1. **CLI first.** Validates write path, consensus gate, and retrieval actually work. No framework needed beyond `argparse`/`click`.
2. **FalkorDB's built-in browser** for whole-graph debugging during Phase 1–3 build-out — free, zero build cost, ships with the database.
3. **TUI for day-to-day use and lineage debugging** — this is the interface actually worth building deliberately:
   - **Textual** as the app shell (widgets, layout, keybindings, reactive updates).
   - **Rich `Tree`** for `derived_from` lineage chains — a near-exact fit, since lineage is structurally a tree, not a free-form graph.
   - **Rich `Table`** for a facts list view (`trust_weight`, `valid_from/to`, `permission_scope` as columns).
   - Covers the two debugging questions that actually matter — "did this revocation cascade correctly" and "what does this agent currently believe" — without touching general graph layout.
4. **React Flow, GUI, only if/when a demo is needed.** Force-directed graph rendering has no good terminal equivalent and isn't needed for debugging — it's a legibility tool for showing the idea to someone who won't read Cypher or a tree view. Scope any such view to a bounded subgraph (one lineage chain, one entity neighborhood, one time window) — rendering the full graph past a few hundred nodes is unreadable in any renderer, terminal or GUI.

If this project styling ever extends to a GUI: shadcn/ui + Radix + Tailwind, thin font weights (300–400), soft-tint low-saturation fills instead of heavy borders — consistent with existing visual preferences, deferred until step 4 is actually needed.

---

## 12. Agent Access Model

Agents never touch FalkorDB directly. All access goes through the MCP server (Section 9's
architecture), which is the only component allowed to issue Cypher. This section defines how
an individual agent's access is declared and enforced.

### Scope taxonomy

Flat tags, no implied hierarchy or ordering — a scope is a category of information, not a
sensitivity rank:

```
STYLE | PROFESSIONAL | PERSONAL | RESEARCH | LEADERSHIP
```

Each fact/edge carries one or more of these in `permission_scope` (Section 3 schema). An
agent's access is a *set* of tags it may intersect against, not a threshold on a single axis —
this avoids bundling unrelated categories together just because one was assumed "above" another.

### `agents.yaml` — single source of truth

Git-tracked, default-deny. A new agent with no entry sees nothing.

```yaml
agents:
  - id: writing-analyzer
    allowed_scopes: [STYLE]
    trust_weight_prior: 0.7
    registered_at: 2026-08-07

  - id: resume-drafter
    allowed_scopes: [STYLE, PROFESSIONAL]
    trust_weight_prior: 0.6
    registered_at: 2026-08-07

  - id: personal-assistant
    allowed_scopes: [STYLE, PROFESSIONAL, PERSONAL]
    trust_weight_prior: 0.8
    registered_at: 2026-08-07
    grant_expires_at: null   # set only for time-boxed agents

  - id: malware-detector
    allowed_scopes: [RESEARCH]
    trust_weight_prior: 0.9
    registered_at: 2026-08-07
```

### Enforcement path

1. Agent authenticates once per session → server resolves its row in `agents.yaml`. Scope is
   always looked up server-side, never accepted from the agent's own claim.
2. Registry loads into the Redis permission bitmap (Section 11) on startup/change. The query-time
   check is `agent.allowed_scopes ∩ edge.permission_scope ≠ ∅` — O(1) against the bitmap.
3. No registry entry → `allowed_scopes: []` → agent sees nothing. Fails closed, not open.
4. `grant_expires_at`, if set, is checked alongside the scope intersection. An expired grant
   behaves identically to no grant. This is independent of a fact's own `unlock_at`
   (Section 5 bonus feature) — one governs when an *agent's* access starts/ends, the other
   governs when a *fact* becomes visible to anyone with the right scope. Don't conflate them.

### Change process

Static file, hand-edited, redeploy or hot-reload the bitmap. Git history is the audit trail —
who changed whose access, when. Sufficient at current scale; revisit an admin API only once
editing YAML by hand becomes the actual bottleneck, not before.

---

## 13. Storage & Export

FalkorDB remains the live, queryable store — no change to Section 11. This section separates
that from portable/export formats, which are a distinct concern.

| Purpose | Format | Why |
|---|---|---|
| Live persistence (crash recovery) | **FalkorDB RDB snapshots** (built-in) | Fast, native, not meant to be read by other tools. Backup, not export. |
| Full portable export | **Cypher script dump** (`CREATE` statements, one file) | Round-trips into FalkorDB, Neo4j, or Memgraph. Human-readable, diffable in git — closest thing to a source-of-truth text file for the whole graph. Default export format. |
| Interop with external tools | **GraphML** | Node/edge attributes (`trust_weight`, `permission_scope`, `valid_from`, etc.) map directly to GraphML attribute keys. Opens in Gephi, yEd, NetworkX. |
| Structured data interchange | **JSON** (`{nodes: [...], edges: [...]}`) | For scripts, notebooks, or non-graph-native services consuming the graph programmatically. |

**Export command:** `export --format={cypher,graphml,json}` walks the graph and writes the
requested format. Cypher is default — the only format that round-trips without lossy translation
of custom edge properties.

**Scope-filtered export:** export should accept an optional `--as-agent <agent_id>` flag,
applying the same `allowed_scopes ∩ permission_scope` intersection used at query time (Section 12)
before writing output. This is nearly free to build — same filter, applied at export time instead
of query time — and lets a `STYLE`-only export be produced without ever touching `PERSONAL` facts.
Build this in from the start rather than adding it once an unscoped export has already leaked
something it shouldn't have.

---

## 14. Custom Export Format — `.qgf` (Quorum Graph Format)

A purpose-built columnar binary format, layered on top of Section 13's export tooling — not a
replacement for FalkorDB as the live store (see the explicit non-goal below). Scoped as a
self-contained subproject: own spec, own reader/writer, benchmarked against the Cypher/GraphML/
JSON exporters already defined.

**Rationale:** most edge properties in this schema are low-cardinality (`permission_scope`,
`source_agent_id`) — dictionary encoding those columns alone should beat JSON/GraphML
substantially on size, and more on load time, since nothing needs re-parsing per field. The
`derived_from` lineage — a variable-length list per edge — is the one structure JSON/GraphML
represent inefficiently; a CSR-style offset table handles it the way real graph engines do
internally.

### File layout

```
┌─────────────────────────────────────┐
│ HEADER (fixed 64 bytes)              │
│  magic: "QGF1"                       │
│  version: u16                        │
│  node_count: u64                     │
│  edge_count: u64                     │
│  offset table (7x u64)               │
├─────────────────────────────────────┤
│ STRING TABLE                         │
│  dictionary-encoded strings:         │
│  entity names, agent_ids, scopes     │
│  → indices used everywhere below     │
├─────────────────────────────────────┤
│ NODE TABLE (columnar)                │
│  node_type[]   : u8  (enum-encoded)  │
│  name_ref[]    : u32 (→ string table)│
├─────────────────────────────────────┤
│ EDGE TABLE (columnar)                │
│  source_id[]        : u32            │
│  target_id[]        : u32            │
│  relation_type[]     : u8            │
│  source_agent_ref[]  : u16 (→ dict)  │
│  trust_weight[]      : f32           │
│  valid_from[]        : i64 (epoch)   │
│  valid_to[]           : i64 (0=null) │
│  permission_scope[]   : u8 (bitmask) │
│  unlock_at[]           : i64         │
│  derived_from_count[] : u16          │
├─────────────────────────────────────┤
│ LINEAGE TABLE (variable-length)      │
│  flattened derived_from edge-id refs,│
│  indexed by offsets from edge table  │
├─────────────────────────────────────┤
│ FOOTER: CRC32 checksum               │
└─────────────────────────────────────┘
```

### Key design decisions

- **`permission_scope` as a bitmask, not a string.** Five scopes (Section 12) fit a `u8`:
  `STYLE=0x01, PROFESSIONAL=0x02, PERSONAL=0x04, RESEARCH=0x08, LEADERSHIP=0x10`. The
  `agent.allowed_scopes ∩ edge.permission_scope` check becomes one bitwise AND — evaluable
  directly off this format without reinflating into FalkorDB.
- **`source_agent_id` dictionary-encoded**, not a string per edge — a dozen distinct agents
  against potentially thousands of edges is most of the size win over JSON on its own.
- **Lineage as flattened, offset-indexed arrays**, not nested objects — the one structural piece
  GraphML/JSON can't represent efficiently.
- **CRC32 footer** — this is an export snapshot, not a live store; treat it as untrusted on read,
  unlike FalkorDB's own crash-consistency guarantees.

### Reader/writer scope

- **Writer:** walks FalkorDB via Cypher (same query path as the Cypher/GraphML exporters),
  builds dictionary tables, writes columnar arrays. Python with `struct`/`array` is sufficient
  to start — no need for a compiled implementation unless benchmarks show it's a bottleneck.
- **Reader:** memory-maps the file, decodes columns lazily. Filtering on the `permission_scope`
  column before touching the lineage table means a scoped read doesn't require materializing the
  full graph — filtered reads without full deserialization, which JSON/GraphML can't offer
  without a full parse first.

### Explicit non-goal

`.qgf` is an export/interchange format, not a replacement for FalkorDB as the live, queryable
store. It has no query language, no concurrent-write handling, and no durability guarantees
beyond the CRC check. Building a full custom storage-and-query engine to replace FalkorDB is a
separate, multi-month project (its own storage layer, indexing, query executor, and concurrency
model) and is out of scope here — it would compete directly with the time better spent on
Sections 2–8, which is where this project's actual differentiation lives.

### What to benchmark once built

Size and load time against the Cypher/GraphML/JSON exporters, on the same graph snapshot. A
concrete number ("Nx smaller, Nx faster to filter-load than JSON at N nodes") is the actual
deliverable — more defensible than the format's existence alone.