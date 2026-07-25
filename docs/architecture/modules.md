# Module Map

Complete inventory of all 85 Python modules across 19 packages.

## Package Summary

| Package | Modules | Purpose |
|---------|---------|---------|
| `elidia/` | 2 | Root package, `__main__` entry |
| `elidia/agent/` | 4 | Agent loop, personas, portal bridge |
| `elidia/api/` | 3 | HTTP client, SSE streaming |
| `elidia/auth/` | 2 | API key management |
| `elidia/cache/` | 2 | LRU response cache |
| `elidia/cli/` | 7 | REPL, commands, pager, progress, themes, renderer |
| `elidia/config/` | 4 | Settings, defaults, project rules |
| `elidia/creative/` | 5 | Image, video, audio, local models, terminal display |
| `elidia/daemon/` | 2 | Background task manager |
| `elidia/db/` | 2 | SQLite database layer |
| `elidia/mcp/` | 5 | MCP client, config, registry, types |
| `elidia/memory/` | 7 | Store, auto-memory, compaction, embeddings, outcomes, patterns |
| `elidia/models/` | 3 | Router, adaptive selection |
| `elidia/modes/` | 8 | Classifier, thinking, budget, deep think, consensus, autonomous, swarm |
| `elidia/permissions/` | 4 | Permission manager, trust engine, audit logger |
| `elidia/rag/` | 6 | Chunker, engine, ingest, portal bridge, watcher |
| `elidia/research/` | 4 | Orchestrator, sources, export |
| `elidia/session/` | 3 | Session manager, history search |
| `elidia/tools/` | 7 | Registry, base, filesystem, terminal, fetch, search, git |
| `elidia/widgets/` | 3 | Widget protocol, renderer |
| `elidia/workflow/` | 2 | YAML workflow engine |

## Phase Breakdown

### Phase 0 — Foundation (11 modules)
Core infrastructure: CLI entry, API client, auth, config, database, session management.

### Phase 1 — Intelligence (25 modules)
Agent loop, tool execution, MCP integration, model routing, permissions, personas, RAG pipeline.

### Phase 2 — Advanced Modes (23 modules)
Thinking levels, budget governor, deep think, consensus, autonomous, swarm, memory system, creative media, research, daemon.

### Phase 3 — Workflows & Widgets (8 modules)
Workflow engine, widget protocol, widget renderer, plus REPL integration of all Phase 2 modules.

### Phase 4 — Polish & Distribution (7 modules)
Auto-pager, progress indicators, connection pooling, response cache, themes. Plus packaging, CI/CD, tests, and documentation.

## Module Detail

### `elidia/agent/loop.py`
Core agent loop. Receives messages, classifies execution mode, dispatches to direct/deep/consensus/autonomous paths, manages tool execution loop with permission checks.

### `elidia/api/client.py`
Async HTTP client using httpx with HTTP/2 and connection pooling. Supports both SSE streaming and JSON responses. Handles the AiUtils Developer API protocol.

### `elidia/modes/classifier.py`
LLM-based mode classifier. Analyzes user queries and routes to the appropriate execution mode (direct, deep, consensus, harness) based on complexity and nature.

### `elidia/modes/budget.py`
Budget governor. Tracks token usage and cost per session, enforces spending limits, suggests cheaper models when approaching limits.

### `elidia/cache/lru.py`
LRU response cache with TTL-based expiration. Uses SHA-256 key derivation from model + messages + temperature. Bounded OrderedDict implementation.

### `elidia/workflow/engine.py`
YAML workflow parser and executor. Supports LLM, shell, tool, parallel, and loop step types with variable substitution and conditional execution.
