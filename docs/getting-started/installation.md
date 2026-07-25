# Installation

## Requirements

- Python 3.11 or later
- An AiUtils Developer API key ([get one here](https://developer.aiutils.io))

## Install from PyPI

```bash
pip install elidia-cli
```

With optional local model support (requires PyTorch):

```bash
pip install elidia-cli[local]
```

With development tools:

```bash
pip install elidia-cli[dev]
```

## Install from Source

```bash
git clone https://github.com/aiutils/elidia-cli.git
cd elidia-cli
pip install -e ".[dev]"
```

## Standalone Binaries

Pre-built binaries are available on the [GitHub Releases](https://github.com/aiutils/elidia-cli/releases) page for:

- macOS (Apple Silicon and Intel)
- Linux (x86_64)
- Windows (x86_64)

Download, make executable, and run:

```bash
chmod +x elidia-macos-arm64
./elidia-macos-arm64 auth login
```

## Verify Installation

```bash
elidia --version
elidia --help
```

## Authentication

Elidia uses `ak-dev-*` API keys from the AiUtils Developer platform:

```bash
# Interactive login
elidia auth login

# Or set directly
elidia auth set-key ak-dev-your-key-here

# Verify
elidia auth status
```

Keys are stored in `~/.elidia/keychain.json` with file-system permissions restricted to the current user.
