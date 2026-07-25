import asyncio
import logging

from elidia.tools.base import ToolDefinition, ToolRegistry, ToolResult

logger = logging.getLogger(__name__)

MAX_OUTPUT_BYTES = 256_000


async def _run_git(args: list[str], cwd: str = ".") -> ToolResult:
    logger.debug(f"Entered into _run_git: args={args}")
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)

        stdout_str = stdout.decode(errors="replace")[:MAX_OUTPUT_BYTES]
        stderr_str = stderr.decode(errors="replace")[:MAX_OUTPUT_BYTES]

        parts: list[str] = []
        if stdout_str.strip():
            parts.append(stdout_str.strip())
        if stderr_str.strip():
            parts.append(stderr_str.strip())

        return ToolResult(
            content="\n".join(parts) or "(no output)",
            is_error=proc.returncode != 0,
            metadata={"exit_code": proc.returncode},
        )
    except TimeoutError:
        return ToolResult(content="Git command timed out after 30s", is_error=True)
    except FileNotFoundError:
        return ToolResult(content="git is not installed or not in PATH", is_error=True)
    except Exception as e:
        return ToolResult(content=f"Error running git: {e}", is_error=True)


async def _git_status(cwd: str = ".") -> ToolResult:
    logger.debug(f"Entered into _git_status: cwd={cwd}")
    return await _run_git(["status", "--short", "--branch"], cwd)


async def _git_diff(ref: str = "", staged: bool = False, cwd: str = ".") -> ToolResult:
    logger.debug(f"Entered into _git_diff: ref={ref}, staged={staged}")
    args = ["diff"]
    if staged:
        args.append("--cached")
    if ref:
        args.append(ref)
    return await _run_git(args, cwd)


async def _git_log(count: int = 10, oneline: bool = True, cwd: str = ".") -> ToolResult:
    logger.debug(f"Entered into _git_log: count={count}")
    args = ["log", f"-{count}"]
    if oneline:
        args.append("--oneline")
    return await _run_git(args, cwd)


async def _git_commit(message: str, files: list[str] | None = None, cwd: str = ".") -> ToolResult:
    logger.debug(f"Entered into _git_commit: message={message!r}")

    if files:
        add_result = await _run_git(["add", "--"] + files, cwd)
        if add_result.is_error:
            return add_result
    else:
        add_result = await _run_git(["add", "-A"], cwd)
        if add_result.is_error:
            return add_result

    return await _run_git(["commit", "-m", message], cwd)


async def _git_branch(cwd: str = ".") -> ToolResult:
    logger.debug(f"Entered into _git_branch: cwd={cwd}")
    return await _run_git(["branch", "-a", "--no-color"], cwd)


def register_git_tools(registry: ToolRegistry) -> None:
    logger.debug("Entered into register_git_tools")

    registry.register(ToolDefinition(
        name="git_status",
        description="Show git status (short format with branch info)",
        parameters={
            "type": "object",
            "properties": {
                "cwd": {"type": "string", "description": "Repository path", "default": "."},
            },
        },
        handler=_git_status,
        category="git",
    ))
    registry.register(ToolDefinition(
        name="git_diff",
        description="Show git diff (working tree or staged changes)",
        parameters={
            "type": "object",
            "properties": {
                "ref": {"type": "string", "description": "Diff against this ref (e.g. HEAD~1, main)"},
                "staged": {"type": "boolean", "description": "Show staged changes only", "default": False},
                "cwd": {"type": "string", "description": "Repository path", "default": "."},
            },
        },
        handler=_git_diff,
        category="git",
    ))
    registry.register(ToolDefinition(
        name="git_log",
        description="Show recent git log entries",
        parameters={
            "type": "object",
            "properties": {
                "count": {"type": "integer", "description": "Number of entries", "default": 10},
                "oneline": {"type": "boolean", "description": "One-line format", "default": True},
                "cwd": {"type": "string", "description": "Repository path", "default": "."},
            },
        },
        handler=_git_log,
        category="git",
    ))
    registry.register(ToolDefinition(
        name="git_commit",
        description="Stage files and create a git commit",
        parameters={
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Commit message"},
                "files": {"type": "array", "items": {"type": "string"}, "description": "Files to stage (default: all)"},
                "cwd": {"type": "string", "description": "Repository path", "default": "."},
            },
            "required": ["message"],
        },
        handler=_git_commit,
        category="git",
    ))
    registry.register(ToolDefinition(
        name="git_branch",
        description="List all git branches (local and remote)",
        parameters={
            "type": "object",
            "properties": {
                "cwd": {"type": "string", "description": "Repository path", "default": "."},
            },
        },
        handler=_git_branch,
        category="git",
    ))
