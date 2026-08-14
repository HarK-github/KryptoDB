<div align="center">
  <h1>KryptoDB</h1>
  <p><b>K</b>nowledge <b>R</b>etrieval & <b>Y</b>ielding <b>P</b>rotocol for <b>T</b>rustless <b>O</b>perations <b>D</b>ata<b>B</b>ase</p>
  <p><i>Crypto-Enforced, Multi-Agent Memory Access Control on Neo4j</i></p>
  
  <p>
    <img src="https://img.shields.io/badge/Python-3.10%2B-blue" alt="Python 3.10+">
    <img src="https://img.shields.io/badge/Neo4j-5.x-blue" alt="Neo4j">
    <img src="https://img.shields.io/badge/Security-AES--256--GCM-black" alt="AES-256-GCM">
    <img src="https://img.shields.io/badge/Protocol-MCP-green" alt="MCP">
  </p>
</div>

KryptoDB is an advanced, graph-based memory server designed specifically for multi-agent environments. Every new knowledge item is encrypted with its own AES-256-GCM data-encryption key, while API-key identity, scope authorization, and session activation determine whether that item can be searched or decrypted.

It exposes its memory substrate via the **Model Context Protocol (MCP)**, allowing AI agents to read, write, and manage knowledge securely.

---

## ⬡ System Architecture

![System Architecture](structure.png)

---

## ❖ Key Features

### 1. Per-Knowledge Envelope Encryption
- **Unique DEK:** Every new fact gets a fresh random AES-256-GCM data-encryption key.
- **Wrapped DEK:** A stable scope key wraps only the per-knowledge DEK; agents never receive DEKs or KEKs.
- **Complete envelope:** Subject, predicate, object, description, source identity, tags, and sensitive fact metadata are encrypted before persistence.
- **Authenticated data:** The envelope is bound to its knowledge ID, scope, and schema version.

### 2. API-Key and Session Access Control
- **Identity-bound API keys:** Bearer API keys resolve the agent identity server-side; client-supplied `AGENT_ID` is not an authorization source.
- **Scope intersection:** Effective scopes are the intersection of the agent configuration and the individual API key's scope limit.
- **Session activation gate:** A partition key must be active in the authenticated session before search tokens or plaintext can be produced.
- **Revocation:** API-key revocation and agent-scope changes immediately invalidate associated sessions and decrypted search state.
- **Admin boundary:** Key management, onboarding, agent configuration, and ontology access require an admin API key.

### 3. Searchable Encryption
- **HMAC indexes:** Subject, predicate, object, and token indexes are keyed HMAC values, not plaintext fields.
- **Search-before-decrypt:** Neo4j returns candidates by encrypted indexes; the daemon decrypts only authorized final candidates.
- **Body-based MCP queries:** MCP search uses `POST /memory/{agent_id}/query`, avoiding plaintext terms in URLs.
- **Supported search:** Exact and token search are supported. Confidential substring, fuzzy, ranked, and semantic search are intentionally out of scope.
- **Known leakage:** Equality/frequency, graph topology, and access patterns may remain observable.

### 4. Background Revocation Cascades
When a primary fact is superseded by a newer, more trustworthy fact, KryptoDB initiates a recursive revocation cascade along `derived_from` edges. 
- Deep cascades are offloaded to asynchronous FastAPI `BackgroundTasks`.
- Status endpoints (`/status/{task_id}`) allow agents to asynchronously poll the completion state, protecting the database from write-amplification deadlock storms.

### 5. Ephemeral Global Clustering
For global summarization (e.g., "what are the main themes of this project?"), we dynamically project a temporary graph in Neo4j RAM, run the Louvain community detection algorithm via GDS, cache the insights, and instantly destroy the projection to protect memory constraints.

### 6. Controlled Onboarding and Migration
- Onboarding is admin-only; ordinary agents cannot grant themselves scopes.
- `migrate_legacy_tool` converts old partition-encrypted facts into per-knowledge envelopes for active authorized scopes.
- Knowledge-level reclassification regenerates the envelope and search indexes under the target scope.

---

## ❯ Getting Started

### Prerequisites
- Python 3.10+
- Neo4j 5.x (with APOC and Graph Data Science plugins enabled)
- Redis 7.x
- SQLite (built-in)

### Installation
1. Clone the repository and install dependencies:
```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```
2. Configure your environment variables in `.env`:
```env
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=password
REDIS_URL=redis://localhost:6379/0
SYNAPSE_ENV=development
SYNAPSE_MASTER_KEY=<64-hex-character-production-key>
SYNAPSE_ADMIN_API_KEY=<admin-key>
# Optional customer-managed scope keys (use a mounted secret file in production)
SYNAPSE_SCOPE_KEYS_JSON={"diagnosis":{"key_id":"diagnosis:v1","key":"<base64-32-byte-key>"},"patients":{"key_id":"patients:v1","key":"<base64-32-byte-key>"}}
# Or: SYNAPSE_SCOPE_KEYS_FILE=/run/secrets/synapse-scope-keys.json
```

### Running the Server
Launch the backend Daemon (which handles cryptographic state, connections, and background workers):
```bash
python -m synapse.daemon.server
```

Run the MCP interface for your agents. Each MCP process must receive a real API key:
```bash
# Example with Claude Desktop or similar MCP clients
mcp run synapse/daemon/mcp_server.py
```

## 🔌 Connecting the MCP Server

KryptoDB implements the Model Context Protocol (MCP) to seamlessly provide zero-trust memory to modern AI tools.

### Claude Desktop Integration

1. Ensure your backend storage is running via Docker:
```bash
docker-compose up -d
```
2. Open your Claude Desktop configuration file:
   - **Mac**: `~/Library/Application Support/Claude/claude_desktop_config.json`
   - **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`
3. Build the KryptoDB Docker image (if you haven't already):
```bash
docker build -t kryptodb-mcp .
```
4. Add the KryptoDB server configuration, ensuring it uses the Docker runtime and maps to the host network so it can talk to Neo4j and Redis:
```json
{
  "mcpServers": {
    "kryptodb": {
      "command": "docker",
      "args": [
        "run",
        "-i",
        "--rm",
        "--network",
        "host",
        "-e", "NEO4J_URI=bolt://localhost:7687",
        "-e", "NEO4J_USER=neo4j",
        "-e", "NEO4J_PASSWORD=password",
        "-e", "REDIS_URL=redis://localhost:6380/0",
        "-e", "AGENT_ID=Agent-1",
        "-e", "SYNAPSE_API_KEY=syn_live_<key>",
        "kryptodb-mcp"
      ]
    }
  }
}
```

5. Restart Claude Desktop. You will now see the KryptoDB tools (like 🔨 `query_memory_tool`) available!

### Cursor IDE Integration

1. Build the Docker image if you haven't yet: `docker build -t kryptodb-mcp .`
2. Open Cursor Settings -> Features -> MCP Servers.
3. Click **+ Add New MCP Server**.
4. Name it `KryptoDB`.
5. Set the Type to `command`.
6. Set the Command to: `docker run -i --rm --network host -e NEO4J_URI=bolt://localhost:7687 -e NEO4J_USER=neo4j -e NEO4J_PASSWORD=password -e REDIS_URL=redis://localhost:6380/0 -e AGENT_ID=Agent-1 -e SYNAPSE_API_KEY=syn_live_<key> kryptodb-mcp`.
7. Save and refresh. Activate a permitted partition with `session_activate_tool` before querying.

---

## ⚙ MCP Tools Exposed

KryptoDB exposes the following tools to the LLM agent via the Model Context Protocol:

| Tool Name | Description |
|-----------|-------------|
| `propose_fact_tool` | Propose a new encrypted fact, optionally including a description. |
| `query_memory_tool` | Search authorized knowledge using subject or encrypted exact/token indexes. |
| `session_activate_tool` | Activate permitted partition keys for the current MCP session. |
| `session_show_tool` | Inspect currently active partitions for the current session. |
| `migrate_legacy_tool` | Convert authorized legacy facts into per-knowledge envelopes. |
| `reclassify_tool` | Re-encrypt a knowledge item under a different active scope. |
| `run_onboard_tool` | Admin-only schema onboarding from sample facts. |
| `task_status_tool` | Poll the status of background graph mutations (cascades). |

### API-Key Setup

Create an agent API key through the authenticated admin endpoint:

```bash
curl -X POST http://127.0.0.1:8000/admin/api-keys \
  -H "Authorization: Bearer $SYNAPSE_ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"agent_id":"Agent-1","scopes":["research"]}'
```

The raw API key is returned once. Store it as `SYNAPSE_API_KEY` in the MCP process. API keys are authentication credentials, not encryption keys.

### Legacy Migration

Activate the relevant legacy scopes first, then call `migrate_legacy_tool`. Migration decrypts and re-encrypts records inside the daemon, removes legacy relationship fields only after successful conversion, and returns counts rather than plaintext.

### Dataset Encryption Boundaries

When importing a new knowledge base, choose one of two modes:

- **New isolated scope/key:** configure a new customer scope key, such as `research:v1`, and import the dataset under that scope.
- **Existing scope/key:** attach the dataset to an existing scope, such as `diagnosis`. New items automatically use that scope's customer KEK and derived search key, while still receiving unique per-item DEKs.

The scope must be explicit. If its customer key is unavailable, writes and session activation fail closed; the server never stores the dataset unencrypted as a fallback. The frontend shows scope-key IDs, fingerprints, and availability, but never accepts or displays raw key material.

## Frontend

The Next.js frontend is an authenticated operational console for graph inspection,
agent access, ontology, audit, and MCP setup.

```bash
cd ui
npm install
NEXT_PUBLIC_SYNAPSE_URL=http://127.0.0.1:8000 npm run dev
```

Open `http://localhost:3000`, enter an API key in **Agents & Access**, and save it
in the browser. API requests use `Authorization: Bearer ...`; the live graph uses
the same credential through a negotiated WebSocket subprotocol. The UI never
receives or stores encryption keys and does not display plaintext until the
backend has authorized and decrypted a knowledge envelope.

---

## ✦ Contributors

Made with ❤️ by **Harshit Kandpal** - *Creator & Lead Developer*

Contributions, issues, and feature requests are welcome! Feel free to check the issues page or consult standard open-source contribution guides to get started.

---
*  If you found this helpful, consider giving it a star on GitHub!*
