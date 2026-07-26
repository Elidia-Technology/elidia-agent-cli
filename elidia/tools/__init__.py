from elidia.tools.base import ToolDefinition, ToolRegistry, ToolResult
from elidia.tools.browser import register_browser_tools
from elidia.tools.database import register_database_tools
from elidia.tools.email import register_email_tools
from elidia.tools.fetch import register_fetch_tools
from elidia.tools.filesystem import register_filesystem_tools
from elidia.tools.git import register_git_tools
from elidia.tools.office import register_office_tools
from elidia.tools.search import register_search_tools
from elidia.tools.terminal import register_terminal_tools

__all__ = [
    "ToolDefinition",
    "ToolRegistry",
    "ToolResult",
    "register_browser_tools",
    "register_database_tools",
    "register_email_tools",
    "register_fetch_tools",
    "register_filesystem_tools",
    "register_git_tools",
    "register_office_tools",
    "register_search_tools",
    "register_terminal_tools",
    "create_default_registry",
]


def create_default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    register_filesystem_tools(registry)
    register_terminal_tools(registry)
    register_git_tools(registry)
    register_search_tools(registry)
    register_fetch_tools(registry)
    register_browser_tools(registry)
    register_office_tools(registry)
    register_database_tools(registry)
    register_email_tools(registry)
    return registry
