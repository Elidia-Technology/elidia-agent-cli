"""Office document skills — read/write docx, xlsx, pptx.

Read reuses the existing parsers in rag/ingest.py (python-docx/openpyxl/
python-pptx), already exercised by the RAG ingestion pipeline — no reason
to duplicate that logic for the agent-tool path. This module wraps them
with ToolResult error handling (the RAG parsers swallow errors and return
"" for best-effort indexing; a direct tool call should surface the real
failure instead) and adds write, which didn't exist anywhere before.
"""
from __future__ import annotations

import logging
from pathlib import Path

from elidia.tools.base import ToolDefinition, ToolRegistry, ToolResult

logger = logging.getLogger(__name__)

MAX_READ_BYTES = 25 * 1024 * 1024  # 25 MB


def _check_readable(path: Path, expected_suffix: str) -> str | None:
    """Return an error message if the file can't be read, else None."""
    if not path.exists():
        return f"File not found: {path}"
    if not path.is_file():
        return f"Not a file: {path}"
    if path.suffix.lower() != expected_suffix:
        return f"Expected a {expected_suffix} file, got: {path.suffix}"
    size = path.stat().st_size
    if size > MAX_READ_BYTES:
        return f"File too large: {size} bytes (max {MAX_READ_BYTES})"
    return None


async def _read_docx(path: str) -> ToolResult:
    logger.debug(f"Entered into _read_docx: path={path}")
    p = Path(path).resolve()
    err = _check_readable(p, ".docx")
    if err:
        return ToolResult(content=err, is_error=True)
    try:
        from elidia.rag.ingest import _parse_docx
        text = _parse_docx(p)
    except ImportError:
        return ToolResult(
            content="python-docx not installed: pip install elidia-agent-cli[rag]",
            is_error=True,
        )
    if not text:
        return ToolResult(content=f"Could not extract any text from {path} (empty or unparseable)", is_error=True)
    return ToolResult(content=text, metadata={"path": str(p), "chars": len(text)})


async def _read_xlsx(path: str) -> ToolResult:
    logger.debug(f"Entered into _read_xlsx: path={path}")
    p = Path(path).resolve()
    err = _check_readable(p, ".xlsx")
    if err:
        return ToolResult(content=err, is_error=True)
    try:
        from elidia.rag.ingest import _parse_xlsx
        text = _parse_xlsx(p)
    except ImportError:
        return ToolResult(
            content="openpyxl not installed: pip install elidia-agent-cli[rag]",
            is_error=True,
        )
    if not text:
        return ToolResult(content=f"Could not extract any data from {path} (empty or unparseable)", is_error=True)
    return ToolResult(content=text, metadata={"path": str(p), "chars": len(text)})


async def _read_pptx(path: str) -> ToolResult:
    logger.debug(f"Entered into _read_pptx: path={path}")
    p = Path(path).resolve()
    err = _check_readable(p, ".pptx")
    if err:
        return ToolResult(content=err, is_error=True)
    try:
        from elidia.rag.ingest import _parse_pptx
        text = _parse_pptx(p)
    except ImportError:
        return ToolResult(
            content="python-pptx not installed: pip install elidia-agent-cli[rag]",
            is_error=True,
        )
    if not text:
        return ToolResult(content=f"Could not extract any text from {path} (empty or unparseable)", is_error=True)
    return ToolResult(content=text, metadata={"path": str(p), "chars": len(text)})


async def _write_docx(path: str, content: str) -> ToolResult:
    """Create a .docx with one paragraph per line of content (blank lines become blank paragraphs)."""
    logger.debug(f"Entered into _write_docx: path={path}, chars={len(content)}")
    try:
        from docx import Document
    except ImportError:
        return ToolResult(
            content="python-docx not installed: pip install elidia-agent-cli[rag]",
            is_error=True,
        )
    try:
        p = Path(path).resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        doc = Document()
        for line in content.split("\n"):
            doc.add_paragraph(line)
        doc.save(str(p))
        return ToolResult(content=f"Wrote {path} ({len(content)} chars)", metadata={"path": str(p)})
    except Exception as e:
        return ToolResult(content=f"Error writing {path}: {e}", is_error=True)


async def _write_xlsx(path: str, rows: list[list]) -> ToolResult:
    """Create an .xlsx with one sheet, rows as a list of row-lists."""
    logger.debug(f"Entered into _write_xlsx: path={path}, row_count={len(rows)}")
    try:
        from openpyxl import Workbook
    except ImportError:
        return ToolResult(
            content="openpyxl not installed: pip install elidia-agent-cli[rag]",
            is_error=True,
        )
    try:
        p = Path(path).resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        wb = Workbook()
        ws = wb.active
        for row in rows:
            ws.append(row)
        wb.save(str(p))
        return ToolResult(content=f"Wrote {path} ({len(rows)} rows)", metadata={"path": str(p)})
    except Exception as e:
        return ToolResult(content=f"Error writing {path}: {e}", is_error=True)


def register_office_tools(registry: ToolRegistry) -> None:
    logger.debug("Entered into register_office_tools")

    registry.register(ToolDefinition(
        name="read_docx", description="Extract text (paragraphs + tables) from a Word .docx file",
        parameters={"type": "object", "properties": {
            "path": {"type": "string", "description": "Path to the .docx file"},
        }, "required": ["path"]},
        handler=_read_docx, category="office",
    ))
    registry.register(ToolDefinition(
        name="read_xlsx", description="Extract cell data (all sheets) from an Excel .xlsx file",
        parameters={"type": "object", "properties": {
            "path": {"type": "string", "description": "Path to the .xlsx file"},
        }, "required": ["path"]},
        handler=_read_xlsx, category="office",
    ))
    registry.register(ToolDefinition(
        name="read_pptx", description="Extract slide text from a PowerPoint .pptx file",
        parameters={"type": "object", "properties": {
            "path": {"type": "string", "description": "Path to the .pptx file"},
        }, "required": ["path"]},
        handler=_read_pptx, category="office",
    ))
    registry.register(ToolDefinition(
        name="write_docx", description="Create a Word .docx file from plain text (one paragraph per line)",
        parameters={"type": "object", "properties": {
            "path": {"type": "string", "description": "Path to write the .docx to"},
            "content": {"type": "string", "description": "Text content, newline-separated paragraphs"},
        }, "required": ["path", "content"]},
        handler=_write_docx, category="office",
    ))
    registry.register(ToolDefinition(
        name="write_xlsx", description="Create an Excel .xlsx file from tabular data",
        parameters={"type": "object", "properties": {
            "path": {"type": "string", "description": "Path to write the .xlsx to"},
            "rows": {
                "type": "array", "description": "List of rows, each a list of cell values",
                "items": {"type": "array"},
            },
        }, "required": ["path", "rows"]},
        handler=_write_xlsx, category="office",
    ))
