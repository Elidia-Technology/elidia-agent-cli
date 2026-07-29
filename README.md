<p align="center">
  <h1 align="center">Elidia Agent CLI</h1>
  <p align="center"><strong>Universal AI Agent for your terminal</strong></p>
  <p align="center">
    Code, research, create, orchestrate — with 50 LLMs, 36+ tools, and 5 execution modes.
  </p>
  <p align="center">
    <a href="https://pypi.org/project/elidia-agent-cli/"><img src="https://img.shields.io/pypi/v/elidia-agent-cli?color=blue" alt="PyPI"></a>
    <a href="https://pypi.org/project/elidia-agent-cli/"><img src="https://img.shields.io/pypi/pyversions/elidia-agent-cli" alt="Python"></a>
    <a href="LICENSE"><img src="https://img.shields.io/badge/license-Proprietary-red" alt="License"></a>
  </p>
</p>

---

## What is Elidia Agent CLI?

Elidia Agent CLI is a powerful terminal-based AI agent connecting you to **50 language models** through a single interface. It goes beyond chat — execute tools, run multi-model consensus, conduct deep research, generate images/video/audio, orchestrate complex workflows, and spawn parallel sub-agents (SWARM mode) — all from your terminal.

Powered by the [AiUtils Developer API](https://developer.aiutils.io), Elidia Agent CLI gives you access to models from OpenAI, Anthropic, Google, DeepSeek, Meta, Alibaba, and more — with a single API key.

## Key Features

### 🎨 Hermes-Style Terminal UI *(v0.4.2)*
- **ASCII art logo** — "ELIDIA AGENT" in ANSI Shadow at launch
- **Styled prompt bar** — model, mode, context usage, timing, and cost before each input
- **Rich welcome panel** — tools, MCP servers, execution modes, personas at a glance
- **Theme support** — 6 built-in themes (default, dark, light, ocean, sunset, minimal)

### 🤖 5 Execution Modes
- **DIRECT** — fast single-model answers for simple questions
- **CONSENSUS** — 2-3 models vote, synthesize the best response
- **HARNESS** — multi-step agent loop with planning, tool use, self-correction
- **DEEP** — extended reasoning with DeepEvaluator (8-dimension quality scoring)
- **SWARM** — parallel sub-agents with full tool access for complex tasks

### 🧠 Self-Learning System
- **OutcomeTracker** — records every task execution (model, success/failure, tokens, cost)
- **PatternLearner** — cross-session analysis for model optimization
- **KnowledgeGraph** — LLM-powered entity + relation extraction from conversations
- **Adaptive routing** — learned preferences influence model selection (30% weight)

### 📋 Project Orchestrator
- **SCRUM-style planning** — EPIC → Story → Ticket decomposition
- **SprintExecutor** — multi-agent parallel execution with dependency ordering
- **Iterative refinement** — 1-3 passes (small jobs) or 1-5 passes (large jobs)

### 🔧 Built-in Tools (36+)
- **File system** — read, write, search, manage files
- **File upload** — PDF, DOC/DOCX, PPT/PPTX, CSV, TXT, XLS/XLSX, images, code (max 10 files, 50MB each)
- **Git** — status, diff, log, commit
- **Terminal** — shell commands with permission controls
- **Web search** — multi-provider (Tavily, DuckDuckGo)
- **Browser** — Playwright-based web automation
- **Database** — read-only SQL with permission controls
- **Email** — SMTP send + IMAP search/read
- **Calendar** — local .ics read/write, conflict detection
- **MCP support** — connect any MCP-compatible server

### 🔬 Research Pipeline
- **5-stage pipeline** — Decompose → Search → Analyze → Synthesize → Verify
- **Cross-reference verification** — claims checked against search sources (not circular self-check)
- **Export** — Markdown or HTML reports with citations

### 🎨 Creative Generation
- **Images** — FLUX, DALL-E, Stable Diffusion (500+ models)
- **Video** — Kling, Minimax (500+ models)
- **Speech** — ElevenLabs, OpenAI TTS (100+ models)
- **Music** — Suno text-to-music

### 🧩 Memory & RAG
- **4-tier memory** — SYSTEM, USER, PROJECT, SESSION
- **AutoMemory** — detects corrections, preferences, confirmations
- **Vector search** — sqlite-vec with bge-m3 1024-dim embeddings
- **Auto-compaction** — summarizes sessions into persistent memory
- **Knowledge graph** — entities and relations extracted from conversations

### 🔐 Security & Permissions
- **4-tier system** — AUTO, SESSION, EVERY_TIME, NEVER
- **Progressive trust** — auto-promotes trusted actions
- **Audit logging** — every tool execution logged
- **Keychain storage** — API keys via OS keychain, never plaintext

---

## Installation

### From PyPI (recommended)

```bash
pip install elidia-agent-cli
elidia auth login          # enter your AiUtils API key
elidia ask "Hello!"        # send your first message
```

**If `elidia` command not found**, add pip's bin directory to PATH:

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

### Requirements

- Python 3.11 or higher
- An AiUtils Developer API key ([get one here](https://developer.aiutils.io))
- macOS 12+, Ubuntu 22.04+, or Windows 10+

---

## Quick Start

```bash
# 1. Authenticate
elidia auth login

# 2. One-shot message
elidia ask "Write a Python function to reverse a string"

# 3. Interactive REPL (Hermes-style UI)
elidia

# 4. With file context
elidia ask --file report.pdf "Summarize this document"

# 5. With image analysis
elidia ask --image photo.jpg "What's in this image?"

# 6. Research mode
elidia ask --mode research "Compare PostgreSQL vs MongoDB for time-series data"

# 7. Specific model
elidia ask --model claude-sonnet-4-6 "Review this code for bugs"
```

---

## All Commands

| Command | Description |
|---|---|
| `elidia` | Interactive REPL with Hermes-style UI and 29 slash commands |
| `elidia ask <msg>` | One-shot message (supports `--model`, `--mode`, `--file`, `--image`) |
| `elidia auth login/logout/status` | Manage API key |
| `elidia auth email-login/email-logout/email-status` | Email credentials |
| `elidia config show` | View configuration |
| `elidia config set <section.field> <value>` | Persist preferences |
| `elidia daemon start/stop/restart/status/init` | Background daemon |
| `elidia mcp list/health` | MCP server management |
| `elidia models` | List all 50 LLMs |
| `elidia models --local` | List local Ollama models |
| `elidia rag ingest/search/list/clear` | Local RAG document store |
| `elidia session list/new/delete` | Session management |
| `elidia version` | Show version |
| `elidia workflow run <file>` | Execute YAML workflow |

---

## REPL Commands (29 total)

| Command | Description |
|---|---|
| `/model [name]` | Switch model or show current |
| `/mode [mode]` | Switch mode (chat/code/research/think/create) |
| `/think [level]` | Set thinking depth (minimal/low/medium/high/max) |
| `/persona [slug]` | Switch domain persona (developer, doctor, lawyer, etc.) |
| `/research <query>` | 5-stage deep research pipeline |
| `/create image\|video\|speech\|music <prompt>` | AI media generation |
| `/workflow <file.yaml>` | Execute YAML workflow |
| `/tools [category]` | List available tools grouped by category |
| `/mcp [disconnect]` | MCP server status |
| `/image <path> [message]` | Attach image for vision analysis |
| `/rag ingest\|search\|list\|clear` | Manage RAG document store |
| `/memory search\|save\|forget\|list` | Persistent memory management |
| `/history [query]` | Search chat history |
| `/sessions` | List sessions |
| `/outcomes [best\|failures]` | Model performance rankings, failure patterns |
| `/analyze` | Full learning report (trends, preferences, routing hints) |
| `/knowledge [query]` | Search knowledge graph for entities and relations |
| `/balance` | Check DT balance |
| `/cost` | Session cost breakdown |
| `/budget` | Budget governor status |
| `/daemon status\|start\|stop` | Background daemon control |
| `/theme [name]` | Switch UI theme (default/dark/light/ocean/sunset/minimal) |
| `/cache [on\|off\|clear]` | Response cache control |
| `/pager [on\|off]` | Auto-pager for long responses |
| `/new` | Start new session (compacts current) |
| `/clear` | Clear conversation |
| `/rules` | Show project rules (.elidia/rules.md) |
| `/trust` | Show trust engine promotions |
| `/help [command]` | Show help |

---

## Available Models

### Language Models (LLMs) — 50 total
Claude (Opus, Sonnet, Haiku), GPT (4o, 4.5, 5.5, o3, o4-mini), Gemini (2.5 Pro/Flash), DeepSeek (V4, Reasoner), Llama (4 Scout/Maverick, 3.3 70B), Qwen (3, 2.5 72B), Mistral (Large, Codestral), and more across 6 vendors (OpenAI, Anthropic, DeepSeek, PIAPI/Elidia-1, Local/Ollama, Aiutils).

### Creative Models — available through `/create` and chat
| Category | Count | Examples |
|---|---|---|
| Image Generation | 500+ | FLUX, SDXL, DALL-E, Midjourney |
| Video Generation | 500+ | Runway, Pika, Kling, Sora |
| Audio/Music | 100+ | ElevenLabs, MusicGen, Whisper |
| 3D Generation | 50+ | TripoSR, Zero123++, Stable3D |

### Local Models — `elidia models --local`
Ollama models running on your machine. Zero API cost, works offline.
```bash
ollama pull llama3.2:3b
elidia models --local
```

---

## Configuration

`~/.elidia/config.toml`:

```toml
[api]
base_url = "https://developer.aiutils.io/v1"
timeout_seconds = 120

[models]
default = "claude-sonnet-4-6"
code = "claude-sonnet-4-6"
reasoning = "deepseek-reasoner"
creative = "auto"

[permissions]
default_level = "session"

[budget]
session_limit_dt = 50000.0

[theme]
name = "default"
```

---

## MCP Integration

Connect any MCP-compatible server:

```json
// ~/.elidia/mcp.json
{
  "mcpServers": {
    "github": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": { "GITHUB_TOKEN": "ghp_..." }
    },
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/dir"]
    }
  }
}
```

---

## Workflow Example

```yaml
# research.yaml
name: competitor-analysis
steps:
  - name: research
    type: llm
    prompt: "List top 5 AI coding assistants with key features"
    output: findings

  - name: analyze
    type: llm
    prompt: "Create a comparison table:\n{{findings}}"
    output: analysis

  - name: save
    type: shell
    command: "echo '{{analysis}}' > competitor-report.md"
```

```bash
elidia workflow run research.yaml
```

---

## Support

- **PyPI:** [pypi.org/project/elidia-agent-cli](https://pypi.org/project/elidia-agent-cli/)
- **GitHub:** [github.com/Elidia-Technology/elidia-agent-cli](https://github.com/Elidia-Technology/elidia-agent-cli)
- **Email:** support@aiutils.io

---

## License

Proprietary software. Copyright 2026 Elidia Technology Pvt Ltd. All Rights Reserved.
See [LICENSE](LICENSE) for full terms.
