# Architecture Overview

Elidia CLI is a standalone terminal AI agent built in Python. It connects to the AiUtils Developer API for model inference and tool execution, with no dependency on the AiUtils portal backend.

## Design Principles

1. **Standalone** — self-contained; copies what it needs, never imports from the portal
2. **Multi-model** — routes to the best model per task, not locked to one provider
3. **Tool-augmented** — agent loop with permission-gated tool execution
4. **Extensible** — MCP servers add tools without code changes
5. **Observable** — structured logging, audit trail, budget tracking

## System Architecture

```mermaid
graph TB
    subgraph CLI["CLI Layer"]
        REPL[REPL]
        CMD[Commands]
        PAGER[Auto-Pager]
        THEME[Theme Manager]
        PROG[Progress]
    end

    subgraph AGENT["Agent Layer"]
        LOOP[Agent Loop]
        PERSONA[Persona Engine]
        PORTAL[Portal Bridge]
    end

    subgraph MODES["Mode Layer"]
        CLASS[Mode Classifier]
        THINK[Thinking Levels]
        DEEP[Deep Think]
        CONS[Consensus]
        AUTO[Autonomous]
        SWARM[Swarm]
        BUD[Budget Governor]
    end

    subgraph TOOLS_LAYER["Tool Layer"]
        TREG[Tool Registry]
        FS[Filesystem]
        SHELL[Terminal]
        WEB[Fetch/Search]
        GIT_T[Git]
        MCP_R[MCP Registry]
    end

    subgraph DATA["Data Layer"]
        DB[SQLite Database]
        SESS[Session Manager]
        MEM[Memory Store]
        CACHE[Response Cache]
        HIST[History Search]
    end

    subgraph EXTERNAL["External Services"]
        API[AiUtils Developer API]
        MCP_S[MCP Servers]
    end

    REPL --> LOOP
    CMD --> REPL
    PAGER --> REPL
    THEME --> REPL
    PROG --> REPL

    LOOP --> CLASS
    LOOP --> TREG
    LOOP --> MCP_R
    CLASS --> DEEP
    CLASS --> CONS
    CLASS --> AUTO
    CLASS --> SWARM
    THINK --> LOOP
    BUD --> LOOP

    LOOP --> API
    MCP_R --> MCP_S

    LOOP --> DB
    SESS --> DB
    MEM --> DB
    HIST --> DB
```

## Request Flow

```mermaid
sequenceDiagram
    participant U as User
    participant R as REPL
    participant A as Agent Loop
    participant C as Classifier
    participant M as Model Router
    participant API as AiUtils API
    participant T as Tool Registry

    U->>R: Input message
    R->>A: send_message()
    A->>C: classify_mode()
    C-->>A: ExecMode (direct/deep/consensus)
    A->>M: route(query, mode)
    M-->>A: model selection
    A->>API: chat_completion_stream()
    API-->>A: SSE response chunks

    alt Tool call requested
        A->>T: execute_tool()
        T-->>A: tool result
        A->>API: continue with tool result
        API-->>A: final response
    end

    A-->>R: AgentEvent stream
    R-->>U: Rendered output
```

## Package Dependencies

```mermaid
graph LR
    CLI[cli] --> AGENT[agent]
    CLI --> CACHE[cache]
    AGENT --> API[api]
    AGENT --> TOOLS[tools]
    AGENT --> MCP[mcp]
    AGENT --> MODES[modes]
    AGENT --> MODELS[models]
    API --> AUTH[auth]
    CLI --> CONFIG[config]
    CLI --> CREATIVE[creative]
    CLI --> RESEARCH[research]
    CLI --> WORKFLOW[workflow]
    CLI --> DAEMON[daemon]
    CLI --> WIDGETS[widgets]
    AGENT --> PERMISSIONS[permissions]
    AGENT --> MEMORY[memory]
    AGENT --> SESSION[session]
    SESSION --> DB[db]
    MEMORY --> DB
    CREATIVE --> API
    RESEARCH --> API
```

## Data Flow

All data flows through three channels:

1. **User ↔ REPL** — prompt_toolkit input, Rich output (with pager/themes)
2. **Agent ↔ API** — httpx async HTTP/2 with SSE streaming
3. **Agent ↔ Tools** — local tool execution or MCP server RPC

State is persisted to:

- `~/.elidia/elidia.db` — sessions, messages, memory entries
- `~/.elidia/config.toml` — user configuration
- `~/.elidia/keychain.json` — API key storage
- `~/.elidia/audit.jsonl` — permission audit log
- `~/.elidia/media/` — generated images, videos, audio
- `~/.elidia/research/` — exported research reports
