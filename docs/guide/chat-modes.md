# Chat & Modes

## Modes

Elidia operates in five modes, each optimizing model selection and system prompts:

| Mode | Best For | Default Model Strategy |
|------|----------|----------------------|
| `chat` | General conversation | Cheapest capable model |
| `code` | Code generation, debugging | Code-optimized model |
| `research` | Information gathering | Balanced model + search tools |
| `think` | Complex reasoning | Reasoning model with extended thinking |
| `create` | Creative writing, media | Creative-optimized model |

Switch modes with `/mode`:

```
> /mode code
> /mode research
```

## Thinking Levels

Control how deeply Elidia reasons about each query:

| Level | Loops | Agents | Tools | Web | Consensus | Swarm |
|-------|-------|--------|-------|-----|-----------|-------|
| `minimal` (1) | 1 | 0 | No | No | No | No |
| `low` (2) | 2 | 1 | Yes | No | No | No |
| `medium` (3) | 5 | 3 | Yes | Yes | No | No |
| `high` (4) | 10 | 5 | Yes | Yes | Yes | No |
| `max` (5) | 20 | 10 | Yes | Yes | Yes | Yes |

```
> /think high
> /think 5         # Same as /think max
> /think min       # Aliases work too
```

## Execution Modes

The agent loop automatically classifies each query into an execution mode:

- **Direct** — single model call (most queries)
- **Deep** — streaming reasoning with a thinking model (complex logic, math, multi-step problems)
- **Consensus** — parallel queries to multiple models, then synthesis (controversial or high-stakes questions)
- **Harness** — autonomous multi-step with tool use (requires `/think high` or above)

## Budget Governor

Track and limit spending per session:

```
> /budget
```

Shows current session usage, token counts, and percentage of limit used. Configure limits in `config.toml`:

```toml
[budget]
session_limit_dt = 50000.0
warn_threshold = 0.8
```

When projected cost exceeds the session limit, Elidia warns and suggests a cheaper model.

## Personas

Switch the assistant's personality and system prompt:

```
> /persona list      # Show available personas
> /persona coder     # Activate coder persona
> /persona off       # Deactivate
```

## Session Management

```
> /new               # Start fresh session
> /sessions          # List recent sessions
> /history search    # Search past conversations
> /clear             # Clear current messages
```
