import asyncio
import logging
import sys
from pathlib import Path

import click
from rich.console import Console

from elidia.config.defaults import VERSION

console = Console()
logger = logging.getLogger(__name__)


def _setup_logging(debug: bool) -> None:
    logger.debug("Entered into _setup_logging")
    level = logging.DEBUG if debug else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(sys.stderr)],
    )


_DEFAULT_COMMAND_NAME = "__default__"


class _ElidiaGroup(click.Group):
    """Group that falls back to a hidden one-shot command for free-form messages.

    Click has no built-in way to combine subcommands (``auth``, ``config`` ...)
    with a catch-all positional message (``elidia "question"``): declaring a
    ``nargs=-1`` argument directly on the group would swallow every token,
    including real subcommand names, before dispatch ever runs. Instead this
    tries normal subcommand resolution first and only falls back to the
    hidden ``__default__`` command — treating the raw args as a
    natural-language message — when nothing matches.
    """

    def resolve_command(self, ctx: click.Context, args: list[str]):
        logger.debug(f"Entered into _ElidiaGroup.resolve_command: arg_count={len(args)}")
        try:
            return super().resolve_command(ctx, args)
        except click.UsageError:
            default_cmd = self.commands[_DEFAULT_COMMAND_NAME]
            return _DEFAULT_COMMAND_NAME, default_cmd, args


@click.group(cls=_ElidiaGroup, invoke_without_command=True)
@click.option("--model", "-m", default=None, help="Override model selection")
@click.option("--mode", default="chat", type=click.Choice(["chat", "code", "research", "think", "create"]))
@click.option("--debug", is_flag=True, help="Enable debug logging")
@click.option("--version", "-v", is_flag=True, help="Show version")
@click.option("--file", "-f", "files", multiple=True, type=click.Path(exists=True),
              help="Include file content in context (repeatable)")
@click.option("--image", "-i", "images", multiple=True, type=click.Path(exists=True),
              help="Attach an image for vision analysis (repeatable, jpg/png/webp/gif)")
@click.pass_context
def cli(ctx: click.Context, model: str | None, mode: str, debug: bool, version: bool,
        files: tuple[str, ...], images: tuple[str, ...]) -> None:
    """Elidia Agent CLI — Universal AI Agent for your terminal."""
    _setup_logging(debug)
    logger.debug("Entered into cli")

    if version:
        console.print(f"elidia-agent-cli v{VERSION}")
        ctx.exit()

    ctx.ensure_object(dict)
    ctx.obj["model"] = model
    ctx.obj["mode"] = mode
    ctx.obj["debug"] = debug
    ctx.obj["files"] = files
    ctx.obj["images"] = images

    if ctx.invoked_subcommand is not None:
        return

    if not sys.stdin.isatty():
        stdin_content = sys.stdin.read().strip()
        if stdin_content:
            asyncio.run(_one_shot(stdin_content, model=model, mode=mode, files=files, images=images))
            return

    asyncio.run(_start_repl(model=model, mode=mode))


@click.command(name=_DEFAULT_COMMAND_NAME, hidden=True)
@click.argument("message", nargs=-1)
@click.pass_context
def _default_message(ctx: click.Context, message: tuple[str, ...]) -> None:
    """Hidden fallback: any input that isn't a known subcommand is a one-shot message."""
    logger.debug(f"Entered into _default_message: token_count={len(message)}")
    user_msg = " ".join(message).strip()
    if not user_msg:
        return
    model = ctx.obj.get("model") if ctx.obj else None
    mode = ctx.obj.get("mode", "chat") if ctx.obj else "chat"
    files = ctx.obj.get("files", ()) if ctx.obj else ()
    images = ctx.obj.get("images", ()) if ctx.obj else ()
    asyncio.run(_one_shot(user_msg, model=model, mode=mode, files=files, images=images))


cli.add_command(_default_message)


@cli.command("ask")
@click.argument("message", nargs=-1, required=True)
@click.option("--model", "-m", default=None, help="Override model selection")
@click.option("--mode", default=None, type=click.Choice(["chat", "code", "research", "think", "create"]))
@click.option("--image", "-i", "images", multiple=True, type=click.Path(exists=True),
              help="Attach an image for vision analysis (repeatable, jpg/png/webp/gif)")
@click.option("--file", "-f", "files", multiple=True, type=click.Path(exists=True),
              help="Include file content in context (repeatable)")
@click.pass_context
def ask(ctx: click.Context, message: tuple[str, ...], model: str | None, mode: str | None,
        images: tuple[str, ...], files: tuple[str, ...]) -> None:
    """Send a one-shot message: elidia ask 'your question here'"""
    logger.debug("Entered into ask")
    parent = ctx.parent
    parent_obj = parent.obj if parent and parent.obj else {}
    model = model or parent_obj.get("model")
    mode = mode or parent_obj.get("mode") or "chat"
    images = images or parent_obj.get("images", ())
    files = files or parent_obj.get("files", ())
    user_msg = " ".join(message)
    asyncio.run(_one_shot(user_msg, model=model, mode=mode, images=images, files=files))


_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tiff", ".heic"}
_OFFICE_PARSERS = {".docx": "_parse_docx", ".xlsx": "_parse_xlsx", ".pptx": "_parse_pptx"}


def _summarize_ics(path: Path) -> str:
    """Format .ics events as readable lines instead of raw VCALENDAR/VEVENT
    markup. Not a correctness fix like the office-doc parsers above (.ics is
    plain text, so raw content already reaches the model intact) — just a
    clearer format for what's usually the actually-relevant question
    ("what's on my schedule")."""
    try:
        from icalendar import Calendar
    except ImportError:
        return path.read_text(encoding="utf-8", errors="replace")

    try:
        cal = Calendar.from_ical(path.read_bytes())
    except Exception:
        return path.read_text(encoding="utf-8", errors="replace")

    lines = []
    for component in cal.walk("VEVENT"):
        summary = str(component.get("summary", "(no title)"))
        start = component.get("dtstart")
        end = component.get("dtend")
        lines.append(f"{summary}: {start.dt if start else '?'} - {end.dt if end else '?'}")
    return "\n".join(lines) if lines else "[No events in this calendar]"


_AUTO_INGEST_THRESHOLD = 8_000  # chars — beyond this, index into RAG + preview instead of blind truncation


async def _auto_ingest_file(path: Path, content: str) -> bool:
    """Best-effort: index a large file into the local RAG store so it's
    retrievable via rag_search instead of being silently truncated.
    Returns False (never raises) if no API key is configured or ingestion
    fails for any reason — callers fall back to plain truncation."""
    logger.debug(f"Entered into _auto_ingest_file: path={path}")
    from elidia.auth.keychain import get_api_key

    api_key = get_api_key()
    if not api_key:
        return False

    try:
        import hashlib

        from elidia.memory.embeddings import EmbeddingClient
        from elidia.rag.engine import RagEngine

        engine = RagEngine(EmbeddingClient(api_key=api_key))
        engine.open()
        try:
            file_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
            await engine.ingest(text=content, source=str(path.resolve()), file_hash=file_hash)
        finally:
            engine.close()
        return True
    except Exception as e:
        logger.warning(f"Entered into _auto_ingest_file: auto-ingest failed for {path}: {e}")
        return False


async def _build_file_context(files: tuple[str, ...]) -> str:
    """Read file contents and build a context prefix for the message.

    Files larger than _AUTO_INGEST_THRESHOLD are indexed into the local
    RAG store (auto-ingest) instead of being blindly truncated — the
    message gets a preview plus a pointer to rag_search for the rest.
    """
    if not files:
        return ""
    parts: list[str] = []
    max_total = 100_000
    total = 0
    for path_str in files:
        path = Path(path_str)
        if path.suffix.lower() in _IMAGE_EXTENSIONS:
            parts.append(
                f"[{path.name} is an image — use --image/-i instead of --file/-f "
                f"to attach it for vision analysis, not raw text decoding]"
            )
            continue
        try:
            size = path.stat().st_size
            if size > 1_000_000:
                ingested = await _auto_ingest_file(path, path.read_text(encoding="utf-8", errors="replace"))
                if ingested:
                    parts.append(
                        f"[{path.name} ({size} bytes) is too large to inline but has been indexed "
                        f"into the local RAG store — use rag_search to find relevant sections]"
                    )
                else:
                    parts.append(f"[File too large to include: {path.name} ({size} bytes)]")
                continue
            suffix = path.suffix.lower()
            if suffix in _OFFICE_PARSERS:
                import elidia.rag.ingest as _ingest
                parser = getattr(_ingest, _OFFICE_PARSERS[suffix])
                content = parser(path) or f"[Could not extract text from {path.name}]"
            elif suffix == ".ics":
                content = _summarize_ics(path)
            else:
                content = path.read_text(encoding="utf-8", errors="replace")

            if len(content) > _AUTO_INGEST_THRESHOLD:
                ingested = await _auto_ingest_file(path, content)
                if ingested:
                    preview = content[:_AUTO_INGEST_THRESHOLD] + "\n... (truncated — indexed into RAG, use rag_search for the rest)"
                    parts.append(f"--- {path.name} (preview, {len(content)} chars total, indexed into RAG) ---\n{preview}\n--- end {path.name} preview ---")
                    total += _AUTO_INGEST_THRESHOLD
                    continue

            if total + len(content) > max_total:
                content = content[:max_total - total] + "\n... (truncated)"
            parts.append(f"--- {path.name} ---\n{content}\n--- end {path.name} ---")
            total += len(content)
        except Exception as e:
            parts.append(f"[Could not read {path.name}: {e}]")
    if parts:
        parts.append("---\n")
    return "\n".join(parts)


async def _one_shot(message: str, model: str | None = None, mode: str = "chat",
                    files: tuple[str, ...] = (), images: tuple[str, ...] = ()) -> None:
    logger.debug(
        f"Entered into _one_shot: msg_len={len(message)}, files={len(files)}, images={len(images)}"
    )
    from elidia.cli.repl import ElidiaRepl

    file_ctx = await _build_file_context(files)
    full_message = f"{file_ctx}{message}" if file_ctx else message

    repl = ElidiaRepl(forced_model=model, mode=mode)
    await repl.initialize()

    image_urls: list[str] = []
    for path in images:
        try:
            url = await repl._client.upload_image(path)
            image_urls.append(url)
        except Exception as e:
            console.print(f"[red]Could not attach {path}: {e}[/red]")
            await repl.cleanup()
            return

    await repl.send_message(full_message, interactive=False, image_urls=image_urls or None)
    await repl.cleanup()


async def _start_repl(model: str | None = None, mode: str = "chat") -> None:
    logger.debug("Entered into _start_repl")
    from elidia.cli.repl import ElidiaRepl

    repl = ElidiaRepl(forced_model=model, mode=mode)
    await repl.initialize()
    await repl.run()
    await repl.cleanup()


# --- Auth subcommands ---


@cli.group()
def auth() -> None:
    """Manage API key authentication."""


@auth.command("login")
def auth_login() -> None:
    """Store your AiUtils API key."""
    logger.debug("Entered into auth_login")
    from elidia.auth.keychain import mask_api_key, store_api_key, validate_api_key

    key = click.prompt("Enter your AiUtils API key (get one at developer.aiutils.io)", hide_input=True)
    if not validate_api_key(key):
        console.print("[red]Invalid key format. Keys start with 'ak-dev-'.[/red]")
        raise SystemExit(1)
    store_api_key(key)
    console.print(f"[green]v[/green] API key stored: {mask_api_key(key)}")


@auth.command("logout")
def auth_logout() -> None:
    """Remove stored API key."""
    logger.debug("Entered into auth_logout")
    from elidia.auth.keychain import delete_api_key

    delete_api_key()
    console.print("[green]v[/green] API key removed.")


@auth.command("status")
def auth_status() -> None:
    """Show API key status and balance."""
    logger.debug("Entered into auth_status")
    from elidia.auth.keychain import get_api_key, mask_api_key

    key = get_api_key()
    if not key:
        console.print("[yellow]No API key configured.[/yellow] Run: elidia auth login")
        return
    console.print(f"API Key: {mask_api_key(key)}")

    async def _show_balance() -> None:
        from elidia.api.client import AiUtilsClient
        from elidia.config.settings import load_config

        config = load_config()
        client = AiUtilsClient(api_key=key, base_url=config.api.base_url)
        balance = await client.get_balance()
        await client.close()
        if balance.get("balance_dt", -1) >= 0:
            console.print(f"Balance: {balance['balance_dt']:,.0f} DT (${balance['balance_dt'] / 1000:.2f})")
        else:
            console.print(f"[yellow]Could not fetch balance: {balance.get('error', 'unknown')}[/yellow]")

    asyncio.run(_show_balance())


@auth.command("email-login")
def auth_email_login() -> None:
    """Store email credentials (app password, not your account password) for Email Skills."""
    logger.debug("Entered into auth_email_login")
    from elidia.auth.keychain import store_email_credentials

    console.print(
        "[dim]Use an app password, not your real account password — "
        "Gmail/Outlook/etc. all support generating one for exactly this.[/dim]"
    )
    address = click.prompt("Email address (SMTP/IMAP login)")
    password = click.prompt("App password / API key", hide_input=True)
    smtp_host = click.prompt("SMTP host", default=f"smtp.{address.split('@')[-1]}")
    smtp_port = click.prompt("SMTP port", default=587, type=int)
    imap_host = click.prompt("IMAP host", default=f"imap.{address.split('@')[-1]}")
    imap_port = click.prompt("IMAP port", default=993, type=int)
    from_address = click.prompt(
        "From address shown to recipients", default=address,
    )

    store_email_credentials(address, password, smtp_host, smtp_port, imap_host, imap_port, from_address)
    console.print(f"[green]v[/green] Email account configured: {address}")


@auth.command("email-logout")
def auth_email_logout() -> None:
    """Remove stored email credentials."""
    logger.debug("Entered into auth_email_logout")
    from elidia.auth.keychain import delete_email_credentials

    delete_email_credentials()
    console.print("[green]v[/green] Email credentials removed.")


@auth.command("email-status")
def auth_email_status() -> None:
    """Show configured email account, if any."""
    logger.debug("Entered into auth_email_status")
    from elidia.auth.keychain import get_email_credentials

    creds = get_email_credentials()
    if not creds:
        console.print("[yellow]No email account configured.[/yellow] Run: elidia auth email-login")
        return
    console.print(f"Email account: {creds['address']}")
    console.print(f"SMTP: {creds['smtp_host']}:{creds['smtp_port']}  IMAP: {creds['imap_host']}:{creds['imap_port']}")


# --- Config subcommands ---


@cli.group()
def config() -> None:
    """View and manage configuration."""


@config.command("show")
def config_show() -> None:
    """Show effective configuration."""
    logger.debug("Entered into config_show")
    import dataclasses
    import json

    from elidia.auth.keychain import mask_api_key
    from elidia.config.settings import ELIDIA_HOME, load_config

    cfg = load_config()
    d = dataclasses.asdict(cfg)
    if d.get("api", {}).get("key"):
        d["api"]["key"] = mask_api_key(d["api"]["key"])
    console.print_json(json.dumps(d, indent=2))
    console.print(f"\n[dim]Config dir: {ELIDIA_HOME}[/dim]")


# --- Session subcommands ---


@cli.group()
def session() -> None:
    """Manage chat sessions."""


@session.command("list")
def session_list() -> None:
    """List recent sessions."""
    logger.debug("Entered into session_list")
    from rich.table import Table

    from elidia.db.database import Database
    from elidia.session.manager import SessionManager

    db = Database()
    db.connect()
    mgr = SessionManager(db)
    sessions = mgr.list_sessions()
    db.close()

    if not sessions:
        console.print("[dim]No sessions yet. Start chatting to create one.[/dim]")
        return

    table = Table(title="Sessions")
    table.add_column("ID", style="dim", max_width=8)
    table.add_column("Title")
    table.add_column("Model", style="cyan")
    table.add_column("Updated", style="green")

    for s in sessions:
        table.add_row(s["id"][:8], s["title"], s.get("model") or "auto", s["updated_at"])

    console.print(table)


@session.command("new")
def session_new() -> None:
    """Create a new session."""
    logger.debug("Entered into session_new")
    from elidia.db.database import Database
    from elidia.session.manager import SessionManager

    db = Database()
    db.connect()
    mgr = SessionManager(db)
    sid = mgr.create_session()
    db.close()
    console.print(f"[green]v[/green] Created session: {sid[:8]}")


@session.command("delete")
@click.argument("session_id")
def session_delete(session_id: str) -> None:
    """Delete a session."""
    logger.debug(f"Entered into session_delete: {session_id}")
    from elidia.db.database import Database
    from elidia.session.manager import SessionManager

    db = Database()
    db.connect()
    mgr = SessionManager(db)
    mgr.delete_session(session_id)
    db.close()
    console.print("[green]v[/green] Deleted session.")


@cli.command("version")
def show_version() -> None:
    """Show version information."""
    logger.debug("Entered into show_version")
    console.print(f"elidia-cli v{VERSION}")


# --- Daemon subcommands ---
#
# Real background-process daemon: `elidia daemon start` spawns a detached
# process (elidia/daemon/worker.py) that outlives this command, tracked via
# a PID file, queryable/stoppable from any later `elidia daemon ...`
# invocation over a Unix socket (elidia/daemon/ipc.py). The `/daemon` REPL
# slash command is a different, narrower thing — an in-process task runner
# scoped to that one REPL session; this is the standalone version the
# README's `$ elidia daemon start` / `$ elidia daemon status` examples
# actually need.


@cli.group()
def daemon() -> None:
    """Manage the background daemon (persists across separate CLI invocations)."""


@daemon.command("start")
def daemon_start() -> None:
    """Start the daemon as a detached background process."""
    logger.debug("Entered into daemon_start")
    from elidia.daemon.process import start_daemon
    from elidia.daemon.worker import CONFIG_FILE

    try:
        pid = start_daemon()
    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        raise SystemExit(1) from None
    console.print(f"[green]v[/green] Daemon started (pid {pid}).")
    if not CONFIG_FILE.exists():
        console.print(f"[dim]No tasks configured — run 'elidia daemon init' to create {CONFIG_FILE}[/dim]")


@daemon.command("stop")
def daemon_stop() -> None:
    """Stop the running daemon."""
    logger.debug("Entered into daemon_stop")
    from elidia.daemon.process import stop_daemon

    stopped = asyncio.run(stop_daemon())
    if stopped:
        console.print("[green]v[/green] Daemon stopped.")
    else:
        console.print("[yellow]Daemon is not running.[/yellow]")


@daemon.command("restart")
def daemon_restart() -> None:
    """Restart the daemon (stop, then start)."""
    logger.debug("Entered into daemon_restart")
    from elidia.daemon.process import start_daemon, stop_daemon

    asyncio.run(stop_daemon())
    try:
        pid = start_daemon()
    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        raise SystemExit(1) from None
    console.print(f"[green]v[/green] Daemon restarted (pid {pid}).")


@daemon.command("status")
def daemon_status() -> None:
    """Show whether the daemon is running and what tasks it has."""
    logger.debug("Entered into daemon_status")
    from elidia.daemon.process import get_daemon_status, is_daemon_running

    running, pid = is_daemon_running()
    if not running:
        console.print("[yellow]Daemon is not running.[/yellow] Run: elidia daemon start")
        return

    status = asyncio.run(get_daemon_status())
    if status is None:
        console.print(f"[yellow]Daemon process (pid {pid}) is alive but not responding to status queries.[/yellow]")
        return

    console.print(f"[green]Running[/green] (pid {pid})")
    console.print(f"Tasks: {status['task_count']} ({status['active']} active)")
    for t in status.get("tasks", []):
        style = "green" if t["status"] == "running" else "dim"
        console.print(f"  [{style}]{t['status']:8s}[/{style}] {t['name']} ({t['type']}) runs={t['run_count']}")


@daemon.command("init")
def daemon_init() -> None:
    """Write an example daemon task config to ~/.elidia/daemon.toml."""
    logger.debug("Entered into daemon_init")
    from elidia.daemon.config import write_example_daemon_config
    from elidia.daemon.worker import CONFIG_FILE

    if CONFIG_FILE.exists():
        console.print(f"[yellow]{CONFIG_FILE} already exists — not overwriting.[/yellow]")
        return
    write_example_daemon_config(CONFIG_FILE)
    console.print(f"[green]v[/green] Wrote example config to {CONFIG_FILE}")


# --- MCP subcommands ---


@cli.group()
def mcp() -> None:
    """Manage MCP (Model Context Protocol) server connections."""


@mcp.command("list")
def mcp_list() -> None:
    """List configured MCP servers and their connection status."""
    logger.debug("Entered into mcp_list")
    from elidia.mcp.config import load_mcp_config

    configs = load_mcp_config()
    if not configs:
        console.print("[dim]No MCP servers configured. Edit ~/.elidia/mcp.json to add one.[/dim]")
        return
    for name, cfg in configs.items():
        state = "enabled" if cfg.enabled else "disabled"
        console.print(f"  {name}: {state} ({cfg.command})")


@mcp.command("health")
def mcp_health() -> None:
    """Connect to all enabled MCP servers and report status."""
    logger.debug("Entered into mcp_health")
    from elidia.mcp.registry import MCPRegistry

    async def _check() -> None:
        registry = MCPRegistry()
        await registry.load_and_connect()
        servers = registry.get_connected_servers()
        if not servers:
            console.print("[yellow]No MCP servers connected.[/yellow]")
        else:
            for name, tool_count in servers.items():
                console.print(f"  [green]connected[/green] {name} ({tool_count} tools)")
        await registry.disconnect_all()

    asyncio.run(_check())


# --- Workflow subcommands ---


@cli.group()
def workflow() -> None:
    """Run YAML-defined workflow pipelines."""


@workflow.command("run")
@click.argument("path", type=click.Path(exists=True))
def workflow_run(path: str) -> None:
    """Execute a workflow YAML file."""
    logger.debug(f"Entered into workflow_run: path={path}")
    from elidia.workflow.engine import WorkflowExecutor, parse_workflow, workflow_requires_llm

    try:
        wf = parse_workflow(Path(path))
    except Exception as e:
        console.print(f"[red]Failed to parse workflow: {e}[/red]")
        raise SystemExit(1) from None

    async def _run() -> None:
        from elidia.api.client import AiUtilsClient
        from elidia.auth.keychain import get_api_key
        from elidia.config.settings import load_config

        client = None
        if workflow_requires_llm(wf):
            api_key = get_api_key()
            if not api_key:
                console.print("[red]No API key configured. Run: elidia auth login[/red]")
                console.print("[dim]This workflow has at least one 'llm' step, which needs an API key.[/dim]")
                raise SystemExit(1)
            config = load_config()
            client = AiUtilsClient(api_key=api_key, base_url=config.api.base_url)

        executor = WorkflowExecutor(client=client)

        console.print(f"[cyan]Running workflow:[/cyan] {wf.name}")
        try:
            async for event in executor.run(wf):
                if event.kind == "start":
                    console.print(f"  [dim]Steps: {event.data.get('step_count', 0)}[/dim]")
                elif event.kind == "step_start":
                    console.print(f"  [cyan]>{event.data['name']}[/cyan] ({event.data['type']})")
                elif event.kind == "step_done":
                    status = event.data.get("status", "?")
                    elapsed = event.data.get("elapsed_ms", 0)
                    style = "green" if status == "completed" else "yellow" if status == "skipped" else "red"
                    console.print(f"  [{style}]{status}[/{style}] {event.data['name']} ({elapsed}ms)")
                elif event.kind == "done":
                    console.print(
                        f"[green]v[/green] Workflow complete: {event.data.get('completed', 0)}/"
                        f"{event.data.get('total_steps', 0)} steps ({event.data.get('elapsed_ms', 0)}ms)"
                    )
        finally:
            if client is not None:
                await client.close()

    asyncio.run(_run())


# --- Models command ---

@cli.command("models")
@click.option("--local", is_flag=True, help="Show only locally available models (Ollama)")
def models_cmd(local: bool) -> None:
    """List available AI models."""
    logger.debug(f"Entered into models_cmd: local={local}")
    if local:
        from elidia.models.local import list_local_models
        async def _run():
            models = await list_local_models()
            if not models:
                console.print("[yellow]No local models found. Install Ollama and pull a model.[/yellow]")
                return
            console.print(f"\n[bold]Local Models (Ollama)[/bold]\n")
            for m in models:
                console.print(f"  [cyan]{m.name}[/cyan]  {m.parameter_size}  {m.context_length} ctx  caps: {', '.join(m.capabilities[:3])}")
            console.print(f"\n[dim]{len(models)} model(s) available locally[/dim]")
        return asyncio.run(_run())

    from elidia.auth.keychain import get_api_key
    from elidia.config.settings import load_config
    from elidia.api.client import AiUtilsClient
    api_key = get_api_key()
    if not api_key:
        console.print("[red]No API key configured. Run: elidia auth login[/red]")
        return
    config = load_config()

    async def _run_remote():
        client = AiUtilsClient(api_key=api_key, base_url=config.api.base_url)
        try:
            models = await client.list_models()
            if not models:
                console.print("[yellow]No models returned from API.[/yellow]")
                return
            console.print(f"\n[bold]Available Models ({len(models)} total)[/bold]\n")
            for m in models:
                name = m.get("id", m.get("name", "?"))
                provider = m.get("owned_by", m.get("provider", "?"))
                cost = m.get("pricing", {}).get("input", "?")
                caps = []
                if m.get("supports_vision"): caps.append("vision")
                if m.get("supports_reasoning"): caps.append("reasoning")
                if m.get("supports_tools"): caps.append("tools")
                cap_str = f"[dim]({', '.join(caps)})[/dim]" if caps else ""
                console.print(f"  [cyan]{name}[/cyan] @ [dim]{provider}[/dim] {cap_str}")
            console.print(f"\n[dim]Use: elidia --model <name> ask '...'  |  /model <name> in REPL[/dim]")
        finally:
            await client.close()
    asyncio.run(_run_remote())


# --- RAG subcommands ---


async def _open_rag_engine():
    """Shared setup for the rag CLI subcommands: real API key required
    (embeddings go through the AiUtils API), console error + SystemExit(1)
    if missing so every subcommand fails the same clear way."""
    from elidia.auth.keychain import get_api_key
    from elidia.memory.embeddings import EmbeddingClient
    from elidia.rag.engine import RagEngine

    api_key = get_api_key()
    if not api_key:
        console.print("[red]No API key configured. Run: elidia auth login[/red]")
        console.print("[dim]RAG ingestion/search needs it for embeddings.[/dim]")
        raise SystemExit(1)

    engine = RagEngine(EmbeddingClient(api_key=api_key))
    engine.open()
    return engine


@cli.group()
def rag() -> None:
    """Manage the local RAG (retrieval-augmented search) store."""


@rag.command("ingest")
@click.argument("path", type=click.Path(exists=True))
@click.option("--recursive/--no-recursive", default=True, help="Recurse into subdirectories (directories only)")
def rag_ingest(path: str, recursive: bool) -> None:
    """Ingest a file or directory into the local RAG store for later rag_search."""
    logger.debug(f"Entered into rag_ingest: path={path}, recursive={recursive}")
    from elidia.rag.ingest import FileIngestPipeline

    target = Path(path)

    async def _run() -> None:
        engine = await _open_rag_engine()
        pipeline = FileIngestPipeline(engine)
        try:
            if target.is_dir():
                console.print(f"[cyan]Ingesting directory:[/cyan] {target}")
                result = await pipeline.ingest_directory(target, recursive=recursive)
                console.print(
                    f"[green]v[/green] Ingested {result['files']} file(s), "
                    f"{result['chunks']} chunk(s), skipped {result['skipped']}"
                )
            else:
                console.print(f"[cyan]Ingesting file:[/cyan] {target}")
                ids = await pipeline.ingest_file(target)
                if ids:
                    console.print(f"[green]v[/green] Ingested {len(ids)} chunk(s) from {target.name}")
                else:
                    console.print(
                        f"[yellow]Nothing ingested from {target.name}[/yellow] "
                        "(unsupported type, empty, too large, or already ingested — same content hash)"
                    )
        finally:
            engine.close()

    asyncio.run(_run())


@rag.command("search")
@click.argument("query")
@click.option("--limit", default=5, type=int, help="Max results")
def rag_search_cmd(query: str, limit: int) -> None:
    """Search the local RAG store."""
    logger.debug(f"Entered into rag_search_cmd: query={query!r}, limit={limit}")

    async def _run() -> None:
        engine = await _open_rag_engine()
        try:
            results = await engine.search(query, limit=limit)
            if not results:
                console.print("[yellow]No matching content found.[/yellow]")
                return
            for r in results:
                doc = r.document
                console.print(
                    f"\n[cyan]{doc.source}[/cyan] "
                    f"[dim](chunk {doc.chunk_index + 1}/{doc.total_chunks}, score={r.score:.3f})[/dim]"
                )
                console.print(doc.content[:500] + ("..." if len(doc.content) > 500 else ""))
        finally:
            engine.close()

    asyncio.run(_run())


@rag.command("list")
def rag_list() -> None:
    """Show how much content is currently ingested."""
    logger.debug("Entered into rag_list")

    async def _run() -> None:
        engine = await _open_rag_engine()
        try:
            counts = engine.count_documents()
            if counts["sources"] == 0:
                console.print("[yellow]Nothing has been ingested yet.[/yellow] Run: elidia rag ingest <path>")
            else:
                console.print(f"{counts['sources']} source(s), {counts['chunks']} chunk(s) ingested.")
        finally:
            engine.close()

    asyncio.run(_run())


@rag.command("clear")
@click.option("--source", default=None, help="Only clear entries from this source path (default: clear everything)")
@click.confirmation_option(prompt="This permanently deletes ingested RAG data. Continue?")
def rag_clear(source: str | None) -> None:
    """Delete ingested content from the local RAG store."""
    logger.debug(f"Entered into rag_clear: source={source}")

    async def _run() -> None:
        engine = await _open_rag_engine()
        try:
            if source:
                n = engine.delete_source(source)
                console.print(f"[green]v[/green] Deleted {n} chunk(s) from {source}")
            else:
                n = engine.clear_all()
                console.print(f"[green]v[/green] Deleted {n} chunk(s) — RAG store is now empty")
        finally:
            engine.close()

    asyncio.run(_run())
