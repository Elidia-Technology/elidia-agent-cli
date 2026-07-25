import logging
from pathlib import Path

logger = logging.getLogger(__name__)

RULES_FILENAMES = [".elidia/rules.md", ".elidia/rules.txt", ".elidia/RULES.md"]


def load_project_rules(project_root: Path | None = None) -> str | None:
    logger.debug(f"Entered into load_project_rules: root={project_root}")

    if project_root is None:
        project_root = _find_project_root()
        if project_root is None:
            return None

    for filename in RULES_FILENAMES:
        rules_path = project_root / filename
        if rules_path.exists() and rules_path.is_file():
            try:
                content = rules_path.read_text(encoding="utf-8")
                if content.strip():
                    logger.info(f"Loaded project rules from {rules_path}")
                    return content.strip()
            except Exception as e:
                logger.warning(f"Could not read {rules_path}: {e}")

    return None


def _find_project_root() -> Path | None:
    cwd = Path.cwd()
    for parent in [cwd] + list(cwd.parents):
        if (parent / ".git").exists():
            return parent
        if (parent / ".elidia").exists():
            return parent
    return cwd


def format_rules_for_system_prompt(rules: str) -> str:
    logger.debug(f"Entered into format_rules_for_system_prompt: len={len(rules)}")
    return (
        "\n\n--- Project Rules (from .elidia/rules.md) ---\n"
        "The following rules are set by the project owner. Follow them.\n\n"
        f"{rules}\n"
        "\n--- End Project Rules ---\n"
    )
