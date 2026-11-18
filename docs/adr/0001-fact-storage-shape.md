# ADR 0001: Fact Storage Shape & Relationship Vector Indexing

## Status
Accepted

## Context
KryptoDB stores semantic graph facts connecting entities: `(s:Entity)-[:FACT]->(t:Entity)`. In GraphRAG, facts require dense vector embeddings (384-dimensional vectors from `all-MiniLM-L6-v2`) to enable hybrid vector + BM25 search over factual statements.

Earlier versions of Neo4j only supported vector indexing on Node properties (`FOR (n:Label) ON (n.embedding)`). Recent versions of Neo4j (5.18+) introduced support for Relationship Vector Indexes (`FOR ()-[r:TYPE]-() ON (r.embedding)`).

If relationship vector indexes were not supported, KryptoDB would be forced to adopt a **reified fact design**:
`(:Entity)-[:SUBJECT_OF]->(:Fact)-[:OBJECT_IS]->(:Entity)` where the `:Fact` node holds the embedding and metadata.

## Decision
We executed `scripts/check_neo4j_capabilities.py` against the running Neo4j instance:
- **Version:** `5.21.2`
- **Edition:** `community`
- **Node Vector Index (`FOR (n:Entity) ON (n.embedding)`):** Supported (`true`)
- **Relationship Vector Index (`FOR ()-[r:FACT]-() ON (r.embedding)`):** Supported (`true`)

Because Neo4j 5.21.2 Community natively supports relationship vector indexing, we **keep the direct labeled relationship model**:
`(:Entity)-[:FACT {id, predicate, statement, embedding, confidence, status, ...}]->(:Entity)`

### Architectural Benefits:
1. **Zero Graph Expansion Overhead:** Avoids creating an extra intermediate `:Fact` node and two edges for every single fact, cutting graph size and traversal hop count by 50%.
2. **True 2-Hop Traversal:** Direct 2-hop traversals `(A)-[r1:FACT]->(B)-[r2:FACT]->(C)` map directly to two relationship hops, rather than four hops in a reified model.
3. **Encapsulation behind `MemoryStore` Port:** The storage implementation remains encapsulated behind the `MemoryStore` port interface, allowing seamless fallback to a reified node model if deployed against a legacy Neo4j version (<5.18).

## Consequences
- Schema defines `fact_vector_index FOR ()-[r:FACT]-() ON (r.embedding)`.
- Reified fact nodes are not required for Neo4j 5.21+.
