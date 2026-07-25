import asyncio
import logging
import os
from pathlib import Path

from elidia.tools.base import ToolDefinition, ToolRegistry, ToolResult

logger = logging.getLogger(__name__)


async def _file_read(path: str, offset: int = 0, limit: int = 2000) -> ToolResult:
    logger.debug(f"Entered into _file_read: path={path}, offset={offset}, limit={limit}")
    try:
        p = Path(path).resolve()
        if not p.exists():
            return ToolResult(content=f"File not found: {path}", is_error=True)
        if not p.is_file():
            return ToolResult(content=f"Not a file: {path}", is_error=True)

        with open(p, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()

        total = len(lines)
        selected = lines[offset : offset + limit]
        numbered = [f"{i}\t{line.rstrip()}" for i, line in enumerate(selected, start=offset + 1)]

        content = "\n".join(numbered)
        if offset + limit < total:
            content += f"\n... ({total - offset - limit} more lines)"

        return ToolResult(content=content, metadata={"total_lines": total, "bytes": p.stat().st_size})
    except Exception as e:
        return ToolResult(content=f"Error reading {path}: {e}", is_error=True)


async def _file_write(path: str, content: str) -> ToolResult:
    logger.debug(f"Entered into _file_write: path={path}, bytes={len(content)}")
    try:
        p = Path(path).resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return ToolResult(content=f"Written {len(content)} bytes to {path}")
    except Exception as e:
        return ToolResult(content=f"Error writing {path}: {e}", is_error=True)


async def _file_edit(path: str, old_string: str, new_string: str) -> ToolResult:
    logger.debug(f"Entered into _file_edit: path={path}")
    try:
        p = Path(path).resolve()
        if not p.exists():
            return ToolResult(content=f"File not found: {path}", is_error=True)

        text = p.read_text(encoding="utf-8")
        count = text.count(old_string)

        if count == 0:
            return ToolResult(content=f"String not found in {path}", is_error=True)
        if count > 1:
            return ToolResult(
                content=f"String found {count} times in {path} — must be unique. Provide more context.",
                is_error=True,
            )

        new_text = text.replace(old_string, new_string, 1)
        p.write_text(new_text, encoding="utf-8")
        return ToolResult(content=f"Edited {path}: replaced 1 occurrence")
    except Exception as e:
        return ToolResult(content=f"Error editing {path}: {e}", is_error=True)


async def _file_list(path: str = ".", recursive: bool = False) -> ToolResult:
    logger.debug(f"Entered into _file_list: path={path}, recursive={recursive}")
    try:
        p = Path(path).resolve()
        if not p.exists():
            return ToolResult(content=f"Path not found: {path}", is_error=True)
        if not p.is_dir():
            return ToolResult(content=f"Not a directory: {path}", is_error=True)

        entries: list[str] = []
        if recursive:
            for root, dirs, files in os.walk(p):
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                rel = os.path.relpath(root, p)
                for f in sorted(files):
                    if not f.startswith("."):
                        fp = os.path.join(rel, f) if rel != "." else f
                        entries.append(fp)
        else:
            for item in sorted(p.iterdir()):
                if item.name.startswith("."):
                    continue
                suffix = "/" if item.is_dir() else ""
                entries.append(f"{item.name}{suffix}")

        if not entries:
            return ToolResult(content=f"Empty directory: {path}")

        return ToolResult(content="\n".join(entries), metadata={"count": len(entries)})
    except Exception as e:
        return ToolResult(content=f"Error listing {path}: {e}", is_error=True)


async def _file_glob(pattern: str, root: str = ".") -> ToolResult:
    logger.debug(f"Entered into _file_glob: pattern={pattern}, root={root}")
    try:
        p = Path(root).resolve()
        matches = sorted(
            str(m.relative_to(p))
            for m in p.glob(pattern)
            if not any(part.startswith(".") for part in m.relative_to(p).parts)
        )
        if not matches:
            return ToolResult(content=f"No matches for '{pattern}' in {root}")
        return ToolResult(content="\n".join(matches), metadata={"count": len(matches)})
    except Exception as e:
        return ToolResult(content=f"Error globbing {pattern}: {e}", is_error=True)


async def _file_grep(pattern: str, path: str = ".", include: str = "") -> ToolResult:
    logger.debug(f"Entered into _file_grep: pattern={pattern}, path={path}")
    try:
        cmd = ["grep", "-rn", "--color=never"]
        if include:
            cmd.extend(["--include", include])
        cmd.extend([pattern, path])

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        output = stdout.decode(errors="replace").strip()

        if not output:
            return ToolResult(content=f"No matches for '{pattern}'")

        lines = output.split("\n")
        if len(lines) > 200:
            output = "\n".join(lines[:200]) + f"\n... ({len(lines) - 200} more matches)"

        return ToolResult(content=output, metadata={"match_count": len(lines)})
    except TimeoutError:
        return ToolResult(content="Search timed out after 30s", is_error=True)
    except Exception as e:
        return ToolResult(content=f"Error searching: {e}", is_error=True)


def register_filesystem_tools(registry: ToolRegistry) -> None:
    logger.debug("Entered into register_filesystem_tools")

    registry.register(ToolDefinition(
        name="file_read", description="Read a file's contents with line numbers",
        parameters={"type": "object", "properties": {
            "path": {"type": "string", "description": "Path to the file"},
            "offset": {"type": "integer", "description": "Line offset (0-based)", "default": 0},
            "limit": {"type": "integer", "description": "Max lines to read", "default": 2000},
        }, "required": ["path"]},
        handler=_file_read, category="filesystem",
    ))
    registry.register(ToolDefinition(
        name="file_write", description="Write content to a file",
        parameters={"type": "object", "properties": {
            "path": {"type": "string", "description": "Path to write to"},
            "content": {"type": "string", "description": "Content to write"},
        }, "required": ["path", "content"]},
        handler=_file_write, category="filesystem",
    ))
    registry.register(ToolDefinition(
        name="file_edit", description="Replace a unique string in a file",
        parameters={"type": "object", "properties": {
            "path": {"type": "string", "description": "Path to the file"},
            "old_string": {"type": "string", "description": "Exact string to find (must be unique)"},
            "new_string": {"type": "string", "description": "Replacement string"},
        }, "required": ["path", "old_string", "new_string"]},
        handler=_file_edit, category="filesystem",
    ))
    registry.register(ToolDefinition(
        name="file_list", description="List directory contents",
        parameters={"type": "object", "properties": {
            "path": {"type": "string", "description": "Directory path", "default": "."},
            "recursive": {"type": "boolean", "description": "List recursively", "default": False},
        }},
        handler=_file_list, category="filesystem",
    ))
    registry.register(ToolDefinition(
        name="file_glob", description="Find files matching a glob pattern",
        parameters={"type": "object", "properties": {
            "pattern": {"type": "string", "description": "Glob pattern (e.g. '**/*.py')"},
            "root": {"type": "string", "description": "Root directory", "default": "."},
        }, "required": ["pattern"]},
        handler=_file_glob, category="filesystem",
    ))
    registry.register(ToolDefinition(
        name="file_grep", description="Search for text pattern in files",
        parameters={"type": "object", "properties": {
            "pattern": {"type": "string", "description": "Text or regex to search for"},
            "path": {"type": "string", "description": "Directory to search in", "default": "."},
            "include": {"type": "string", "description": "File pattern (e.g. '*.py')"},
        }, "required": ["pattern"]},
        handler=_file_grep, category="filesystem",
    ))
