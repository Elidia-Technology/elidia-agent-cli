# Changelog

All notable changes to Elidia CLI will be documented in this file.

## [0.3.0] - 2026-07-27

### Added
- RAG reconnected to every user-facing surface: `elidia rag ingest/search/list/clear`
  (CLI), `/rag ingest|search|list|clear` (REPL), and auto-ingest for files over
  ~8,000 chars passed via `-f/--file` (preview + index instead of blind truncation,
  including files >1MB that were previously dropped entirely). New agent-facing
  `rag_search`/`rag_list_sources` tools (AUTO permission tier).

### Fixed
- `elidia workflow run` required an API key even for pure-shell workflows with no
  `llm` step.
- Email skill used the same field for the SMTP/IMAP login and the visible `From`
  address, which breaks for transactional relays (Zepto, SendGrid, Mailgun, SES)
  whose AUTH username is a token distinct from the sender address. Added
  `from_address`.
- RAG's FTS5 query builder crashed on any hyphenated search term (`"no such
  column: call"` on a query containing "on-call").
- RAG's chunker never split a paragraph/section/line once it exceeded
  `chunk_size` — a 21KB file with no blank lines produced 1 chunk instead of
  ~40, diluting the embedding enough that content buried mid-file became
  unfindable by search.
- `elidia ask` silently dropped `-f/--file` — the subcommand never declared its
  own option or read the parent group's.

## [0.2.0] - 2026-07-26

### Added
- Real background daemon (`elidia daemon start/stop/restart/status/init`),
  `elidia mcp list/health`, `elidia workflow run`.
- Vision support: `--image`/`-i` flag, `/image` REPL command, S3/CDN upload.
- Browser, Office, Database, Email, and Calendar skills (5 new tool categories).

### Fixed
- Tool-calling never actually worked — the system prompt (and therefore every
  tool schema) was never sent to the model; the agent could only ever respond
  from training knowledge, never call a real tool.
- Permission classification for tool calls with an omitted path argument fell
  through to the wrong (over-restrictive) tier.

## [0.1.0] - 2026-07-25

### Added
- Phase 0: Core CLI framework (Click + Rich + prompt_toolkit)
- Phase 1: MCP integration, built-in tools, model router, permissions
- Phase 2: Persistent memory (sqlite-vec), RAG engine, session management
- Phase 3: Advanced modes (classifier, consensus, deep think, autonomous, swarm)
- Phase 3: Research pipeline (YOYO 5-stage), creative generation, workflow engine
- Phase 3: Daemon mode, widget protocol, terminal image display
- Phase 4: Auto-pager, progress indicators, response cache, configurable themes
- Phase 4: Connection pooling (httpx HTTP/2), PyPI packaging, CI/CD pipeline
- Phase 4: Automated test suite, MkDocs documentation site
