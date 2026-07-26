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
@click.pass_context
def ask(ctx: click.Context, message: tuple[str, ...], model: str | None, mode: str | None,
        images: tuple[str, ...]) -> None:
    """Send a one-shot message: elidia ask 'your question here'"""
    logger.debug("Entered into ask")
    parent = ctx.parent
    parent_obj = parent.obj if parent and parent.obj else {}
    model = model or parent_obj.get("model")
    mode = mode or parent_obj.get("mode") or "chat"
    images = images or parent_obj.get("images", ())
    user_msg = " ".join(message)
    asyncio.run(_one_shot(user_msg, model=model, mode=mode, images=images))


_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tiff", ".heic"}
_OFFICE_PARSERS = {".docx": "_parse_docx", ".xlsx": "_parse_xlsx", ".pptx": "_parse_pptx"}


def _build_file_context(files: tuple[str, ...]) -> str:
    """Read file contents and build a context prefix for the message."""
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
                parts.append(f"[File too large to include: {path.name} ({size} bytes)]")
                continue
            suffix = path.suffix.lower()
            if suffix in _OFFICE_PARSERS:
                import elidia.rag.ingest as _ingest
                parser = getattr(_ingest, _OFFICE_PARSERS[suffix])
                content = parser(path) or f"[Could not extract text from {path.name}]"
            else:
                content = path.read_text(encoding="utf-8", errors="replace")
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

    file_ctx = _build_file_context(files)
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
    address = click.prompt("Email address")
    password = click.prompt("App password", hide_input=True)
    smtp_host = click.prompt("SMTP host", default=f"smtp.{address.split('@')[-1]}")
    smtp_port = click.prompt("SMTP port", default=587, type=int)
    imap_host = click.prompt("IMAP host", default=f"imap.{address.split('@')[-1]}")
    imap_port = click.prompt("IMAP port", default=993, type=int)

    store_email_credentials(address, password, smtp_host, smtp_port, imap_host, imap_port)
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
