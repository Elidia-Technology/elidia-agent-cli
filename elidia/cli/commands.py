import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class SlashCommand:
    name: str
    description: str
    usage: str = ""
    handler: Callable[..., Any] | None = None
    category: str = "general"


class CommandRegistry:
    """Registry of slash commands available in the REPL."""

    def __init__(self) -> None:
        logger.debug("Entered into CommandRegistry.__init__")
        self._commands: dict[str, SlashCommand] = {}

    def register(self, cmd: SlashCommand) -> None:
        logger.debug(f"Entered into register: name={cmd.name}")
        self._commands[cmd.name] = cmd

    def get(self, name: str) -> SlashCommand | None:
        logger.debug(f"Entered into get: name={name}")
        clean = name.lstrip("/").lower()
        return self._commands.get(clean)

    def list_commands(self) -> list[SlashCommand]:
        logger.debug("Entered into list_commands")
        return sorted(self._commands.values(), key=lambda c: c.name)

    def list_by_category(self) -> dict[str, list[SlashCommand]]:
        logger.debug("Entered into list_by_category")
        cats: dict[str, list[SlashCommand]] = {}
        for cmd in self._commands.values():
            cats.setdefault(cmd.category, []).append(cmd)
        for v in cats.values():
            v.sort(key=lambda c: c.name)
        return cats


def build_default_commands() -> CommandRegistry:
    logger.debug("Entered into build_default_commands")
    registry = CommandRegistry()

    registry.register(SlashCommand(
        name="help", description="Show available commands",
        usage="/help [command]", category="general",
    ))
    registry.register(SlashCommand(
        name="model", description="Switch or show current model",
        usage="/model <name|auto>", category="general",
    ))
    registry.register(SlashCommand(
        name="mode", description="Switch conversation mode",
        usage="/mode <chat|code|research|think|create>", category="general",
    ))
    registry.register(SlashCommand(
        name="cost", description="Show session token usage and cost",
        usage="/cost", category="general",
    ))
    registry.register(SlashCommand(
        name="new", description="Start a new conversation session",
        usage="/new", category="session",
    ))
    registry.register(SlashCommand(
        name="sessions", description="List recent sessions",
        usage="/sessions", category="session",
    ))
    registry.register(SlashCommand(
        name="clear", description="Clear current conversation history",
        usage="/clear", category="session",
    ))
    registry.register(SlashCommand(
        name="tools", description="List available tools",
        usage="/tools [category]", category="tools",
    ))
    registry.register(SlashCommand(
        name="mcp", description="Show MCP server status",
        usage="/mcp [connect|disconnect <name>]", category="tools",
    ))
    registry.register(SlashCommand(
        name="persona", description="Switch domain persona",
        usage="/persona <slug|list|off>", category="agent",
    ))
    registry.register(SlashCommand(
        name="trust", description="Show progressive trust stats",
        usage="/trust", category="agent",
    ))
    registry.register(SlashCommand(
        name="quit", description="Exit Elidia",
        usage="/quit", category="general",
    ))
    registry.register(SlashCommand(
        name="exit", description="Exit Elidia",
        usage="/exit", category="general",
    ))
    registry.register(SlashCommand(
        name="balance", description="Show API credit balance",
        usage="/balance", category="general",
    ))
    registry.register(SlashCommand(
        name="memory", description="View, search, save, or forget memories",
        usage="/memory [list|search <query>|save key=value|forget <key>]",
        category="memory",
    ))
    registry.register(SlashCommand(
        name="history", description="Search chat history across sessions",
        usage="/history [search query]", category="session",
    ))
    registry.register(SlashCommand(
        name="rules", description="Show project rules (.elidia/rules.md)",
        usage="/rules", category="general",
    ))

    return registry
