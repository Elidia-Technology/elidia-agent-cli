# Elidia CLI

**Terminal AI agent with multi-model orchestration, tool execution, and autonomous workflows.**

Elidia is a standalone CLI that connects to 30+ AI models through the AiUtils Developer API. It goes beyond simple chat — it reasons about which model to use, executes tools, runs multi-step research, generates creative media, and orchestrates autonomous agent workflows.

## Key Features

- **Multi-model routing** — automatic model selection based on task complexity, or pin a specific model
- **Tool execution** — built-in tools (file I/O, shell, web) plus MCP server integration
- **Thinking levels** — control reasoning depth from minimal (fast) to max (deep multi-step)
- **Consensus mode** — query multiple models and synthesize agreement
- **Deep think** — streaming chain-of-thought with reasoning models
- **Research** — multi-source research with citations and export
- **Creative** — generate images, video, audio, and music
- **Workflows** — YAML-defined multi-step pipelines with conditions and parallelism
- **Autonomous mode** — self-directed task decomposition and execution
- **Swarm** — parallel multi-agent coordination
- **Daemon** — background watchers, schedules, and webhooks
- **Themes** — 6 built-in color schemes plus custom TOML themes
- **Response cache** — LRU cache with TTL for repeated queries
- **Session persistence** — SQLite-backed conversation history with search

## Quick Install

```bash
pip install elidia-cli
```

Then authenticate:

```bash
elidia auth login
```

## Architecture

Elidia is organized into 21 packages with 60+ modules:

```
elidia/
├── agent/       # Core agent loop, personas, tool executor
├── api/         # AiUtils API client (SSE + JSON, HTTP/2)
├── auth/        # API key management
├── cache/       # Response cache (LRU + TTL)
├── cli/         # REPL, commands, pager, progress, themes
├── config/      # Settings, defaults, project rules
├── creative/    # Image, video, audio generation
├── daemon/      # Background task management
├── db/          # SQLite database layer
├── mcp/         # Model Context Protocol integration
├── memory/      # Tiered memory (working → long-term)
├── models/      # Model registry and routing
├── modes/       # Thinking, budget, consensus, deep think, autonomous, swarm
├── permissions/ # Permission manager, trust engine, audit
├── research/    # Multi-source research orchestrator
├── session/     # Session management and history
├── tools/       # Built-in tool registry
├── widgets/     # Interactive widget protocol
└── workflow/    # YAML workflow engine
```
