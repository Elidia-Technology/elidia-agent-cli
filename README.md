<p align="center">
  <h1 align="center">Elidia Agent CLI</h1>
  <p align="center"><strong>Universal AI Agent for your terminal</strong></p>
  <p align="center">
    Write code, search the web, manage files, run research, generate creative content — all from the command line.
  </p>
  <p align="center">
    <a href="https://pypi.org/project/elidia-agent-cli/"><img src="https://img.shields.io/pypi/v/elidia-agent-cli?color=blue" alt="PyPI"></a>
    <a href="https://pypi.org/project/elidia-agent-cli/"><img src="https://img.shields.io/pypi/pyversions/elidia-agent-cli" alt="Python"></a>
    <a href="LICENSE"><img src="https://img.shields.io/badge/license-Proprietary-red" alt="License"></a>
  </p>
</p>

---

## What is Elidia Agent CLI?

Elidia Agent CLI is a powerful terminal-based AI agent that connects you to **30+ language models** through a single interface. It goes beyond simple chat — it can execute tools, run multi-model consensus, conduct deep research, generate images/video/audio, and orchestrate complex workflows — all from your terminal.

Powered by the [AiUtils Developer API](https://developer.aiutils.io), Elidia Agent CLI gives you access to models from OpenAI, Anthropic, Google, DeepSeek, Meta, Alibaba, and more — with a single API key.

## Key Features

### Multi-Model Intelligence
- **30+ models** — Claude, GPT, Gemini, DeepSeek, Llama, Qwen, Mistral, and more
- **Automatic model routing** — picks the best model for each task based on complexity and cost
- **Budget governance** — session cost limits, pre-execution estimation, auto-downgrade to cheaper models

### Smart Execution Modes
- **Direct** — fast single-model answers for simple questions
- **Deep Think** — extended reasoning with chain-of-thought for complex problems
- **Consensus** — runs 2-3 models in parallel, compares answers, synthesizes the best response
- **Autonomous** — multi-step agent loop with planning, tool use, and self-correction

### Built-in Tools
- **File system** — read, write, search, and manage files
- **Git** — status, diff, log, commit — full git workflow from chat
- **Terminal** — execute shell commands with permission controls
- **Web search** — search the web and fetch page content
- **MCP support** — connect any MCP-compatible server for extended capabilities

### Research Pipeline
- **5-stage YOYO pipeline** — Decompose > Search > Analyze > Synthesize > Verify
- **Multi-source** — web search, academic papers, documentation
- **Citation tracking** — every claim linked to its source
- **Export** — Markdown, JSON, or plain text reports

### Creative Generation
- **Images** — FLUX, DALL-E, Stable Diffusion (text-to-image)
- **Video** — Kling, Minimax (text-to-video, image-to-video)
- **Speech** — ElevenLabs, OpenAI TTS (text-to-speech)
- **Music** — Suno (text-to-music)

### Workflow Engine
- **YAML-defined pipelines** — chain LLM calls, tool executions, and shell commands
- **Conditions and loops** — conditional branching and iteration
- **Parallel execution** — run steps concurrently
- **Variable passing** — capture outputs and feed into later steps

### Memory & RAG
- **Persistent memory** — remembers context across sessions
- **Vector search** — sqlite-vec with 1024-dimensional embeddings
- **Auto-compaction** — keeps memory lean and relevant

### Security & Permissions
- **4-tier permission system** — AUTO, SESSION, EVERY_TIME, NEVER
- **Progressive trust** — permissions escalate based on usage patterns
- **Audit logging** — every tool execution is logged
- **Keychain storage** — API keys stored securely via OS keychain

---

## Installation

### From PyPI (recommended)

```bash
pip install elidia-agent-cli
```

### From GitHub Release

Download the latest `.whl` file from [Releases](https://github.com/Elidia-Technology/elidia-agent-cli/releases), then:

```bash
pip install elidia_agent_cli-0.1.0-py3-none-any.whl
```

### Requirements

- Python 3.11 or higher
- An AiUtils Developer API key ([get one here](https://developer.aiutils.io))

---

## Quick Start

### 1. Authenticate

```bash
elidia auth login
```

You'll be prompted for your AiUtils Developer API key (`ak-dev-...`).

### 2. Start chatting

```bash
# Interactive REPL
elidia chat

# One-shot query
elidia chat "Explain async/await in Python"

# Specify a model
elidia chat --model claude-sonnet-5 "Review this function for bugs"

# Research mode
elidia chat --mode research "Compare PostgreSQL vs MongoDB for time-series data"
```

### 3. Check your balance

```bash
elidia auth status
```

---

## REPL Commands

Once inside the interactive REPL (`elidia chat`), use slash commands:

| Command | Description |
|---|---|
| `/model [name]` | Switch model or show current |
| `/mode [mode]` | Switch mode (chat, code, research, think, create) |
| `/think [level]` | Set thinking depth (minimal, low, medium, high, max) |
| `/budget` | Show session cost summary |
| `/research <query>` | Run deep research pipeline |
| `/create image <prompt>` | Generate an image |
| `/create video <prompt>` | Generate a video |
| `/create speech <text>` | Generate speech audio |
| `/create music <prompt>` | Generate music |
| `/workflow <file.yaml>` | Execute a YAML workflow |
| `/tools [category]` | List available tools |
| `/mcp` | Show MCP server status |
| `/persona [name]` | Switch agent persona |
| `/memory search\|save\|forget` | Manage persistent memory |
| `/history [query]` | Search chat history |
| `/balance` | Check DT balance |
| `/cost` | Session cost breakdown |
| `/theme [name]` | Switch UI theme |
| `/cache [on\|off]` | Toggle response cache |
| `/new` | Start new session |
| `/clear` | Clear conversation |
| `/help [command]` | Show help |

---

## Configuration

Elidia Agent CLI stores configuration in `~/.elidia/config.toml`:

```toml
[api]
base_url = "https://developer.aiutils.io/v1"
timeout_seconds = 120

[models]
default = "deepseek-chat"
code = "deepseek-chat"
reasoning = "deepseek-reasoner"
creative = "auto"

[permissions]
default_level = "session"

[budget]
session_limit_dt = 50.0
warn_threshold_dt = 40.0

[theme]
name = "default"
```

---

## Available Models

Elidia Agent CLI supports 30+ models across multiple providers:

| Provider | Models |
|---|---|
| **Anthropic** | Claude Sonnet 5, Claude Haiku 4.5 |
| **OpenAI** | GPT-4o, GPT-4o-mini, o3, o4-mini |
| **Google** | Gemini 2.5 Pro, Gemini 2.5 Flash |
| **DeepSeek** | DeepSeek Chat, DeepSeek Reasoner |
| **Meta** | Llama 3.3 70B, Llama 4 Scout, Llama 4 Maverick |
| **Alibaba** | Qwen 2.5 72B, QwQ 32B |
| **Mistral** | Mistral Large, Codestral |
| **Image** | FLUX 1.1 Pro, FLUX Schnell, DALL-E 3, SD 3.5 |
| **Video** | Kling v2, Minimax Video |
| **Audio** | ElevenLabs v2, OpenAI TTS, Suno v4 |

---

## Workflow Example

Create a `research.yaml` file:

```yaml
name: competitor-analysis
description: Research competitors and generate a summary

variables:
  topic: "AI coding assistants"

steps:
  - name: research
    type: llm
    prompt: "List the top 5 {{topic}} with their key features"
    output: findings
    model: deepseek-chat

  - name: analyze
    type: llm
    prompt: "Based on these findings, create a comparison table:\n{{findings}}"
    output: analysis
    model: claude-sonnet-5

  - name: save
    type: shell
    command: "echo '{{analysis}}' > competitor-report.md"
```

Run it:

```bash
elidia chat
> /workflow research.yaml
```

---

## MCP Integration

Connect any MCP-compatible server for extended tool access:

```toml
# ~/.elidia/config.toml
[mcp.servers.filesystem]
command = "npx"
args = ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/allowed/dir"]

[mcp.servers.github]
command = "npx"
args = ["-y", "@modelcontextprotocol/server-github"]
env = { GITHUB_TOKEN = "ghp_..." }
```

---

## Local Models (Experimental)

For offline/free usage via Ollama:

```bash
# Install local model dependencies
pip install elidia-agent-cli[local]

# Use a local model
elidia chat --model ollama:llama3.2
```

---

## API Key

Elidia Agent CLI uses the AiUtils Developer API. Get your API key at [developer.aiutils.io](https://developer.aiutils.io).

Your key starts with `ak-dev-` and is stored securely in your OS keychain — never in plain text files.

---

## Support

- **Issues:** [GitHub Issues](https://github.com/Elidia-Technology/elidia-agent-cli/issues)
- **Email:** support@aiutils.io

---

## License

Proprietary software. Copyright 2026 Elidia Technology Pvt Ltd. All Rights Reserved.

See [LICENSE](LICENSE) for full terms.
