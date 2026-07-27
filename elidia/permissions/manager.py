from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from enum import IntEnum
from pathlib import Path
from typing import TYPE_CHECKING

from elidia.config.settings import PermissionConfig
from elidia.permissions.audit import AuditLogger

if TYPE_CHECKING:
    from elidia.permissions.trust import TrustEngine

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
    "browser_read": PermissionTier.AUTO,
    "rag_search": PermissionTier.AUTO,

    "file_write_project": PermissionTier.SESSION,
    "command_exec": PermissionTier.SESSION,
    "mcp_call_session": PermissionTier.SESSION,
    "file_read_external": PermissionTier.SESSION,
    "email_read": PermissionTier.SESSION,

    "file_delete": PermissionTier.EVERY_TIME,
    "git_push": PermissionTier.EVERY_TIME,
    "command_destructive": PermissionTier.EVERY_TIME,
    "message_send": PermissionTier.EVERY_TIME,
    "mcp_call_ask": PermissionTier.EVERY_TIME,
    "file_write_external": PermissionTier.EVERY_TIME,
    "code_execute": PermissionTier.EVERY_TIME,
    "browser_interact": PermissionTier.EVERY_TIME,
    "db_query": PermissionTier.EVERY_TIME,
    "email_send": PermissionTier.EVERY_TIME,

    "keychain_access": PermissionTier.NEVER,
    "system_security": PermissionTier.NEVER,
    "other_user_home": PermissionTier.NEVER,
    "disable_audit": PermissionTier.NEVER,
    "external_data_send": PermissionTier.NEVER,
    "force_push_main": PermissionTier.NEVER,
    "self_modify": PermissionTier.NEVER,
}

# Actions that must prompt on literally every call, forever — progressive
# trust (TrustEngine) normally lets a repeatedly-approved EVERY_TIME action
# auto-promote to no-prompt after enough clean approvals (see
# _ask_permission below), which is the right behavior for something like
# repeated file deletes in a familiar project. It is NOT the right behavior
# for actions where a single unnoticed approval hands the agent a standing
# capability with real external consequences — sending email or running SQL
# against a live database as the user is a different risk class from
# repeated file operations, so these are exempted from promotion entirely.
NEVER_PROMOTE: set[str] = {
    "db_query",
    "email_send",
}

DESTRUCTIVE_PATTERNS = {"rm ", "rm -", "rmdir", "drop ", "truncate ", "delete ", "format "}


class PermissionManager:
    """Manages the 4-tier permission system for agent actions."""

    def __init__(
        self,
        config: PermissionConfig,
        audit: AuditLogger,
        prompt_fn: Callable[[str], bool] | None = None,
        trust_engine: TrustEngine | None = None,
    ):
        logger.debug("Entered into PermissionManager.__init__")
        self._config = config
        self._audit = audit
        self._prompt_fn = prompt_fn
        self._trust = trust_engine
        self._session_approvals: set[str] = set()
        self._project_root: Path | None = None

    def set_project_root(self, path: Path) -> None:
        logger.debug(f"Entered into set_project_root: path={path}")
        self._project_root = path.resolve()

    def classify_action(
        self, action: str, path: str | None = None, command: str | None = None
    ) -> str:
        logger.debug(f"Entered into classify_action: action={action}")

        if action == "file_read":
            # A missing path means the tool used its own default — every
            # file-read tool in this codebase (file_list, file_grep,
            # file_glob) defaults to "." (current/project directory) when
            # the model omits the argument, so treat a missing path the
            # same as an explicit ".": project-local, not the EVERY_TIME
            # fallback bare "file_read" would otherwise get. Verified live
            # 2026-07-26: the agent called file_list({}) (no path arg) and
            # got an unexpected EVERY_TIME permission prompt for what
            # should have been an ordinary AUTO-tier project read.
            return "file_read_project" if not path or self._is_project_path(path) else "file_read_external"

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

    async def check(
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
                return await self._ask_permission(classified, tier, session_id, description)
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
            # Check progressive trust — promoted actions skip prompt
            if self._trust and self._trust.is_promoted(classified):
                self._audit.log_permission_check(
                    action=classified, tier=2, approved=True,
                    method="trust_promoted", session_id=session_id,
                )
                return True

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

            return await self._ask_permission(classified, tier, session_id, description)

        return await self._ask_permission(classified, tier, session_id, description)

    async def _ask_permission(
        self, action: str, tier: int, session_id: str, description: str
    ) -> bool:
        logger.debug(f"Entered into _ask_permission: action={action}, tier={tier}")

        # Check if trust has promoted this action (applies to SESSION + EVERY_TIME,
        # except NEVER_PROMOTE actions — see the set's docstring above)
        if action in NEVER_PROMOTE:
            pass
        elif self._trust and self._trust.is_promoted(action):
            self._audit.log_permission_check(
                action=action, tier=tier, approved=True,
                method="trust_promoted", session_id=session_id,
            )
            return True

        if not self._prompt_fn:
            self._audit.log_permission_check(
                action=action, tier=tier, approved=False,
                method="no_prompt_fn", session_id=session_id,
            )
            return False

        prompt_text = description or f"Allow '{action}'?"
        result = self._prompt_fn(prompt_text)
        if asyncio.iscoroutine(result):
            approved = await result
        else:
            approved = result

        if approved and tier == PermissionTier.SESSION:
            self._session_approvals.add(action)

        # Record decision for progressive trust learning
        if self._trust:
            self._trust.record_decision(action, approved)

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
