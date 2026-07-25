# Quick Start

## Start a Chat

```bash
# Interactive REPL
elidia chat

# One-shot query
elidia ask "What is the capital of France?"

# Pipe input
echo "Summarize this" | elidia ask --model deepseek-chat
```

## Basic Commands

Once in the REPL, use slash commands:

| Command | Description |
|---------|-------------|
| `/help` | Show all commands |
| `/model <name>` | Switch model (or `auto` for routing) |
| `/mode <mode>` | Switch mode: chat, code, research, think, create |
| `/think <level>` | Set thinking depth: minimal, low, medium, high, max |
| `/cost` | Show session token usage and cost |
| `/new` | Start a new session |
| `/clear` | Clear current conversation |
| `/quit` | Exit |

## Choose a Model

```
> /model deepseek-chat        # Fast, cheap general chat
> /model claude-sonnet-5      # Strong reasoning
> /model claude-opus-4-8      # Maximum capability
> /model auto                 # Let Elidia decide
```

## Research Mode

```
> /research "Latest advances in quantum computing" --export md
```

This runs a multi-source search, synthesizes findings, cites sources, and exports to Markdown.

## Creative Mode

```
> /create image A cyberpunk cityscape at night --model flux-1.1-pro
> /create speech "Hello, world!" --voice alloy
> /create music An upbeat jazz piano track
```

## Run a Workflow

Create a YAML file:

```yaml
name: code_review
steps:
  - name: read_file
    type: shell
    command: "cat main.py"
    output: source_code

  - name: review
    type: llm
    prompt: "Review this code for bugs:\n{{source_code}}"
    output: review_result

  - name: save
    type: shell
    command: "echo '{{review_result}}' > review.md"
```

Run it:

```
> /workflow code_review.yaml
```

## Themes

```
> /theme ocean     # Cool blue-green
> /theme sunset    # Warm orange-red
> /theme minimal   # Low-color
> /theme list      # Show all themes
```

## Next Steps

- [Configuration](configuration.md) — customize models, budget, themes
- [Chat & Modes](../guide/chat-modes.md) — deep dive into modes and thinking levels
- [Tools & MCP](../guide/tools-mcp.md) — extend with MCP servers
