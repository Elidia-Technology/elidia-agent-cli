import logging
from collections.abc import Callable
from enum import IntEnum
from pathlib import Path

from elidia.config.settings import PermissionConfig
from elidia.permissions.audit import AuditLogger

logger = logging.getLogger(__name__)


class PermissionTier(IntEnum):
    AUTO = 1
    SESSION = 2
    EVERY_TIME = 3
    NEVER = 4


ACTION_TIERS: dict[str, PermissionTier] = {
    "file_read_project": PermissionTier.AUTO,
    "config_read": PermissionTier.AUTO,
    "web_search": PermissionTier.AUTO,
    "model_call": PermissionTier.AUTO,
    "clipboard_read": PermissionTier.AUTO,
    "mcp_call_auto": PermissionTier.AUTO,

    "file_write_project": PermissionTier.SESSION,
    "command_exec": PermissionTier.SESSION,
    "mcp_call_session": PermissionTier.SESSION,
    "file_read_external": PermissionTier.SESSION,

    "file_delete": PermissionTier.EVERY_TIME,
    "git_push": PermissionTier.EVERY_TIME,
    "command_destructive": PermissionTier.EVERY_TIME,
    "message_send": PermissionTier.EVERY_TIME,
    "mcp_call_ask": PermissionTier.EVERY_TIME,
    "file_write_external": PermissionTier.EVERY_TIME,
    "code_execute": PermissionTier.EVERY_TIME,

    "keychain_access": PermissionTier.NEVER,
    "system_security": PermissionTier.NEVER,
    "other_user_home": PermissionTier.NEVER,
    "disable_audit": PermissionTier.NEVER,
    "external_data_send": PermissionTier.NEVER,
    "force_push_main": PermissionTier.NEVER,
    "self_modify": PermissionTier.NEVER,
}

DESTRUCTIVE_PATTERNS = {"rm ", "rm -", "rmdir", "drop ", "truncate ", "delete ", "format "}


class PermissionManager:
    """Manages the 4-tier permission system for agent actions."""

    def __init__(
        self,
        config: PermissionConfig,
        audit: AuditLogger,
        prompt_fn: Callable[[str], bool] | None = None,
    ):
        logger.debug("Entered into PermissionManager.__init__")
        self._config = config
        self._audit = audit
        self._prompt_fn = prompt_fn
        self._session_approvals: set[str] = set()
        self._project_root: Path | None = None

    def set_project_root(self, path: Path) -> None:
        logger.debug(f"Entered into set_project_root: path={path}")
        self._project_root = path.resolve()

    def classify_action(
        self, action: str, path: str | None = None, command: str | None = None
    ) -> str:
        logger.debug(f"Entered into classify_action: action={action}")

        if action == "file_read" and path:
            return "file_read_project" if self._is_project_path(path) else "file_read_external"

        if action == "file_write" and path:
            return "file_write_project" if self._is_project_path(path) else "file_write_external"

        if action == "file_delete":
            return "file_delete"

        if action == "command_exec" and command:
            cmd_lower = command.lower().strip()
            if any(cmd_lower.startswith(p) for p in DESTRUCTIVE_PATTERNS):
                return "command_destructive"
            if cmd_lower.startswith("git push"):
                if "--force" in cmd_lower or "main" in cmd_lower or "master" in cmd_lower:
                    return "force_push_main"
                return "git_push"
            return "command_exec"

        return action

    def check(
        self,
        action: str,
        session_id: str = "",
        path: str | None = None,
        command: str | None = None,
        description: str = "",
    ) -> bool:
        logger.debug(f"Entered into check: action={action}")

        classified = self.classify_action(action, path=path, command=command)
        tier = ACTION_TIERS.get(classified, PermissionTier.EVERY_TIME)

        if tier == PermissionTier.NEVER:
            self._audit.log_permission_check(
                action=classified, tier=4, approved=False,
                method="blocked", session_id=session_id,
                description=description,
            )
            logger.warning(f"BLOCKED (Tier 4): {classified}")
            return False

        if tier == PermissionTier.AUTO:
            if classified == "file_read_project" and not self._config.auto_approve_reads:
                return self._ask_permission(classified, tier, session_id, description)
            if classified == "file_write_project" and self._config.auto_approve_writes:
                self._audit.log_permission_check(
                    action=classified, tier=1, approved=True,
                    method="auto_config_override", session_id=session_id,
                )
                return True
            self._audit.log_permission_check(
                action=classified, tier=1, approved=True,
                method="auto", session_id=session_id,
            )
            return True

        if tier == PermissionTier.SESSION:
            if classified in self._session_approvals:
                self._audit.log_permission_check(
                    action=classified, tier=2, approved=True,
                    method="session_cached", session_id=session_id,
                )
                return True

            if classified == "file_write_project" and self._config.auto_approve_writes:
                self._session_approvals.add(classified)
                self._audit.log_permission_check(
                    action=classified, tier=2, approved=True,
                    method="auto_config", session_id=session_id,
                )
                return True

            if classified == "command_exec" and self._config.auto_approve_commands:
                self._session_approvals.add(classified)
                self._audit.log_permission_check(
                    action=classified, tier=2, approved=True,
                    method="auto_config", session_id=session_id,
                )
                return True

            return self._ask_permission(classified, tier, session_id, description)

        return self._ask_permission(classified, tier, session_id, description)

    def _ask_permission(
        self, action: str, tier: int, session_id: str, description: str
    ) -> bool:
        logger.debug(f"Entered into _ask_permission: action={action}, tier={tier}")

        if not self._prompt_fn:
            self._audit.log_permission_check(
                action=action, tier=tier, approved=False,
                method="no_prompt_fn", session_id=session_id,
            )
            return False

        prompt_text = description or f"Allow '{action}'?"
        approved = self._prompt_fn(prompt_text)

        if approved and tier == PermissionTier.SESSION:
            self._session_approvals.add(action)

        self._audit.log_permission_check(
            action=action, tier=tier, approved=approved,
            method="user_prompt", session_id=session_id,
        )
        return approved

    def reset_session(self) -> None:
        logger.debug("Entered into reset_session")
        self._session_approvals.clear()

    def _is_project_path(self, path: str) -> bool:
        logger.debug(f"Entered into _is_project_path: path={path}")
        if not self._project_root:
            return True
        try:
            resolved = Path(path).resolve()
            return str(resolved).startswith(str(self._project_root))
        except (OSError, ValueError):
            return False
