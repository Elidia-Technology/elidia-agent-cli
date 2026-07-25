import asyncio
import logging
import os

from elidia.tools.base import ToolDefinition, ToolRegistry, ToolResult

logger = logging.getLogger(__name__)

MAX_OUTPUT_BYTES = 512_000
DEFAULT_TIMEOUT = 120


async def _command_exec(
    command: str,
    cwd: str = ".",
    timeout: int = DEFAULT_TIMEOUT,
    env: dict[str, str] | None = None,
) -> ToolResult:
    logger.debug(f"Entered into _command_exec: command={command!r}, cwd={cwd}")

    proc_env = dict(os.environ)
    if env:
        proc_env.update(env)

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=proc_env,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)

        stdout_str = stdout.decode(errors="replace")[:MAX_OUTPUT_BYTES]
        stderr_str = stderr.decode(errors="replace")[:MAX_OUTPUT_BYTES]

        parts: list[str] = []
        if stdout_str.strip():
            parts.append(stdout_str.strip())
        if stderr_str.strip():
            parts.append(f"STDERR:\n{stderr_str.strip()}")
        parts.append(f"Exit code: {proc.returncode}")

        return ToolResult(
            content="\n".join(parts),
            is_error=proc.returncode != 0,
            metadata={"exit_code": proc.returncode, "pid": proc.pid},
        )
    except TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return ToolResult(content=f"Command timed out after {timeout}s", is_error=True)
    except Exception as e:
        return ToolResult(content=f"Error executing command: {e}", is_error=True)


def register_terminal_tools(registry: ToolRegistry) -> None:
    logger.debug("Entered into register_terminal_tools")

    registry.register(ToolDefinition(
        name="command_exec",
        description="Execute a shell command and return its output",
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to execute"},
                "cwd": {"type": "string", "description": "Working directory", "default": "."},
                "timeout": {"type": "integer", "description": "Timeout in seconds", "default": DEFAULT_TIMEOUT},
            },
            "required": ["command"],
        },
        handler=_command_exec,
        category="terminal",
    ))
