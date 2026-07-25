import json
import logging
from pathlib import Path

from elidia.config.settings import ELIDIA_HOME, PROJECT_DIR
from elidia.mcp.types import MCPServerConfig

logger = logging.getLogger(__name__)

USER_MCP_CONFIG_PATH = ELIDIA_HOME / "mcp.json"
PROJECT_MCP_CONFIG_PATH = PROJECT_DIR / "mcp.json"
CLAUDE_MCP_CONFIG_PATH = Path.home() / ".claude" / "mcp.json"


def _parse_server_entry(name: str, data: dict) -> MCPServerConfig:
    logger.debug(f"Entered into _parse_server_entry: name={name}")
    return MCPServerConfig(
        name=name,
        command=data["command"],
        args=list(data.get("args", [])),
        env=dict(data.get("env", {})),
        cwd=data.get("cwd"),
        enabled=data.get("enabled", True),
        trust_level=data.get("trust_level", "session"),
    )


def _load_mcp_json_file(path: Path) -> dict[str, MCPServerConfig]:
    logger.debug(f"Entered into _load_mcp_json_file: path={path}")
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning(f"Entered into _load_mcp_json_file: failed to parse {path}: {exc}")
        return {}

    servers = raw.get("mcpServers", {})
    if not isinstance(servers, dict):
        logger.warning(f"Entered into _load_mcp_json_file: 'mcpServers' is not an object in {path}")
        return {}

    result: dict[str, MCPServerConfig] = {}
    for name, data in servers.items():
        if not isinstance(data, dict) or "command" not in data:
            logger.warning(f"Entered into _load_mcp_json_file: skipping invalid server entry '{name}' in {path}")
            continue
        try:
            result[name] = _parse_server_entry(name, data)
        except (KeyError, TypeError) as exc:
            logger.warning(f"Entered into _load_mcp_json_file: skipping malformed server entry '{name}' in {path}: {exc}")
    return result


def load_mcp_config(include_claude_compat: bool = True) -> dict[str, MCPServerConfig]:
    """Load MCP server configurations from all config sources and merge them.

    Precedence (lowest to highest, later sources override earlier ones by server name):
    1. ~/.claude/mcp.json (Claude Code compatibility, optional)
    2. ~/.elidia/mcp.json (user global)
    3. .elidia/mcp.json (project local)
    """
    logger.debug(f"Entered into load_mcp_config: include_claude_compat={include_claude_compat}")
    merged: dict[str, MCPServerConfig] = {}

    if include_claude_compat:
        merged.update(_load_mcp_json_file(CLAUDE_MCP_CONFIG_PATH))

    merged.update(_load_mcp_json_file(USER_MCP_CONFIG_PATH))
    merged.update(_load_mcp_json_file(PROJECT_MCP_CONFIG_PATH))

    logger.debug(f"Entered into load_mcp_config: loaded {len(merged)} server(s): {list(merged.keys())}")
    return merged


def _server_config_to_dict(config: MCPServerConfig) -> dict:
    logger.debug(f"Entered into _server_config_to_dict: name={config.name}")
    return {
        "command": config.command,
        "args": config.args,
        "env": config.env,
        "cwd": config.cwd,
        "enabled": config.enabled,
        "trust_level": config.trust_level,
    }


def save_mcp_config(servers: dict[str, MCPServerConfig], path: Path) -> None:
    """Save MCP config to file."""
    logger.debug(f"Entered into save_mcp_config: path={path}, servers={list(servers.keys())}")
    payload = {"mcpServers": {name: _server_config_to_dict(cfg) for name, cfg in servers.items()}}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
