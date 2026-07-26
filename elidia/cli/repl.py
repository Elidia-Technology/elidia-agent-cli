import logging
import sys
import time
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.history import FileHistory
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

from elidia.agent.loop import AgentLoop
from elidia.agent.personas import PersonaEngine
from elidia.api.client import AiUtilsClient, ChatMessage, extract_text
from elidia.auth.keychain import get_api_key, mask_api_key
from elidia.cache.lru import ResponseCache
from elidia.cli.commands import CommandRegistry, build_default_commands
from elidia.cli.pager import AutoPager
from elidia.cli.themes import ThemeManager
from elidia.config.defaults import VERSION
from elidia.config.rules import load_project_rules
from elidia.config.settings import ELIDIA_HOME, ElidiaConfig, ensure_elidia_home, load_config
from elidia.creative.audio import generate_music, generate_speech, list_audio_models
from elidia.creative.display import display_image
from elidia.creative.image import generate_image, list_image_models
from elidia.creative.video import generate_video, list_video_models
from elidia.daemon.manager import DaemonManager
from elidia.db.database import Database
from elidia.mcp.registry import MCPRegistry
from elidia.memory.auto import AutoMemory
from elidia.memory.store import MemoryStore, MemoryTier
from elidia.memory.outcomes import OutcomeTracker
from elidia.memory.patterns import PatternLearner
from elidia.memory.compaction import SessionCompactor
from elidia.models.adaptive import AdaptiveRouter
from elidia.models.router import ModelRouter
from elidia.modes.budget import BudgetGovernor
from elidia.modes.thinking import ThinkingLevel, describe_level, parse_thinking_level
from elidia.permissions.audit import AuditLogger
from elidia.permissions.manager import PermissionManager
from elidia.permissions.trust import TrustEngine
from elidia.research.export import export_html, export_markdown
from elidia.research.orchestrator import ResearchOrchestrator
from elidia.research.sources import ResearchSources
from elidia.session.history import HistorySearch
from elidia.session.manager import SessionManager
from elidia.tools import ToolRegistry, create_default_registry
from elidia.widgets.renderer import CliWidgetRenderer
from elidia.cli.renderer import ResponseRenderer, render_success, render_error
from elidia.workflow.engine import WorkflowExecutor, parse_workflow

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

        self._tool_registry: ToolRegistry | None = None
        self._mcp_registry: MCPRegistry | None = None
        self._audit: AuditLogger | None = None
        self._permission_mgr: PermissionManager | None = None
        self._trust_engine: TrustEngine | None = None
        self._persona_engine: PersonaEngine | None = None
        self._agent_loop: AgentLoop | None = None
        self._command_registry: CommandRegistry | None = None
        self._memory_store: MemoryStore | None = None
        self._auto_memory: AutoMemory | None = None
        self._history_search: HistorySearch | None = None
        self._project_rules: str | None = None

        self._budget: BudgetGovernor | None = None
        self._thinking_level: ThinkingLevel = ThinkingLevel.MEDIUM
        self._daemon: DaemonManager | None = None
        self._widget_renderer: CliWidgetRenderer | None = None
        self._research_sources: ResearchSources | None = None

        self._theme_manager: ThemeManager | None = None
        self._pager: AutoPager | None = None
        self._cache: ResponseCache | None = None

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

        self._tool_registry = create_default_registry()

        # Portal tool bridge — registers 111 enterprise tools from AiUtils portal
        from elidia.agent.portal import PortalToolBridge
        self._portal_bridge = PortalToolBridge(self._client)
        try:
            await self._portal_bridge.discover_tools()
            self._portal_bridge.register_portal_tools(self._tool_registry)
        except Exception as e:
            logger.warning(f"Portal tool discovery failed (non-fatal): {e}")

        # Semantic tool router — embeds tool descriptions for intelligent routing
        from elidia.tools.tool_router import SemanticToolRouter
        from elidia.memory.embeddings import EmbeddingClient
        api_key = get_api_key()
        if api_key:
            tool_embedder = EmbeddingClient(api_key=api_key)
            self._tool_router = SemanticToolRouter(embedding_client=tool_embedder)
            try:
                tool_entries = [
                    {"name": t.name, "description": t.description,
                     "category": getattr(t, "category", ""), "parameters": t.parameters}
                    for t in self._tool_registry.list_tools()
                ]
                await self._tool_router.index_tools(tool_entries)
            except Exception as e:
                logger.warning(f"Tool indexing failed (non-fatal): {e}")

        self._audit = AuditLogger()
        self._audit.open()

        self._trust_engine = TrustEngine(config=self._config.permissions)

        self._permission_mgr = PermissionManager(
            config=self._config.permissions,
            audit=self._audit,
            prompt_fn=self._prompt_user_permission,
            trust_engine=self._trust_engine,
        )
        self._permission_mgr.set_project_root(Path.cwd())

        self._mcp_registry = MCPRegistry()
        try:
            await self._mcp_registry.load_and_connect()
            connected = self._mcp_registry.get_connected_servers()
            if connected:
                logger.info(f"MCP servers connected: {connected}")
        except Exception as e:
            logger.warning(f"MCP initialization failed (non-fatal): {e}")

        self._persona_engine = PersonaEngine()
        self._command_registry = build_default_commands()

        self._memory_store = MemoryStore()
        self._memory_store.open()
        self._auto_memory = AutoMemory(self._memory_store)

        # Adaptive model routing — learns which model works best per task type
        self._outcome_tracker = OutcomeTracker(self._memory_store)
        self._pattern_learner = PatternLearner(self._outcome_tracker)
        self._adaptive_router = AdaptiveRouter(self._router, self._pattern_learner, self._outcome_tracker)

        # Session compaction — summarizes session into persistent memory on /new
        self._compactor = SessionCompactor(self._memory_store, self._client)
        self._history_search = HistorySearch(self._db)

        self._project_rules = load_project_rules()

        self._budget = BudgetGovernor(
            session_limit_dt=self._config.budget.session_limit if hasattr(self._config, "budget") else 50000.0,
        )
        self._daemon = DaemonManager()
        self._research_sources = ResearchSources(mcp_registry=self._mcp_registry)

        self._theme_manager = ThemeManager()
        theme_name = getattr(self._config, "theme", None)
        if theme_name and isinstance(theme_name, str):
            self._theme_manager.set_theme(theme_name)
        self._console = self._theme_manager.create_console()
        self._widget_renderer = CliWidgetRenderer(console=self._console)
        self._response_renderer = ResponseRenderer(console=self._console)

        self._pager = AutoPager(console=self._console)
        self._cache = ResponseCache(max_size=256, default_ttl=600)

        self._agent_loop = AgentLoop(
            client=self._client,
            tool_registry=self._tool_registry,
            mcp_registry=self._mcp_registry,
            model_router=self._adaptive_router,
            permission_manager=self._permission_mgr,
            audit=self._audit,
            budget=self._budget,
            thinking_level=self._thinking_level,
            memory_store=self._memory_store,
            persona_engine=self._persona_engine,
            project_path=str(Path.cwd()),
        )

    async def cleanup(self) -> None:
        logger.debug("Entered into cleanup")
        from elidia.tools.browser import close_browser_session
        from elidia.tools.database import close_database_session
        from elidia.tools.rag import close_rag_session
        await close_browser_session()
        close_database_session()
        close_rag_session()
        if self._daemon:
            await self._daemon.stop()
        if self._mcp_registry:
            await self._mcp_registry.disconnect_all()
        if self._client:
            await self._client.close()
        if self._memory_store:
            self._memory_store.close()
        if self._audit:
            self._audit.close()
        if self._db:
            self._db.close()

    def _prompt_user_permission(self, description: str) -> bool:
        logger.debug(f"Entered into _prompt_user_permission: {description}")
        self._console.print(f"\n[yellow]Permission required:[/yellow] {description}")
        try:
            answer = input("  Allow? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        approved = answer in ("y", "yes")
        if self._trust_engine:
            action = description.split(":")[0].strip() if ":" in description else description
            self._trust_engine.record_decision(action, approved)
        return approved

    def _print_banner(self) -> None:
        logger.debug("Entered into _print_banner")
        api_key = get_api_key() or (self._config.api.key if self._config else "")
        masked = mask_api_key(api_key) if api_key else "not set"
        model = self._forced_model or "auto"
        msg_count = len(self._messages)

        tool_count = len(self._tool_registry.list_tools()) if self._tool_registry else 0
        mcp_servers = self._mcp_registry.get_connected_servers() if self._mcp_registry else {}
        mcp_tool_count = sum(mcp_servers.values())
        persona = self._persona_engine.active.name if self._persona_engine and self._persona_engine.active else "none"

        banner = Text()
        banner.append(f"ELIDIA v{VERSION}", style="bold cyan")
        banner.append(f" --- {masked}", style="dim")
        banner.append(f"\n Model: {model}", style="")
        banner.append(f" | Mode: {self._mode}", style="")
        banner.append(f" | Persona: {persona}", style="")
        banner.append(f"\n Tools: {tool_count} built-in", style="dim")
        if mcp_servers:
            banner.append(f" + {mcp_tool_count} MCP ({len(mcp_servers)} servers)", style="dim")
        banner.append(f" | Messages: {msg_count}", style="dim")

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
                    handled = await self._handle_slash_command(user_input)
                    if handled:
                        continue

                await self.send_message(user_input, interactive=True)
            except EOFError:
                self._console.print("\n[dim]Goodbye.[/dim]")
                break

    async def _handle_slash_command(self, command: str) -> bool:
        logger.debug(f"Entered into _handle_slash_command: {command}")
        parts = command.split(maxsplit=1)
        cmd = parts[0].lower().lstrip("/")
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd == "help":
            return self._cmd_help(arg)
        if cmd == "model":
            return self._cmd_model(arg)
        if cmd == "mode":
            return self._cmd_mode(arg)
        if cmd == "cost":
            return self._cmd_cost()
        if cmd == "new":
            return self._cmd_new()
        if cmd == "sessions":
            return self._cmd_sessions()
        if cmd == "clear":
            return self._cmd_clear()
        if cmd == "tools":
            return self._cmd_tools(arg)
        if cmd == "mcp":
            return await self._cmd_mcp(arg)
        if cmd == "persona":
            return self._cmd_persona(arg)
        if cmd == "trust":
            return self._cmd_trust()
        if cmd == "balance":
            return await self._cmd_balance()
        if cmd == "memory":
            return self._cmd_memory(arg)
        if cmd == "history":
            return self._cmd_history(arg)
        if cmd == "rules":
            return self._cmd_rules()
        if cmd == "think":
            return self._cmd_think(arg)
        if cmd == "budget":
            return self._cmd_budget()
        if cmd == "research":
            return await self._cmd_research(arg)
        if cmd == "create":
            return await self._cmd_create(arg)
        if cmd == "workflow":
            return await self._cmd_workflow(arg)
        if cmd == "daemon":
            return await self._cmd_daemon(arg)
        if cmd == "theme":
            return self._cmd_theme(arg)
        if cmd == "cache":
            return self._cmd_cache(arg)
        if cmd == "pager":
            return self._cmd_pager(arg)
        if cmd == "image":
            return await self._cmd_image(arg)
        if cmd == "rag":
            return await self._cmd_rag(arg)
        if cmd in ("quit", "exit"):
            raise EOFError()

        self._console.print(f"[yellow]Unknown command: /{cmd}[/yellow] -- type /help")
        return True

    def _cmd_help(self, arg: str) -> bool:
        if arg and self._command_registry:
            cmd_def = self._command_registry.get(arg)
            if cmd_def:
                self._console.print(f"[bold]/{cmd_def.name}[/bold] -- {cmd_def.description}")
                if cmd_def.usage:
                    self._console.print(f"  Usage: {cmd_def.usage}")
                return True

        cats = self._command_registry.list_by_category() if self._command_registry else {}
        lines: list[str] = []
        for cat, cmds in cats.items():
            lines.append(f"\n[bold]{cat.title()}[/bold]")
            for c in cmds:
                lines.append(f"  [bold]/{c.name:12s}[/bold] {c.description}")
        self._console.print(Panel("\n".join(lines), title="Commands", border_style="blue"))
        return True

    def _cmd_model(self, arg: str) -> bool:
        if arg:
            if arg == "auto":
                self._router.force_model(None)
                self._forced_model = None
                self._console.print("[green]v[/green] Switched to auto model routing.")
            else:
                self._router.force_model(arg)
                self._forced_model = arg
                self._console.print(f"[green]v[/green] Model set to: [cyan]{arg}[/cyan]")
        else:
            current = self._forced_model or "auto"
            self._console.print(f"Current model: [cyan]{current}[/cyan]")
        return True

    async def _cmd_image(self, arg: str) -> bool:
        """Usage: /image <path> [message] -- attach an image and ask about it."""
        logger.debug(f"Entered into _cmd_image: arg={arg}")
        if not arg:
            self._console.print("[yellow]Usage: /image <path> [message][/yellow]")
            return True

        parts = arg.split(maxsplit=1)
        image_path = parts[0]
        message = parts[1] if len(parts) > 1 else "What's in this image?"

        try:
            self._console.print(f"[dim]Uploading {image_path}...[/dim]")
            url = await self._client.upload_image(image_path)
        except ValueError as e:
            self._console.print(f"[red]Could not attach image: {e}[/red]")
            return True
        except Exception as e:
            self._console.print(f"[red]Image upload failed: {e}[/red]")
            return True

        await self.send_message(message, interactive=True, image_urls=[url])
        return True

    def _cmd_mode(self, arg: str) -> bool:
        valid_modes = ("chat", "code", "research", "think", "create")
        if arg and arg in valid_modes:
            self._mode = arg
            self._console.print(f"[green]v[/green] Mode: [cyan]{arg}[/cyan]")
        elif arg:
            self._console.print(f"[yellow]Invalid mode.[/yellow] Choose: {', '.join(valid_modes)}")
        else:
            self._console.print(f"Current mode: [cyan]{self._mode}[/cyan]")
        return True

    def _cmd_cost(self) -> bool:
        self._console.print(
            f"Session cost: [cyan]{self._total_cost_dt:.1f} DT[/cyan] "
            f"(${self._total_cost_dt / 1000:.4f})\n"
            f"Tokens: {self._total_tokens_in:,} in / {self._total_tokens_out:,} out"
        )
        return True

    def _cmd_new(self) -> bool:
        from elidia.tools.browser import close_browser_session
        from elidia.tools.database import close_database_session
        from elidia.tools.rag import close_rag_session
        asyncio.ensure_future(close_browser_session())
        close_database_session()
        close_rag_session()

        # Compact current session before starting a new one
        if self._compactor and len(self._messages) >= 4:
            asyncio.ensure_future(
                self._compactor.compact_session(
                    messages=list(self._messages),
                    session_id=self._session_id or "",
                    project_path=str(Path.cwd()),
                )
            )

        self._messages.clear()
        if self._session_mgr:
            self._session_id = self._session_mgr.create_session(mode=self._mode)
        if self._permission_mgr:
            self._permission_mgr.reset_session()
        self._console.print("[green]v[/green] New session started.")
        return True

    def _cmd_sessions(self) -> bool:
        if not self._session_mgr:
            return True
        sessions = self._session_mgr.list_sessions()
        for s in sessions:
            marker = "->" if s["id"] == self._session_id else "  "
            self._console.print(
                f" {marker} [dim]{s['id'][:8]}[/dim]  {s['title']}  [dim]{s['updated_at']}[/dim]"
            )
        return True

    def _cmd_clear(self) -> bool:
        self._messages.clear()
        self._console.print("[green]v[/green] Conversation cleared.")
        return True

    def _cmd_tools(self, arg: str) -> bool:
        if not self._tool_registry:
            return True

        tools = self._tool_registry.list_tools()
        if arg:
            tools = [t for t in tools if t.category == arg]

        categories: dict[str, list[str]] = {}
        for t in tools:
            categories.setdefault(t.category, []).append(f"  {t.name:20s} {t.description}")

        lines: list[str] = []
        for cat, items in sorted(categories.items()):
            lines.append(f"\n[bold]{cat}[/bold]")
            lines.extend(items)

        if self._mcp_registry:
            mcp_tools = self._mcp_registry.list_all_tools()
            if mcp_tools:
                lines.append("\n[bold]mcp[/bold]")
                for t in mcp_tools:
                    desc = t.description[:50] if t.description else ""
                    lines.append(f"  {t.server_name}__{t.name:20s} {desc}")

        total = len(tools) + (len(self._mcp_registry.list_all_tools()) if self._mcp_registry else 0)
        self._console.print(Panel("\n".join(lines), title=f"Tools ({total})", border_style="green"))
        return True

    async def _cmd_mcp(self, arg: str) -> bool:
        if not self._mcp_registry:
            self._console.print("[dim]MCP not initialized.[/dim]")
            return True

        if not arg:
            servers = self._mcp_registry.get_connected_servers()
            if servers:
                for name, tool_count in servers.items():
                    self._console.print(f"  [green]connected[/green] {name} ({tool_count} tools)")
            else:
                self._console.print("[dim]No MCP servers connected.[/dim]")
            return True

        parts = arg.split(maxsplit=1)
        action = parts[0]
        name = parts[1] if len(parts) > 1 else ""

        if action == "disconnect" and name:
            await self._mcp_registry.disconnect_server(name)
            self._console.print(f"[green]v[/green] Disconnected MCP server: {name}")
        else:
            self._console.print("[yellow]Usage: /mcp [disconnect <name>][/yellow]")
        return True

    def _cmd_persona(self, arg: str) -> bool:
        if not self._persona_engine:
            return True

        if not arg or arg == "list":
            personas = self._persona_engine.list_personas()
            active = self._persona_engine.active
            for p in personas:
                marker = "->" if active and active.slug == p.slug else "  "
                self._console.print(f" {marker} [bold]{p.slug:12s}[/bold] {p.name} -- {p.system_prompt[:60]}...")
            return True

        if arg == "off":
            self._persona_engine.deactivate()
            self._console.print("[green]v[/green] Persona deactivated.")
            return True

        persona = self._persona_engine.activate(arg)
        if persona:
            self._console.print(f"[green]v[/green] Persona: [cyan]{persona.name}[/cyan]")
            if persona.greeting:
                self._console.print(f"[dim]{persona.greeting}[/dim]")
        else:
            self._console.print(f"[yellow]Unknown persona: {arg}[/yellow] -- type /persona list")
        return True

    def _cmd_trust(self) -> bool:
        if not self._trust_engine:
            return True
        stats = self._trust_engine.get_stats()
        if not stats:
            self._console.print("[dim]No trust data yet.[/dim]")
            return True
        for action, s in stats.items():
            status = "[green]promoted[/green]" if s.auto_promoted else "[dim]tracking[/dim]"
            self._console.print(f"  {action:25s} approved={s.approved_count} denied={s.denied_count} {status}")
        return True

    async def _cmd_balance(self) -> bool:
        if not self._client:
            return True
        balance = await self._client.get_balance()
        if balance.get("balance_dt", -1) >= 0:
            self._console.print(f"Balance: [cyan]{balance['balance_dt']:,.0f} DT[/cyan] (${balance['balance_dt'] / 1000:.2f})")
        else:
            self._console.print(f"[yellow]Could not fetch balance: {balance.get('error', 'unknown')}[/yellow]")
        return True

    def _cmd_memory(self, arg: str) -> bool:
        if not self._memory_store:
            self._console.print("[dim]Memory not initialized.[/dim]")
            return True

        parts = arg.split(maxsplit=1)
        action = parts[0] if parts else "list"
        rest = parts[1] if len(parts) > 1 else ""

        if action == "search" and rest:
            results = self._memory_store.search_text(rest, limit=10)
            if not results:
                self._console.print(f"[dim]No memories matching '{rest}'[/dim]")
            for m in results:
                tier = MemoryTier(m.tier).name
                self._console.print(f"  [{tier}] [bold]{m.key}[/bold]: {m.content[:80]}")
            return True

        if action == "save" and rest:
            sep_idx = rest.find("=")
            if sep_idx > 0:
                key = rest[:sep_idx].strip()
                content = rest[sep_idx + 1:].strip()
                from elidia.memory.store import MemoryEntry
                mid = self._memory_store.save(MemoryEntry(
                    tier=MemoryTier.USER, key=key, content=content,
                    session_id=self._session_id or "", source="user_command",
                ))
                self._console.print(f"[green]v[/green] Saved memory: {key} ({mid[:8]})")
            else:
                self._console.print("[yellow]Usage: /memory save key=value[/yellow]")
            return True

        if action == "forget" and rest:
            count = self._memory_store.delete_by_key(rest)
            self._console.print(f"[green]v[/green] Deleted {count} memories with key '{rest}'")
            return True

        if action == "list" or not arg:
            tier_filter = None
            if rest:
                try:
                    tier_filter = MemoryTier[rest.upper()]
                except KeyError:
                    pass
            memories = self._memory_store.list_memories(tier=tier_filter, limit=20)
            if not memories:
                self._console.print("[dim]No memories stored.[/dim]")
            for m in memories:
                tier = MemoryTier(m.tier).name
                self._console.print(f"  [{tier}] [bold]{m.key}[/bold]: {m.content[:80]}")
            total = self._memory_store.count()
            self._console.print(f"\n[dim]Total: {total} memories[/dim]")
            return True

        self._console.print("[yellow]Usage: /memory [list|search <query>|save key=value|forget <key>][/yellow]")
        return True

    def _cmd_history(self, arg: str) -> bool:
        if not self._history_search:
            self._console.print("[dim]History not available.[/dim]")
            return True

        if not arg:
            stats = self._history_search.get_session_stats()
            for s in stats[:15]:
                self._console.print(
                    f"  [dim]{s['id'][:8]}[/dim] {s['title'][:40]:40s} "
                    f"[dim]{s['message_count']} msgs | {s['updated_at']}[/dim]"
                )
            return True

        results = self._history_search.search(arg, limit=10)
        if not results:
            self._console.print(f"[dim]No messages matching '{arg}'[/dim]")
            return True

        for r in results:
            role = r["role"].upper()
            preview = r["content"][:100].replace("\n", " ")
            self._console.print(
                f"  [dim]{r['session_id'][:8]}[/dim] [{role}] {preview}"
            )
        return True

    def _cmd_rules(self) -> bool:
        if self._project_rules:
            self._console.print(Panel(self._project_rules, title="Project Rules", border_style="blue"))
        else:
            self._console.print("[dim]No project rules found (.elidia/rules.md)[/dim]")
        return True

    def _cmd_think(self, arg: str) -> bool:
        logger.debug(f"Entered into _cmd_think: arg={arg}")
        if not arg:
            desc = describe_level(self._thinking_level)
            self._console.print(f"Thinking level: [cyan]{desc}[/cyan]")
            return True

        try:
            level = parse_thinking_level(arg)
            self._thinking_level = level
            if self._agent_loop:
                self._agent_loop._thinking_level = level
            desc = describe_level(level)
            self._console.print(f"[green]v[/green] Thinking level: [cyan]{desc}[/cyan]")
        except ValueError:
            self._console.print(
                "[yellow]Invalid level.[/yellow] Choose: minimal, low, medium, high, max (or 1-5)"
            )
        return True

    def _cmd_budget(self) -> bool:
        logger.debug("Entered into _cmd_budget")
        if not self._budget:
            self._console.print("[dim]Budget not initialized.[/dim]")
            return True

        summary = self._budget.get_summary()
        self._console.print(Panel(
            f"Session: [cyan]{summary['session_dt_used']:.1f}[/cyan] / "
            f"{summary['session_limit_dt']:.0f} DT ({summary['session_pct']:.1f}%)\n"
            f"Tokens: {summary['session_tokens_in']:,} in / {summary['session_tokens_out']:,} out\n"
            f"Calls: {summary['call_count']}",
            title="Budget",
            border_style="yellow",
        ))
        return True

    async def _cmd_research(self, arg: str) -> bool:
        logger.debug(f"Entered into _cmd_research: arg={arg}")
        if not arg:
            self._console.print("[yellow]Usage: /research <query> [--export md|html][/yellow]")
            return True

        export_format = None
        query = arg
        if " --export " in arg:
            parts = arg.split(" --export ", 1)
            query = parts[0].strip()
            export_format = parts[1].strip().lower()

        if not self._client:
            self._console.print("[red]No client initialized.[/red]")
            return True

        self._console.print(f"[cyan]Researching:[/cyan] {query}")

        async def search_fn(q: str):
            if self._research_sources:
                return await self._research_sources.search(q)
            return []

        orchestrator = ResearchOrchestrator(client=self._client, search_fn=search_fn)

        from elidia.cli.progress import StageTracker
        research_stages = ["Decompose", "Search", "Analyze", "Synthesize", "Verify"]
        tracker = StageTracker(console=self._console, stages=research_stages)
        stage_map = {
            "decompose": 0, "search": 1, "analyze": 2,
            "synthesize": 3, "verify": 4,
        }

        tracker.start()
        try:
            async for event in orchestrator.run(query):
                idx = stage_map.get(event.kind, -1)
                if idx >= 0:
                    tracker.advance(stage_index=idx)
                elif event.kind == "result":
                    tracker.complete()
                    report = event.data.get("report", "")
                    sources = event.data.get("sources", [])

                    if export_format == "html":
                        result = export_html(report, sources=sources)
                        output_path = ELIDIA_HOME / "research" / f"report_{int(time.time())}.html"
                        output_path.parent.mkdir(parents=True, exist_ok=True)
                        output_path.write_text(result.content, encoding="utf-8")
                        self._console.print(f"\n[green]v[/green] Saved: {output_path}")
                    elif export_format == "md":
                        result = export_markdown(report, sources=sources)
                        output_path = ELIDIA_HOME / "research" / f"report_{int(time.time())}.md"
                        output_path.parent.mkdir(parents=True, exist_ok=True)
                        output_path.write_text(result.content, encoding="utf-8")
                        self._console.print(f"\n[green]v[/green] Saved: {output_path}")
                    else:
                        self._console.print()
                        self._console.print(Markdown(report))
                        if sources:
                            self._console.print(f"\n[dim]{len(sources)} source(s) cited[/dim]")
                elif event.kind == "error":
                    self._console.print(f"[red]Research error: {event.data}[/red]")
        except Exception as e:
            self._console.print(f"[red]Research failed: {e}[/red]")

        return True

    async def _cmd_create(self, arg: str) -> bool:
        logger.debug(f"Entered into _cmd_create: arg={arg}")
        if not arg:
            self._console.print(
                "[yellow]Usage:[/yellow]\n"
                "  /create image <prompt> [--model <model>]\n"
                "  /create video <prompt> [--model <model>]\n"
                "  /create speech <text> [--voice <voice>]\n"
                "  /create music <prompt>\n"
                "  /create models"
            )
            return True

        parts = arg.split(maxsplit=1)
        sub_cmd = parts[0].lower()
        rest = parts[1] if len(parts) > 1 else ""

        if not self._client:
            self._console.print("[red]No client initialized.[/red]")
            return True

        if sub_cmd == "models":
            self._console.print("[bold]Image models:[/bold]")
            for m in list_image_models():
                self._console.print(f"  {m['model']:25s} {m.get('vendor', '')}")
            self._console.print("[bold]Video models:[/bold]")
            for m in list_video_models():
                self._console.print(f"  {m['model']:25s} {m.get('vendor', '')}")
            self._console.print("[bold]Audio models:[/bold]")
            for m in list_audio_models():
                self._console.print(f"  {m['model']:25s} {m.get('vendor', '')}")
            return True

        model = None
        voice = None
        prompt = rest
        if " --model " in rest:
            prompt, model = rest.rsplit(" --model ", 1)
            prompt = prompt.strip()
            model = model.strip()
        if " --voice " in rest:
            prompt, voice = rest.rsplit(" --voice ", 1)
            prompt = prompt.strip()
            voice = voice.strip()

        if not prompt:
            self._console.print("[yellow]Prompt required.[/yellow]")
            return True

        try:
            if sub_cmd == "image":
                self._console.print("[cyan]Generating image...[/cyan]")
                result = await generate_image(self._client, prompt, model=model)
                self._console.print(f"[green]v[/green] Saved: {result.file_path}")
                display_image(result.file_path)

            elif sub_cmd == "video":
                self._console.print("[cyan]Generating video (this may take a few minutes)...[/cyan]")
                result = await generate_video(self._client, prompt, model=model)
                self._console.print(f"[green]v[/green] Saved: {result.file_path}")

            elif sub_cmd == "speech":
                self._console.print("[cyan]Generating speech...[/cyan]")
                result = await generate_speech(self._client, prompt, voice=voice)
                self._console.print(f"[green]v[/green] Saved: {result.file_path}")

            elif sub_cmd == "music":
                self._console.print("[cyan]Generating music...[/cyan]")
                result = await generate_music(self._client, prompt)
                self._console.print(f"[green]v[/green] Saved: {result.file_path}")

            else:
                self._console.print(f"[yellow]Unknown create type: {sub_cmd}[/yellow]")

        except Exception as e:
            self._console.print(f"[red]Creation failed: {e}[/red]")

        return True

    async def _cmd_workflow(self, arg: str) -> bool:
        logger.debug(f"Entered into _cmd_workflow: arg={arg}")
        if not arg:
            self._console.print("[yellow]Usage: /workflow <path_to_yaml>[/yellow]")
            return True

        workflow_path = Path(arg)
        if not workflow_path.exists():
            self._console.print(f"[red]Workflow file not found: {arg}[/red]")
            return True

        try:
            workflow = parse_workflow(workflow_path)
        except Exception as e:
            self._console.print(f"[red]Failed to parse workflow: {e}[/red]")
            return True

        self._console.print(f"[cyan]Running workflow:[/cyan] {workflow.name}")
        executor = WorkflowExecutor(client=self._client)

        try:
            async for event in executor.run(workflow):
                if event.kind == "start":
                    self._console.print(f"  [dim]Steps: {event.data.get('step_count', 0)}[/dim]")
                elif event.kind == "step_start":
                    self._console.print(f"  [cyan]>{event.data['name']}[/cyan] ({event.data['type']})")
                elif event.kind == "step_done":
                    status = event.data.get("status", "?")
                    elapsed = event.data.get("elapsed_ms", 0)
                    style = "green" if status == "completed" else "yellow" if status == "skipped" else "red"
                    self._console.print(f"  [{style}]{status}[/{style}] {event.data['name']} ({elapsed}ms)")
                elif event.kind == "done":
                    total = event.data.get("total_steps", 0)
                    completed = event.data.get("completed", 0)
                    failed = event.data.get("failed", 0)
                    elapsed = event.data.get("elapsed_ms", 0)
                    self._console.print(
                        f"\n[bold]Done:[/bold] {completed}/{total} completed, "
                        f"{failed} failed ({elapsed}ms)"
                    )
                elif event.kind == "error":
                    self._console.print(f"  [red]Error: {event.data}[/red]")
        except Exception as e:
            self._console.print(f"[red]Workflow error: {e}[/red]")

        return True

    async def _cmd_rag(self, arg: str) -> bool:
        logger.debug(f"Entered into _cmd_rag: arg={arg}")
        from elidia.tools.rag import _get_session

        parts = arg.split(maxsplit=1)
        action = parts[0] if parts else ""
        rest = parts[1] if len(parts) > 1 else ""

        if action == "ingest" and rest:
            from pathlib import Path as _Path

            from elidia.rag.ingest import FileIngestPipeline
            from elidia.tools.rag import _ensure_engine

            target = _Path(rest).expanduser()
            if not target.exists():
                self._console.print(f"[red]Path not found: {rest}[/red]")
                return True
            try:
                engine = await _ensure_engine()
            except RuntimeError as e:
                self._console.print(f"[red]{e}[/red]")
                return True
            pipeline = FileIngestPipeline(engine)
            if target.is_dir():
                self._console.print(f"[cyan]Ingesting directory:[/cyan] {target}")
                result = await pipeline.ingest_directory(target)
                self._console.print(
                    f"[green]v[/green] Ingested {result['files']} file(s), "
                    f"{result['chunks']} chunk(s), skipped {result['skipped']}"
                )
            else:
                ids = await pipeline.ingest_file(target)
                if ids:
                    self._console.print(f"[green]v[/green] Ingested {len(ids)} chunk(s) from {target.name}")
                else:
                    self._console.print(f"[yellow]Nothing ingested from {target.name}[/yellow]")
            return True

        if action == "search" and rest:
            from elidia.tools.rag import _rag_search
            result = await _rag_search(rest, limit=5)
            self._console.print(result.content)
            return True

        if action == "list" or (not action and not arg):
            from elidia.tools.rag import _rag_list_sources
            result = await _rag_list_sources()
            self._console.print(result.content)
            return True

        if action == "clear":
            session = _get_session()
            if session.engine is not None:
                n = session.engine.clear_all()
                self._console.print(f"[green]v[/green] Deleted {n} chunk(s) — RAG store is now empty")
            else:
                self._console.print("[dim]RAG store not open this session.[/dim]")
            return True

        self._console.print("[yellow]Usage: /rag [ingest <path>|search <query>|list|clear][/yellow]")
        return True

    async def _cmd_daemon(self, arg: str) -> bool:
        logger.debug(f"Entered into _cmd_daemon: arg={arg}")
        if not self._daemon:
            self._console.print("[dim]Daemon not initialized.[/dim]")
            return True

        parts = arg.split(maxsplit=2) if arg else []
        action = parts[0] if parts else "status"

        if action == "status":
            status = self._daemon.get_status()
            self._console.print(Panel(
                f"Running: {'yes' if status['running'] else 'no'}\n"
                f"Tasks: {status['task_count']} ({status['active']} active)",
                title="Daemon",
                border_style="magenta",
            ))
            for t in status.get("tasks", []):
                style = "green" if t["status"] == "running" else "dim"
                self._console.print(
                    f"  [{style}]{t['status']:8s}[/{style}] {t['name']} ({t['type']}) "
                    f"runs={t['run_count']}"
                )
            return True

        if action == "start":
            await self._daemon.start()
            self._console.print("[green]v[/green] Daemon started.")
            return True

        if action == "stop":
            await self._daemon.stop()
            self._console.print("[green]v[/green] Daemon stopped.")
            return True

        if action == "watch" and len(parts) >= 2:
            watch_path = parts[1]
            name = parts[2] if len(parts) > 2 else Path(watch_path).name
            task_id = self._daemon.add_watcher(name, watch_path)
            self._console.print(f"[green]v[/green] Watcher added: {name} → {watch_path} ({task_id[:16]})")
            return True

        if action == "schedule" and len(parts) >= 2:
            interval = int(parts[1])
            command = parts[2] if len(parts) > 2 else ""
            name = f"sched_{interval}s"
            task_id = self._daemon.add_schedule(name, interval, command)
            self._console.print(f"[green]v[/green] Schedule added: every {interval}s ({task_id[:16]})")
            return True

        self._console.print(
            "[yellow]Usage:[/yellow]\n"
            "  /daemon status\n"
            "  /daemon start\n"
            "  /daemon stop\n"
            "  /daemon watch <path> [name]\n"
            "  /daemon schedule <seconds> [command]"
        )
        return True

    def _cmd_theme(self, arg: str) -> bool:
        logger.debug(f"Entered into _cmd_theme: arg={arg}")
        if not self._theme_manager:
            self._console.print("[dim]Theme manager not initialized.[/dim]")
            return True

        if not arg or arg == "list":
            themes = self._theme_manager.list_themes()
            current = self._theme_manager.current_name
            for t in themes:
                marker = "->" if t["name"] == current else "  "
                self._console.print(f" {marker} {t['name']:12s} {t['description']}")
            return True

        result = self._theme_manager.set_theme(arg)
        if result:
            self._console = self._theme_manager.create_console()
            if self._pager:
                self._pager = AutoPager(console=self._console)
            self._console.print(f"[green]v[/green] Theme: [cyan]{arg}[/cyan]")
        else:
            self._console.print(f"[yellow]Unknown theme: {arg}[/yellow] -- type /theme list")
        return True

    def _cmd_cache(self, arg: str) -> bool:
        logger.debug(f"Entered into _cmd_cache: arg={arg}")
        if not self._cache:
            self._console.print("[dim]Cache not initialized.[/dim]")
            return True

        if arg == "clear":
            self._cache.clear()
            self._console.print("[green]v[/green] Cache cleared.")
            return True

        if arg == "off":
            self._cache.enabled = False
            self._console.print("[green]v[/green] Cache disabled.")
            return True

        if arg == "on":
            self._cache.enabled = True
            self._console.print("[green]v[/green] Cache enabled.")
            return True

        stats = self._cache.get_stats()
        self._console.print(Panel(
            f"Enabled: {'yes' if stats['enabled'] else 'no'}\n"
            f"Size: {stats['size']} / {stats['max_size']}\n"
            f"Hits: {stats['hits']} | Misses: {stats['misses']}\n"
            f"Hit rate: {stats['hit_rate_pct']:.1f}%",
            title="Cache",
            border_style="blue",
        ))
        return True

    def _cmd_pager(self, arg: str) -> bool:
        logger.debug(f"Entered into _cmd_pager: arg={arg}")
        if not self._pager:
            self._console.print("[dim]Pager not initialized.[/dim]")
            return True

        if arg == "off":
            self._pager.enabled = False
            self._console.print("[green]v[/green] Auto-pager disabled.")
            return True

        if arg == "on":
            self._pager.enabled = True
            self._console.print("[green]v[/green] Auto-pager enabled.")
            return True

        status = "enabled" if self._pager.enabled else "disabled"
        self._console.print(f"Auto-pager: [cyan]{status}[/cyan] (threshold: {self._pager.threshold:.0%})")
        return True

    async def send_message(
        self, user_input: str, interactive: bool = True, image_urls: list[str] | None = None,
    ) -> None:
        logger.debug(
            f"Entered into send_message: msg_len={len(user_input)}, "
            f"interactive={interactive}, image_count={len(image_urls or [])}"
        )

        if image_urls:
            content: str | list[dict] = [{"type": "text", "text": user_input}] + [
                {"type": "image_url", "image_url": {"url": u}} for u in image_urls
            ]
        else:
            content = user_input
        self._messages.append(ChatMessage(role="user", content=content))

        if self._session_mgr and self._session_id:
            self._session_mgr.add_message(self._session_id, "user", user_input)

        # Check response cache before dispatching to agent/LLM
        cache_hit = None
        if self._cache and self._cache.enabled:
            cache_model = self._forced_model or "auto"
            cache_key = self._cache.make_key(
                model=cache_model,
                messages=[{"role": "user", "content": user_input}],
            )
            cache_hit = self._cache.get(cache_key)
            if cache_hit and interactive:
                self._console.print("[dim](cached response)[/dim]")

        if cache_hit:
            full_response = cache_hit if isinstance(cache_hit, str) else str(cache_hit)
            self._messages.append(ChatMessage(role="assistant", content=full_response))
            if self._session_mgr and self._session_id:
                self._session_mgr.add_message(self._session_id, "assistant", full_response)
            if interactive:
                try:
                    if self._pager and self._pager.should_page(full_response):
                        self._pager.print_or_page(full_response, as_markdown=True)
                    else:
                        self._response_renderer.render_response(full_response)
                except Exception:
                    self._console.print(full_response)
            return

        if self._auto_memory:
            saved = self._auto_memory.analyze_user_message(
                user_input,
                session_id=self._session_id or "",
                project_path=str(Path.cwd()),
            )
            if saved and interactive:
                for entry in saved:
                    self._console.print(f"[dim]  (remembered: {entry.key})[/dim]")

        if self._agent_loop:
            await self._send_with_agent_loop(user_input, interactive)
        else:
            await self._send_direct(user_input, interactive)

    async def _send_with_agent_loop(self, user_input: str, interactive: bool) -> None:
        logger.debug(f"Entered into _send_with_agent_loop: interactive={interactive}")
        start_time = time.monotonic()
        full_response = ""
        total_tokens_in = 0
        total_tokens_out = 0
        total_cost = 0.0

        tool_count = 0

        try:
            async for event in self._agent_loop.run(
                messages=self._messages,
                mode=self._mode,
                forced_model=self._forced_model,
                session_id=self._session_id or "",
            ):
                if event.kind == "mode_info":
                    if interactive:
                        exec_mode = event.data.get("exec_mode", "direct")
                        confidence = event.data.get("confidence", 0.0)
                        reason = event.data.get("reason", "")
                        if exec_mode != "direct":
                            self._console.print(f"[dim]Mode: {exec_mode} ({confidence:.0%}) — {reason}[/dim]")

                elif event.kind == "budget_warning":
                    if interactive:
                        self._console.print(f"[yellow]Budget: {event.data.get('message', '')}[/yellow]")

                elif event.kind == "thinking":
                    if interactive:
                        model = event.data.get("model", "?")
                        reason = event.data.get("reason", "")
                        self._console.print(f"\n[dim]Model: {model} ({reason})[/dim]")

                elif event.kind == "content":
                    full_response = event.data
                    if interactive:
                        self._console.print()
                        try:
                            if self._pager and self._pager.should_page(full_response):
                                self._pager.print_or_page(full_response, as_markdown=True)
                            else:
                                self._response_renderer.render_response(full_response)
                        except Exception:
                            self._console.print(full_response)
                    else:
                        sys.stdout.write(full_response)
                        sys.stdout.flush()

                elif event.kind == "tool_call":
                    name = event.data.get("name", "?")
                    args = event.data.get("arguments", {})
                    args_preview = str(args)[:100]
                    tool_count += 1
                    if interactive:
                        self._console.print(f"  [cyan]> {name}[/cyan]({args_preview})")

                elif event.kind == "tool_result":
                    name = event.data.get("name", "?")
                    content = event.data.get("content", "")
                    is_error = event.data.get("is_error", False)
                    if interactive:
                        style = "red" if is_error else "dim"
                        preview = content[:200].replace("\n", " ")
                        self._console.print(f"  [{style}]< {name}: {preview}[/{style}]")

                elif event.kind == "usage":
                    total_tokens_in += event.data.get("tokens_in", 0)
                    total_tokens_out += event.data.get("tokens_out", 0)
                    total_cost += event.data.get("cost_dt", 0.0)

                elif event.kind == "error":
                    err_msg = str(event.data)
                    if interactive:
                        self._console.print(f"\n[red]Error: {err_msg}[/red]")
                    else:
                        sys.stderr.write(f"\nError: {err_msg}\n")
                    self._messages.pop()
                    return

                elif event.kind == "done":
                    loops = event.data.get("loops", 0)
                    tools_used = event.data.get("tools_called", [])
                    if interactive and tools_used:
                        self._console.print(f"[dim]  ({loops} loop(s), {len(tools_used)} tool call(s))[/dim]")

        except KeyboardInterrupt:
            self._console.print("\n[dim]Generation cancelled.[/dim]")
            self._messages.pop()
            return

        elapsed_ms = int((time.monotonic() - start_time) * 1000)

        if full_response:
            self._messages.append(ChatMessage(role="assistant", content=full_response))

            self._total_cost_dt += total_cost
            self._total_tokens_in += total_tokens_in
            self._total_tokens_out += total_tokens_out

            if self._session_mgr and self._session_id:
                model_used = self._forced_model or "auto"
                self._session_mgr.add_message(
                    self._session_id,
                    "assistant",
                    full_response,
                    model=model_used,
                    tokens_in=total_tokens_in,
                    tokens_out=total_tokens_out,
                    cost_dt=total_cost,
                )
                if len(self._messages) == 2:
                    first_text = extract_text(self._messages[0].content)
                    title = first_text[:60]
                    if len(first_text) > 60:
                        title += "..."
                    self._session_mgr.update_title(self._session_id, title)

            if self._audit:
                self._audit.log_model_call(
                    model=self._forced_model or "auto",
                    tokens_in=total_tokens_in,
                    tokens_out=total_tokens_out,
                    cost_dt=total_cost,
                    session_id=self._session_id or "",
                )

            # Store in response cache for future identical queries
            if self._cache and self._cache.enabled and full_response:
                cache_model = self._forced_model or "auto"
                cache_key = self._cache.make_key(
                    model=cache_model,
                    messages=[{"role": "user", "content": user_input}],
                )
                self._cache.put(cache_key, full_response)

            if interactive:
                if not full_response.endswith("\n"):
                    self._console.print()
                self._console.print(
                    f"[dim]{total_tokens_in + total_tokens_out:,} tokens | {elapsed_ms}ms | "
                    f"{total_cost:.1f} DT[/dim]\n"
                )
            elif not full_response.endswith("\n"):
                sys.stdout.write("\n")

    async def _send_direct(self, user_input: str, interactive: bool) -> None:
        logger.debug(f"Entered into _send_direct: interactive={interactive}")
        decision = self._router.route(user_input, mode=self._mode)

        # Vision content needs a vision-capable model regardless of what the
        # classifier would otherwise pick — unless the user explicitly
        # forced a model with /model, which still wins.
        last_content = self._messages[-1].content if self._messages else None
        if isinstance(last_content, list) and not self._forced_model:
            from elidia.models.router import RouteDecision
            decision = RouteDecision(
                model=self._router.get_model_for_type("vision"),
                reason="Vision content attached",
                task_type="vision",
            )

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
                    self._response_renderer.render_response(full_response)
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
