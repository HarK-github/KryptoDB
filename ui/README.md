# KryptoDB Web Console & WebGL Graph Visualizer

The frontend for **KryptoDB**: an interactive, WebGL-accelerated operational console and real-time knowledge graph visualizer built with **Next.js 16 (Turbopack)**, **React 19**, and **Sigma.js**.

---

## Features

* **WebGL Knowledge Graph Explorer:** Hardware-accelerated graph rendering using Sigma.js and Graphology with ForceAtlas2 physics layouts. Visualizes active `:FACT` relationships, temporal validity, and node clusters.
* **Multi-Agent Chat & HITL Approvals:** Real-time chat interface connected to the KryptoDB LangGraph agent runtime. Renders Human-in-the-Loop (HITL) approval cards for sensitive scope proposals (`CONFIDENTIAL`, `RESTRICTED`, `PII`).
* **Interactive Playground:** Debug queries, execute hybrid search (Dense Vector + BM25 Lexical + 2-Hop Graph Traversal), inspect provenance paths, and manage agent API keys.
* **Audit Trail Viewer:** Real-time inspection of the tamper-evident SHA-256 hash-chained audit ledger (`data/audit.db`).

---

## Getting Started

### Prerequisites

* **Node.js**: v20.x or higher
* **npm** or **pnpm**
* **KryptoDB Daemon**: Running locally on port `8000` (default)

### 1. Installation

```bash
cd ui
npm install
```

### 2. Configuration

Create or edit `.env.local` if running outside the default localhost ports:

```bash
# URL of the KryptoDB FastAPI daemon (default: http://localhost:8000)
NEXT_PUBLIC_KRYPTODB_URL=http://localhost:8000
```

> [!NOTE]
> In production and Docker standalone mode, `NEXT_PUBLIC_KRYPTODB_URL` can be left empty (`""`), as Next.js rewrites `/api/*` and WebSocket traffic directly to the backend.

### 3. Running in Development

```bash
npm run dev
```

Open [http://localhost:3000](http://localhost:3000) in your browser.

---

## Authentication & API Keys

KryptoDB enforces fail-closed authentication. To connect the UI to your local database:

1. Copy the admin key generated during backend setup (`KRYPTODB_ADMIN_API_KEY`).
2. Open the console at [http://localhost:3000](http://localhost:3000).
3. Click the **Connect / Settings** icon in the top-right corner.
4. Paste your key into the API Key input. It will be stored in your browser's `localStorage` (`kryptodb_api_key`) and automatically sent as `Authorization: Bearer <KEY>` on all REST and WebSocket requests.
