# CLAUDE.md — Elidia CLI

## Project
Standalone terminal AI agent. Python 3.11+, Click + Rich + prompt_toolkit, httpx for API.
This is a **separate project** from the AiUtils.io portal — independent source code, build, and distribution.

## Stack
- Python 3.11+ / Click CLI / Rich TUI / prompt_toolkit REPL
- httpx[http2] async API client (AiUtils Developer API)
- sqlite-vec for local RAG (1024-dim embeddings)
- keyring for credential storage
- PyYAML for workflow engine

## Source control
- **GitLab (primary, origin):** all source code, versioning, CI/CD
- **GitHub (public, developer builds only):** `git@github-elidia:Elidia-Technology/elidia-cli.git`
  - SSH host alias `github-elidia` in `~/.ssh/config` uses `~/.ssh/id_ed25519` (admin@aiutils.io)
  - Authenticates as GitHub user `aiutils-io`
  - GitHub is for developer builds, downloads, documentation, and public information only
  - Remote name: `github`

## SSH access for GitHub
```
# ~/.ssh/config entry:
Host github-elidia
  HostName github.com
  User git
  IdentityFile ~/.ssh/id_ed25519
  IdentitiesOnly yes
  AddKeysToAgent yes
```
- Push: `git push github master`
- The default `github.com` host uses `~/.ssh/RSA5` (SaleemLww account) — do NOT use that for this repo

## API
- Base URL: `https://developer.aiutils.io/v1`
- API keys: `ak-dev-*` prefix
- 30+ models (Claude, GPT, Gemini, DeepSeek, Llama, Qwen)

## Tests
```bash
python -m pytest tests/ -v --tb=short
```
165 tests across 12 test files. All must pass before any commit.

## Lint
```bash
python -m ruff check elidia/
```

## Company
Elidia Technology Pvt Ltd. Proprietary license. See LICENSE file.

## Rules
- No placeholders, TODOs, stubs, or mock data in production code
- No keyword/regex intent detection — use LLM intelligence only
- Every function starts with `logger.debug(f"Entered into <fn>: ...")`
- No secrets in code — API keys via keyring only
- Verify before claiming done — tests must actually pass
