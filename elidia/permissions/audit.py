import json
import logging
import time
from pathlib import Path
from typing import Any, TextIO

from elidia.config.settings import ELIDIA_HOME

logger = logging.getLogger(__name__)

AUDIT_LOG_PATH = ELIDIA_HOME / "audit.log"


class AuditLogger:
    """Append-only audit log for all agent actions."""

    def __init__(self, path: Path | None = None):
        logger.debug(f"Entered into AuditLogger.__init__: path={path}")
        self._path = path or AUDIT_LOG_PATH
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._fd: TextIO | None = None

    def open(self) -> None:
        """Open the audit log for appending."""
        logger.debug(f"Entered into AuditLogger.open: path={self._path}")
        self._fd = open(self._path, "a", encoding="utf-8", buffering=1)

    def close(self) -> None:
        logger.debug("Entered into AuditLogger.close")
        if self._fd:
            self._fd.close()
            self._fd = None

    def log(self, action: str, session_id: str = "", **details: Any) -> None:
        """Log an action to the audit trail."""
        logger.debug(f"Entered into log: action={action}, session_id={session_id}")
        if not self._fd:
            self.open()

        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "action": action,
            "session": session_id,
        }
        entry.update(details)

        line = json.dumps(entry, default=str) + "\n"
        self._fd.write(line)

    def log_permission_check(
        self,
        action: str,
        tier: int,
        approved: bool,
        method: str = "auto",
        session_id: str = "",
        **details: Any,
    ) -> None:
        """Log a permission check result."""
        logger.debug(f"Entered into log_permission_check: action={action}, tier={tier}, approved={approved}")
        self.log(
            action="permission_check",
            session_id=session_id,
            requested_action=action,
            tier=tier,
            approved=approved,
            method=method,
            **details,
        )

    def log_tool_call(
        self,
        tool_name: str,
        server: str = "",
        approved: bool = True,
        session_id: str = "",
        **details: Any,
    ) -> None:
        """Log an MCP tool call."""
        logger.debug(f"Entered into log_tool_call: tool_name={tool_name}, server={server}")
        self.log(
            action="tool_call",
            session_id=session_id,
            tool=tool_name,
            server=server,
            approved=approved,
            **details,
        )

    def log_file_access(
        self,
        path: str,
        operation: str,
        session_id: str = "",
        **details: Any,
    ) -> None:
        """Log file read/write/delete."""
        logger.debug(f"Entered into log_file_access: path={path}, operation={operation}")
        self.log(
            action=f"file_{operation}",
            session_id=session_id,
            path=path,
            **details,
        )

    def log_model_call(
        self,
        model: str,
        tokens_in: int = 0,
        tokens_out: int = 0,
        cost_dt: float = 0.0,
        session_id: str = "",
    ) -> None:
        """Log a model API call."""
        logger.debug(f"Entered into log_model_call: model={model}, tokens_in={tokens_in}, tokens_out={tokens_out}")
        self.log(
            action="model_call",
            session_id=session_id,
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_dt=cost_dt,
        )

    def log_command(
        self,
        command: str,
        exit_code: int | None = None,
        session_id: str = "",
    ) -> None:
        """Log a terminal command execution."""
        logger.debug(f"Entered into log_command: command={command}, exit_code={exit_code}")
        self.log(
            action="command_exec",
            session_id=session_id,
            command=command,
            exit_code=exit_code,
        )
