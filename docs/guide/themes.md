# Themes

## Built-in Themes

Elidia ships with 6 color themes:

| Theme | Description |
|-------|-------------|
| `default` | Cyan and blue, balanced for most terminals |
| `dark` | High contrast with bright colors |
| `light` | Muted colors optimized for light terminals |
| `minimal` | Monochrome with minimal color |
| `ocean` | Cool blue-green palette |
| `sunset` | Warm orange-red palette |

## Switching Themes

```
> /theme list       # Show all available themes
> /theme ocean      # Switch to ocean
> /theme default    # Back to default
```

## Custom Themes

Create `~/.elidia/themes.toml` with one or more theme definitions:

```toml
[matrix]
description = "Green on black hacker theme"
primary = "green"
secondary = "bright_green"
accent = "green"
success = "bright_green"
warning = "yellow"
error = "red"
border = "green"
prompt_style = "bold green"
dim = "dim green"
code_style = "green"
heading_style = "bold bright_green"
status_style = "dim green"

[solarized]
description = "Solarized dark"
primary = "dark_cyan"
secondary = "blue"
accent = "dark_orange"
success = "green"
warning = "yellow"
error = "red"
border = "dark_cyan"
prompt_style = "bold dark_cyan"
```

Custom themes appear alongside built-in themes in `/theme list`.

## Theme Properties

Each theme defines 14 color properties:

| Property | Controls |
|----------|----------|
| `primary` | Main UI elements, headings |
| `secondary` | Supporting elements |
| `accent` | Highlights, active items |
| `success` | Success messages, checkmarks |
| `warning` | Warning messages |
| `error` | Error messages |
| `dim` | Subdued text, metadata |
| `border` | Panel and box borders |
| `prompt_style` | Input prompt styling |
| `assistant_style` | Assistant response styling |
| `code_style` | Code block styling |
| `heading_style` | Section headings |
| `status_style` | Status bar text |

## Color Values

Use any [Rich color name](https://rich.readthedocs.io/en/latest/appendix/colors.html):

- Named: `red`, `green`, `blue`, `cyan`, `magenta`, `yellow`
- Bright: `bright_red`, `bright_green`, `bright_cyan`
- Extended: `deep_sky_blue1`, `spring_green2`, `hot_pink`
- Styles: `bold`, `dim`, `italic`, `underline`
- Combined: `bold bright_cyan`, `dim green`
