import logging
import sys
import time

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.history import FileHistory
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

from elidia.api.client import AiUtilsClient, ChatMessage
from elidia.auth.keychain import get_api_key, mask_api_key
from elidia.config.defaults import VERSION
from elidia.config.settings import ELIDIA_HOME, ElidiaConfig, ensure_elidia_home, load_config
from elidia.db.database import Database
from elidia.models.router import ModelRouter
from elidia.session.manager import SessionManager

logger = logging.getLogger(__name__)


class ElidiaRepl:
    def __init__(self, forced_model: str | None = None, mode: str = "chat"):
        logger.debug(f"Entered into ElidiaRepl.__init__: mode={mode}")
        self._config: ElidiaConfig | None = None
        self._client: AiUtilsClient | None = None
        self._db: Database | None = None
        self._session_mgr: SessionManager | None = None
        self._router: ModelRouter | None = None
        self._session_id: str | None = None
        self._mode = mode
        self._forced_model = forced_model
        self._console = Console()
        self._messages: list[ChatMessage] = []
        self._total_cost_dt: float = 0.0
        self._total_tokens_in: int = 0
        self._total_tokens_out: int = 0

    async def initialize(self) -> None:
        logger.debug("Entered into initialize")
        ensure_elidia_home()
        self._config = load_config()

        api_key = get_api_key()
        if not api_key and self._config.api.key:
            api_key = self._config.api.key

        if not api_key:
            self._console.print("[red]No API key found.[/red] Run: [bold]elidia auth login[/bold]")
            raise SystemExit(1)

        self._client = AiUtilsClient(
            api_key=api_key,
            base_url=self._config.api.base_url,
            timeout=self._config.api.timeout_seconds,
            max_retries=self._config.api.max_retries,
        )

        model_overrides: dict[str, str] = {}
        mc = self._config.models
        for attr in ("code", "reasoning", "creative", "vision", "cheap"):
            val = getattr(mc, attr, None)
            if val and val != "auto":
                model_overrides[attr] = val

        self._router = ModelRouter(config_models=model_overrides)
        if self._forced_model:
            self._router.force_model(self._forced_model)

        self._db = Database()
        self._db.connect()
        self._session_mgr = SessionManager(self._db)

        last_sid = self._session_mgr.get_last_session_id()
        if last_sid:
            self._session_id = last_sid
            history = self._session_mgr.get_messages(last_sid, limit=50)
            for msg in history:
                self._messages.append(ChatMessage(role=msg["role"], content=msg["content"]))
        else:
            self._session_id = self._session_mgr.create_session(mode=self._mode)

    async def cleanup(self) -> None:
        logger.debug("Entered into cleanup")
        if self._client:
            await self._client.close()
        if self._db:
            self._db.close()

    def _print_banner(self) -> None:
        logger.debug("Entered into _print_banner")
        api_key = get_api_key() or (self._config.api.key if self._config else "")
        masked = mask_api_key(api_key) if api_key else "not set"
        model = self._forced_model or "auto"
        msg_count = len(self._messages)

        banner = Text()
        banner.append(f"ELIDIA v{VERSION}", style="bold cyan")
        banner.append(f" --- {masked}", style="dim")
        banner.append(f"\n Model: {model}", style="")
        banner.append(f" | Messages: {msg_count}", style="")
        banner.append(f" | Mode: {self._mode}", style="")

        self._console.print(Panel(banner, border_style="cyan", padding=(0, 1)))
        self._console.print("[dim]Type your message. Ctrl+C to cancel, Ctrl+D to exit. /help for commands.[/dim]")
        self._console.print()

    async def run(self) -> None:
        logger.debug("Entered into run")
        self._print_banner()

        history_file = ELIDIA_HOME / "history"
        prompt_session: PromptSession[str] = PromptSession(
            history=FileHistory(str(history_file)),
            auto_suggest=AutoSuggestFromHistory(),
            multiline=False,
        )

        while True:
            try:
                user_input = await prompt_session.prompt_async("> ")
            except (EOFError, KeyboardInterrupt):
                self._console.print("\n[dim]Goodbye.[/dim]")
                break

            user_input = user_input.strip()
            if not user_input:
                continue

            try:
                if user_input.startswith("/"):
                    handled = self._handle_slash_command(user_input)
                    if handled:
                        continue

                await self.send_message(user_input, interactive=True)
            except EOFError:
                self._console.print("\n[dim]Goodbye.[/dim]")
                break

    def _handle_slash_command(self, command: str) -> bool:
        logger.debug(f"Entered into _handle_slash_command: {command}")
        parts = command.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        if cmd == "/help":
            self._console.print(Panel(
                "[bold]/model[/bold] <name|auto>  -- Switch model\n"
                "[bold]/mode[/bold] <chat|code|research|think|create>  -- Switch mode\n"
                "[bold]/cost[/bold]  -- Show session cost\n"
                "[bold]/new[/bold]  -- Start new session\n"
                "[bold]/sessions[/bold]  -- List sessions\n"
                "[bold]/clear[/bold]  -- Clear conversation\n"
                "[bold]/help[/bold]  -- Show this help\n"
                "[bold]/quit[/bold]  -- Exit",
                title="Commands",
                border_style="blue",
            ))
            return True

        if cmd == "/model":
            if arg:
                if arg == "auto":
                    self._router.force_model(None)
                    self._console.print("[green]v[/green] Switched to auto model routing.")
                else:
                    self._router.force_model(arg)
                    self._console.print(f"[green]v[/green] Model set to: [cyan]{arg}[/cyan]")
            else:
                current = self._forced_model or "auto"
                self._console.print(f"Current model: [cyan]{current}[/cyan]")
            return True

        if cmd == "/mode":
            if arg and arg in ("chat", "code", "research", "think", "create"):
                self._mode = arg
                self._console.print(f"[green]v[/green] Mode: [cyan]{arg}[/cyan]")
            else:
                self._console.print(f"Current mode: [cyan]{self._mode}[/cyan]")
            return True

        if cmd == "/cost":
            self._console.print(
                f"Session cost: [cyan]{self._total_cost_dt:.1f} DT[/cyan] "
                f"(${self._total_cost_dt / 1000:.4f})\n"
                f"Tokens: {self._total_tokens_in:,} in / {self._total_tokens_out:,} out"
            )
            return True

        if cmd == "/new":
            self._messages.clear()
            self._session_id = self._session_mgr.create_session(mode=self._mode)
            self._console.print("[green]v[/green] New session started.")
            return True

        if cmd == "/sessions":
            sessions = self._session_mgr.list_sessions()
            for s in sessions:
                marker = "->" if s["id"] == self._session_id else "  "
                self._console.print(
                    f" {marker} [dim]{s['id'][:8]}[/dim]  {s['title']}  [dim]{s['updated_at']}[/dim]"
                )
            return True

        if cmd == "/clear":
            self._messages.clear()
            self._console.print("[green]v[/green] Conversation cleared.")
            return True

        if cmd in ("/quit", "/exit"):
            raise EOFError()

        self._console.print(f"[yellow]Unknown command: {cmd}[/yellow] -- type /help")
        return True

    async def send_message(self, user_input: str, interactive: bool = True) -> None:
        logger.debug(f"Entered into send_message: msg_len={len(user_input)}, interactive={interactive}")

        self._messages.append(ChatMessage(role="user", content=user_input))

        if self._session_mgr and self._session_id:
            self._session_mgr.add_message(self._session_id, "user", user_input)

        decision = self._router.route(user_input, mode=self._mode)

        if interactive:
            self._console.print(f"\n[dim]Model: {decision.model} ({decision.reason})[/dim]")

        full_response = ""
        tokens_in = 0
        tokens_out = 0
        start_time = time.monotonic()

        try:
            if interactive:
                self._console.print()
                with Live(
                    Text("...", style="cyan"),
                    console=self._console,
                    refresh_per_second=15,
                    transient=True,
                ) as live:
                    async for event in self._client.chat_completion_stream(
                        messages=self._messages,
                        model=decision.model,
                    ):
                        if event.event_type == "content":
                            full_response += event.data
                            try:
                                live.update(Markdown(full_response + " |"))
                            except Exception:
                                live.update(Text(full_response + " |"))
                        elif event.event_type == "usage" and isinstance(event.data, dict):
                            tokens_in = event.data.get("prompt_tokens", 0)
                            tokens_out = event.data.get("completion_tokens", 0)
                        elif event.event_type == "error":
                            msg = (
                                event.data.get("message", str(event.data))
                                if isinstance(event.data, dict)
                                else str(event.data)
                            )
                            live.update(Text(f"Error: {msg}", style="red"))
                            self._messages.pop()
                            return

                if full_response:
                    self._console.print(Markdown(full_response))
            else:
                async for event in self._client.chat_completion_stream(
                    messages=self._messages,
                    model=decision.model,
                ):
                    if event.event_type == "content":
                        full_response += event.data
                        sys.stdout.write(event.data)
                        sys.stdout.flush()
                    elif event.event_type == "usage" and isinstance(event.data, dict):
                        tokens_in = event.data.get("prompt_tokens", 0)
                        tokens_out = event.data.get("completion_tokens", 0)
                    elif event.event_type == "error":
                        msg = (
                            event.data.get("message", str(event.data))
                            if isinstance(event.data, dict)
                            else str(event.data)
                        )
                        sys.stderr.write(f"\nError: {msg}\n")
                        self._messages.pop()
                        return
                if full_response:
                    sys.stdout.write("\n")

        except KeyboardInterrupt:
            self._console.print("\n[dim]Generation cancelled.[/dim]")
            self._messages.pop()
            return

        elapsed_ms = int((time.monotonic() - start_time) * 1000)

        if full_response:
            self._messages.append(ChatMessage(role="assistant", content=full_response))

            cost_dt = tokens_in * 0.001 + tokens_out * 0.002
            self._total_cost_dt += cost_dt
            self._total_tokens_in += tokens_in
            self._total_tokens_out += tokens_out

            if self._session_mgr and self._session_id:
                self._session_mgr.add_message(
                    self._session_id,
                    "assistant",
                    full_response,
                    model=decision.model,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    cost_dt=cost_dt,
                )
                if len(self._messages) == 2:
                    title = user_input[:60] + ("..." if len(user_input) > 60 else "")
                    self._session_mgr.update_title(self._session_id, title)

            if interactive:
                self._console.print(
                    f"\n[dim]{tokens_in + tokens_out:,} tokens | {elapsed_ms}ms | "
                    f"{cost_dt:.1f} DT[/dim]\n"
                )
