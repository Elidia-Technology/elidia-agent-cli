from dataclasses import dataclass, field
from typing import Any


@dataclass
class MCPToolInput:
    name: str
    type: str
    description: str = ""
    required: bool = False


@dataclass
class MCPTool:
    name: str
    description: str = ""
    input_schema: dict = field(default_factory=dict)
    server_name: str = ""


@dataclass
class MCPResource:
    uri: str
    name: str
    description: str = ""
    mime_type: str = ""


@dataclass
class MCPServerConfig:
    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    enabled: bool = True
    trust_level: str = "session"  # "auto", "session", "ask-every-time"


@dataclass
class MCPCallResult:
    content: list[dict[str, Any]] = field(default_factory=list)
    is_error: bool = False
