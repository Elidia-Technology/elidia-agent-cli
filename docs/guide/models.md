# Models

## Model Routing

When set to `auto`, Elidia's ModelRouter selects the optimal model based on:

1. **Task category** — code, reasoning, creative, vision, general
2. **Input complexity** — length, structure, presence of code/math
3. **Current mode** — chat, code, research, think, create
4. **Budget** — remaining session budget influences model tier

## Available Models

Elidia connects to 30+ models through the AiUtils Developer API:

### Reasoning Models
| Model | Strengths |
|-------|-----------|
| `deepseek-reasoner` | Extended chain-of-thought, math, logic |
| `o1` | Multi-step reasoning |
| `o3` | Advanced reasoning |
| `o4-mini` | Fast reasoning |

### Code Models
| Model | Strengths |
|-------|-----------|
| `deepseek-chat` | Fast, cheap code generation |
| `claude-sonnet-5` | Strong code with reasoning |
| `gpt-4.1` | Reliable code generation |

### General Models
| Model | Strengths |
|-------|-----------|
| `claude-opus-4-8` | Maximum capability |
| `claude-sonnet-5` | Balanced performance |
| `gpt-4o` | Vision + general |
| `gemini-2.5-pro` | Long context |

### Creative Models
| Model | Strengths |
|-------|-----------|
| `claude-sonnet-5` | Creative writing |
| `gpt-4o` | Versatile creative |

## Pinning a Model

```
> /model deepseek-chat       # Use this model for everything
> /model claude-opus-4-8     # Switch to strongest
> /model auto                # Return to auto-routing
```

Or via CLI flag:

```bash
elidia chat --model deepseek-chat
```

## Model Pricing

Costs are measured in DT (Developer Tokens, 1 DT ~ $0.001):

```
> /cost                      # Show session spending
> /budget                    # Show budget status
```

Use `/budget` to monitor spending and the budget governor will warn when approaching session limits and suggest cheaper alternatives.
