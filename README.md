# Elidia CLI

Universal AI Agent for your terminal. Write code, search the web, manage files, run research — all from the command line.

## Features

- **Multi-model chat** — 30+ models via AiUtils Developer API (Claude, GPT, Gemini, DeepSeek, Llama, Qwen)
- **Agent loop** — route → act → observe → reflect with automatic tool calling
- **Built-in tools** — file system, git, terminal, web search, HTTP fetch
- **MCP integration** — connect any MCP server for extended tool access
- **Memory & RAG** — persistent memory with vector search (sqlite-vec, 1024-dim)
- **Mode classification** — automatic routing to Direct, Deep Think, Consensus, or Autonomous modes
- **Research pipeline** — 5-stage YOYO pipeline: Decompose → Search → Analyze → Synthesize → Verify
- **Creative generation** — image, video, audio, music via API or local models
- **Workflow engine** — YAML-defined pipelines with conditions, loops, and parallelism
- **Budget governance** — session cost limits, pre-execution estimation, auto-downgrade
- **Configurable themes** — dark, light, ocean, sunset, minimal, or custom
- **4-tier permissions** — AUTO → SESSION → EVERY_TIME → NEVER with progressive trust

## Install

```bash
pip install elidia-cli
```

## Quick Start

```bash
# Authenticate
elidia auth login

# Interactive chat
elidia chat

# One-shot query
elidia chat "Explain the difference between async and threading in Python"

# Specify a model
elidia chat --model claude-sonnet-5 "Review this code"

# Research mode
elidia chat --mode research "Compare React vs Svelte for enterprise apps"
```

## REPL Commands

| Command | Description |
|---------|-------------|
| `/model [name]` | Switch model or show current |
| `/mode [mode]` | Switch mode (chat, code, research, think, create) |
| `/think [level]` | Set thinking level (minimal, low, medium, high, max) |
| `/budget` | Show session cost summary |
| `/research <query>` | Run deep research pipeline |
| `/create image\|video\|speech\|music <prompt>` | Generate creative content |
| `/workflow <path.yaml>` | Execute a YAML workflow |
| `/daemon status\|start\|stop` | Manage background tasks |
| `/tools [category]` | List available tools |
| `/mcp` | Show MCP server status |
| `/persona [name]` | Switch agent persona |
| `/memory search\|save\|forget` | Manage persistent memory |
| `/history [query]` | Search chat history |
| `/balance` | Check DT balance |
| `/cost` | Session cost breakdown |
| `/new` | Start new session |
| `/clear` | Clear conversation |
| `/help [command]` | Show help |

## Configuration

Configuration lives in `~/.elidia/config.toml`:

```toml
[api]
base_url = "https://developer.aiutils.io/v1"
timeout_seconds = 120

[models]
code = "deepseek-chat"
reasoning = "deepseek-reasoner"
creative = "auto"
cheap = "deepseek-chat"

[permissions]
default_level = "session"

[theme]
name = "default"
```

## Local Models

Elidia supports local models via Ollama for offline/free usage:

```bash
# Install optional local dependencies
pip install elidia-cli[local]

# Use a local model
elidia chat --model ollama:llama3.2
```

## Development

```bash
# Clone
git clone https://github.com/aiutils/elidia-cli
cd elidia-cli

# Install in dev mode
pip install -e ".[dev]"

# Run tests
pytest

# Lint
ruff check elidia/
```

## Architecture

```
elidia/
├── agent/       # Agent loop, personas, portal bridge
├── api/         # AiUtils API client (httpx, SSE)
├── auth/        # Keychain-based authentication
├── cache/       # LRU response cache
├── cli/         # Click commands, REPL, renderer, themes, pager, progress
├── config/      # Settings, defaults, project rules
├── creative/    # Image, video, audio generation + terminal display
├── daemon/      # Background file watchers, schedules, webhooks
├── db/          # SQLite database
├── mcp/         # MCP client, registry, types
├── memory/      # Persistent memory (auto, compaction, embeddings)
├── models/      # Model router, adaptive selection
├── modes/       # Mode classifier, thinking levels, budget, consensus, deep think
├── permissions/ # Permission manager, audit, trust engine
├── rag/         # RAG engine, chunker, ingest, watcher
├── research/    # YOYO research orchestrator, sources, export
├── session/     # Session manager, history search
├── tools/       # Built-in tools (filesystem, git, terminal, search, fetch)
├── widgets/     # Widget protocol + CLI renderer
└── workflow/    # YAML workflow engine
```

## License

MIT
