# CLI Commands

## Entry Points

### `elidia chat`

Start an interactive REPL session.

```bash
elidia chat [--model MODEL] [--mode MODE]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--model` | `auto` | Pin a specific model |
| `--mode` | `chat` | Initial mode: chat, code, research, think, create |

### `elidia ask`

One-shot query (non-interactive).

```bash
elidia ask "your question" [--model MODEL]
```

### `elidia auth`

API key management.

```bash
elidia auth login          # Interactive login
elidia auth set-key KEY    # Set key directly
elidia auth status         # Show current key status
elidia auth logout         # Remove stored key
```

### `elidia version`

Print version and exit.

## REPL Slash Commands

### Session

| Command | Description |
|---------|-------------|
| `/help [cmd]` | Show all commands or help for a specific command |
| `/new` | Start a new session |
| `/sessions` | List recent sessions |
| `/clear` | Clear current conversation |
| `/cost` | Show session token usage and cost |
| `/quit` | Exit the REPL |

### Model & Mode

| Command | Description |
|---------|-------------|
| `/model [name]` | Show or set current model (`auto` for routing) |
| `/mode [name]` | Show or set mode: chat, code, research, think, create |
| `/think [level]` | Show or set thinking level: minimal, low, medium, high, max |
| `/budget` | Show budget status and session spending |

### Tools & Integration

| Command | Description |
|---------|-------------|
| `/tools [category]` | List available tools, optionally filtered by category |
| `/mcp` | List connected MCP servers |
| `/mcp disconnect NAME` | Disconnect an MCP server |
| `/trust` | Show trust engine statistics |
| `/balance` | Check API credit balance |

### Memory & History

| Command | Description |
|---------|-------------|
| `/memory` | List stored memories |
| `/memory search QUERY` | Search memories |
| `/memory save key=value` | Save a memory entry |
| `/memory forget KEY` | Delete memories by key |
| `/history` | Show session statistics |
| `/history QUERY` | Search past conversations |

### Persona

| Command | Description |
|---------|-------------|
| `/persona list` | List available personas |
| `/persona NAME` | Activate a persona |
| `/persona off` | Deactivate current persona |

### Research & Creative

| Command | Description |
|---------|-------------|
| `/research QUERY` | Run multi-source research |
| `/research QUERY --export md` | Research and export to Markdown |
| `/research QUERY --export html` | Research and export to HTML |
| `/create image PROMPT` | Generate an image |
| `/create video PROMPT` | Generate a video |
| `/create speech TEXT` | Generate speech (TTS) |
| `/create music PROMPT` | Generate music |
| `/create models` | List available creative models |

### Workflow & Daemon

| Command | Description |
|---------|-------------|
| `/workflow PATH` | Run a YAML workflow |
| `/daemon status` | Show daemon status |
| `/daemon start` | Start the daemon |
| `/daemon stop` | Stop the daemon |
| `/daemon watch PATH [NAME]` | Add a file watcher |
| `/daemon schedule SECS [CMD]` | Add a scheduled task |

### Appearance

| Command | Description |
|---------|-------------|
| `/theme [name]` | Show or set theme |
| `/theme list` | List available themes |
| `/cache` | Show cache statistics |
| `/cache on` | Enable response cache |
| `/cache off` | Disable response cache |
| `/cache clear` | Clear all cached responses |
| `/pager` | Show pager status |
| `/pager on` | Enable auto-pager |
| `/pager off` | Disable auto-pager |
| `/rules` | Show project rules |
