# Configuration

Elidia stores its configuration at `~/.elidia/config.toml`.

## Default Configuration

```toml
[api]
base_url = "https://developer.aiutils.io/v1"
timeout_seconds = 120
max_retries = 3

[models]
code = "auto"
reasoning = "auto"
creative = "auto"
vision = "auto"
cheap = "auto"

[permissions]
auto_approve_read = true
auto_approve_web_search = false
require_approval_shell = true

[budget]
session_limit_dt = 50000.0
warn_threshold = 0.8
```

## API Settings

| Key | Default | Description |
|-----|---------|-------------|
| `base_url` | `https://developer.aiutils.io/v1` | API endpoint |
| `timeout_seconds` | `120` | Request timeout |
| `max_retries` | `3` | Retry count for failed requests |

## Model Overrides

Set a specific model for each task category, or `auto` for intelligent routing:

```toml
[models]
code = "deepseek-chat"           # Code generation and analysis
reasoning = "deepseek-reasoner"  # Complex reasoning tasks
creative = "claude-sonnet-5"     # Creative writing
vision = "gpt-4o"                # Image understanding
cheap = "deepseek-chat"          # Quick, low-cost queries
```

## Permission Defaults

Control which actions require user approval:

```toml
[permissions]
auto_approve_read = true          # File reads
auto_approve_web_search = false   # Web searches
require_approval_shell = true     # Shell commands
```

## Budget Controls

Set spending limits per session:

```toml
[budget]
session_limit_dt = 50000.0   # Max DT per session (1 DT ≈ $0.001)
warn_threshold = 0.8         # Warn at 80% of limit
```

## Custom Themes

Create `~/.elidia/themes.toml`:

```toml
[matrix]
description = "Green on black"
primary = "green"
secondary = "bright_green"
accent = "green"
success = "bright_green"
warning = "yellow"
error = "red"
border = "green"
prompt_style = "bold green"
dim = "dim green"

[pastel]
description = "Soft pastel colors"
primary = "light_sky_blue1"
secondary = "plum1"
accent = "light_pink1"
border = "light_sky_blue1"
prompt_style = "bold light_sky_blue1"
```

Load with: `/theme matrix`

## Project Rules

Create `.elidia/rules.md` in your project root to set project-specific instructions that are injected into every prompt:

```markdown
# Project Rules

- Use Python 3.11+ features
- Follow PEP 8
- All functions must have type hints
```

## Environment Variables

| Variable | Description |
|----------|-------------|
| `ELIDIA_API_KEY` | API key (overrides keychain) |
| `ELIDIA_HOME` | Config directory (default: `~/.elidia`) |
| `ELIDIA_MODEL` | Default model override |
| `PAGER` | Terminal pager for long output (default: `less -R`) |
