import dataclasses
import logging
import os
import tomllib
from pathlib import Path

logger = logging.getLogger(__name__)

ELIDIA_HOME = Path(os.environ.get("ELIDIA_HOME", Path.home() / ".elidia"))
PROJECT_DIR = Path(".elidia")

_HOME_SUBDIRS = ("cache", "memory", "rag", "skills", "workflows", "prompts", "plugins")


@dataclasses.dataclass
class ApiConfig:
    key: str = ""
    base_url: str = "https://developer.aiutils.io/v1"
    timeout_seconds: int = 120
    max_retries: int = 3


@dataclasses.dataclass
class ModelConfig:
    default: str = "auto"
    reasoning: str = "deepseek-reasoner"
    code: str = "claude-sonnet-5"
    creative: str = "gpt-5"
    cheap: str = "deepseek-chat"
    vision: str = "gpt-5-vision"
    embedding: str = "local"


@dataclasses.dataclass
class LocalModelConfig:
    enabled: bool = True
    ollama_url: str = "http://localhost:11434"
    prefer_local_for: list[str] = dataclasses.field(default_factory=lambda: ["simple-chat", "summarize"])
    auto_fallback: bool = True


@dataclasses.dataclass
class MemoryConfig:
    enabled: bool = True
    auto_save: bool = True
    cloud_sync: bool = False
    max_entries: int = 50000
    compaction_after_messages: int = 100
    similarity_threshold: float = 0.30


@dataclasses.dataclass
class RagConfig:
    enabled: bool = True
    auto_index: bool = True
    watch_extensions: list[str] = dataclasses.field(
        default_factory=lambda: ["md", "txt", "pdf", "py", "ts", "js", "rs", "go", "json", "yaml", "toml"]
    )
    chunk_size: int = 512
    chunk_overlap: int = 64
    portal_bridge: bool = False


@dataclasses.dataclass
class PermissionConfig:
    auto_approve_reads: bool = True
    auto_approve_writes: bool = False
    auto_approve_commands: bool = False
    auto_approve_network: bool = True
    progressive_trust: bool = True
    trust_threshold: int = 20


@dataclasses.dataclass
class BudgetConfig:
    session_limit: int = 0
    monthly_limit: int = 0
    warn_at_percentage: int = 80
    default_thinking_level: str = "auto"


@dataclasses.dataclass
class UiConfig:
    theme: str = "auto"
    markdown: bool = True
    image_display: bool = True
    syntax_highlighting: bool = True
    max_output_lines: int = 500
    keybindings: str = "emacs"


@dataclasses.dataclass
class ElidiaConfig:
    api: ApiConfig = dataclasses.field(default_factory=ApiConfig)
    models: ModelConfig = dataclasses.field(default_factory=ModelConfig)
    local_models: LocalModelConfig = dataclasses.field(default_factory=LocalModelConfig)
    memory: MemoryConfig = dataclasses.field(default_factory=MemoryConfig)
    rag: RagConfig = dataclasses.field(default_factory=RagConfig)
    permissions: PermissionConfig = dataclasses.field(default_factory=PermissionConfig)
    budget: BudgetConfig = dataclasses.field(default_factory=BudgetConfig)
    ui: UiConfig = dataclasses.field(default_factory=UiConfig)


# env var -> (dataclass field on ElidiaConfig, field on the nested section)
_ENV_OVERRIDES: dict[str, tuple[str, str]] = {
    "AIUTILS_API_KEY": ("api", "key"),
    "ELIDIA_API_BASE_URL": ("api", "base_url"),
    "ELIDIA_API_TIMEOUT_SECONDS": ("api", "timeout_seconds"),
    "ELIDIA_API_MAX_RETRIES": ("api", "max_retries"),
    "ELIDIA_MODEL": ("models", "default"),
    "ELIDIA_MODEL_REASONING": ("models", "reasoning"),
    "ELIDIA_MODEL_CODE": ("models", "code"),
    "ELIDIA_MODEL_CREATIVE": ("models", "creative"),
    "ELIDIA_MODEL_CHEAP": ("models", "cheap"),
    "ELIDIA_MODEL_VISION": ("models", "vision"),
    "ELIDIA_OLLAMA_URL": ("local_models", "ollama_url"),
    "ELIDIA_LOCAL_MODELS_ENABLED": ("local_models", "enabled"),
    "ELIDIA_THEME": ("ui", "theme"),
    "ELIDIA_SESSION_BUDGET": ("budget", "session_limit"),
    "ELIDIA_MONTHLY_BUDGET": ("budget", "monthly_limit"),
}


def ensure_elidia_home() -> None:
    logger.debug(f"Entered into ensure_elidia_home: home={ELIDIA_HOME}")
    ELIDIA_HOME.mkdir(parents=True, exist_ok=True)
    for subdir in _HOME_SUBDIRS:
        (ELIDIA_HOME / subdir).mkdir(parents=True, exist_ok=True)


def _merge_toml_into_dataclass(data: dict, dc: object) -> object:
    logger.debug(f"Entered into _merge_toml_into_dataclass: keys={list(data.keys())}")
    for field in dataclasses.fields(dc):
        if field.name not in data:
            continue
        value = data[field.name]
        current = getattr(dc, field.name)
        if dataclasses.is_dataclass(current) and isinstance(value, dict):
            _merge_toml_into_dataclass(value, current)
        else:
            setattr(dc, field.name, value)
    return dc


def _load_toml_file(path: Path) -> dict:
    logger.debug(f"Entered into _load_toml_file: path={path}")
    try:
        with path.open("rb") as f:
            return tomllib.load(f)
    except (tomllib.TOMLDecodeError, OSError) as exc:
        logger.warning(f"Entered into _load_toml_file: failed to parse {path}: {exc}")
        return {}


def _apply_env_overrides(config: ElidiaConfig) -> None:
    logger.debug("Entered into _apply_env_overrides: applying environment variable overrides")
    for env_var, (section_name, field_name) in _ENV_OVERRIDES.items():
        raw_value = os.environ.get(env_var)
        if raw_value is None:
            continue
        section = getattr(config, section_name)
        current_value = getattr(section, field_name)
        if isinstance(current_value, bool):
            coerced_value: object = raw_value.strip().lower() in {"1", "true", "yes", "on"}
        elif isinstance(current_value, int):
            coerced_value = int(raw_value)
        elif isinstance(current_value, float):
            coerced_value = float(raw_value)
        else:
            coerced_value = raw_value
        setattr(section, field_name, coerced_value)


def load_config() -> ElidiaConfig:
    logger.debug("Entered into load_config: loading configuration")
    ensure_elidia_home()
    config = ElidiaConfig()

    user_config_path = ELIDIA_HOME / "config.toml"
    if user_config_path.exists():
        _merge_toml_into_dataclass(_load_toml_file(user_config_path), config)

    project_config_path = PROJECT_DIR / "project.toml"
    if project_config_path.exists():
        _merge_toml_into_dataclass(_load_toml_file(project_config_path), config)

    _apply_env_overrides(config)
    return config


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    raise TypeError(f"Unsupported TOML value type: {type(value)!r}")


def save_config(config: ElidiaConfig, path: Path) -> None:
    logger.debug(f"Entered into save_config: path={path}")
    lines: list[str] = []
    for section_field in dataclasses.fields(config):
        section = getattr(config, section_field.name)
        lines.append(f"[{section_field.name}]")
        for value_field in dataclasses.fields(section):
            value = getattr(section, value_field.name)
            lines.append(f"{value_field.name} = {_toml_value(value)}")
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
