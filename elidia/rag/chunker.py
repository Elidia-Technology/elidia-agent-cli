import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

DEFAULT_CHUNK_SIZE = 512
DEFAULT_OVERLAP = 64


@dataclass
class ChunkMetadata:
    source: str = ""
    chunk_index: int = 0
    total_chunks: int = 0
    start_line: int = 0
    end_line: int = 0
    content_type: str = "text"


def chunk_text(
    text: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP,
    source: str = "",
    content_type: str = "text",
) -> list[tuple[str, ChunkMetadata]]:
    logger.debug(f"Entered into chunk_text: len={len(text)}, chunk_size={chunk_size}")

    if content_type == "code":
        return _chunk_code(text, chunk_size, overlap, source)
    if content_type == "markdown":
        return _chunk_markdown(text, chunk_size, overlap, source)

    return _chunk_by_paragraphs(text, chunk_size, overlap, source, content_type)


def _chunk_by_paragraphs(
    text: str,
    chunk_size: int,
    overlap: int,
    source: str,
    content_type: str,
) -> list[tuple[str, ChunkMetadata]]:
    paragraphs = re.split(r"\n\s*\n", text)
    chunks: list[tuple[str, ChunkMetadata]] = []
    current = ""
    current_start = 0
    line_offset = 0

    for para in paragraphs:
        para = para.strip()
        if not para:
            line_offset += 1
            continue

        para_lines = para.count("\n") + 1

        if len(current) + len(para) + 2 <= chunk_size:
            if current:
                current += "\n\n" + para
            else:
                current = para
                current_start = line_offset
        else:
            if current:
                chunks.append((current, ChunkMetadata(
                    source=source,
                    chunk_index=len(chunks),
                    start_line=current_start,
                    end_line=line_offset - 1,
                    content_type=content_type,
                )))
            current = para
            current_start = line_offset

        line_offset += para_lines + 1

    if current:
        chunks.append((current, ChunkMetadata(
            source=source,
            chunk_index=len(chunks),
            start_line=current_start,
            end_line=line_offset,
            content_type=content_type,
        )))

    for c in chunks:
        c[1].total_chunks = len(chunks)

    return chunks


def _chunk_code(
    text: str,
    chunk_size: int,
    overlap: int,
    source: str,
) -> list[tuple[str, ChunkMetadata]]:
    lines = text.split("\n")
    chunks: list[tuple[str, ChunkMetadata]] = []
    current_lines: list[str] = []
    current_len = 0
    start_line = 0

    for i, line in enumerate(lines):
        current_lines.append(line)
        current_len += len(line) + 1

        is_boundary = (
            current_len >= chunk_size
            or (
                i < len(lines) - 1
                and current_len > chunk_size // 2
                and _is_code_boundary(line, lines[i + 1] if i + 1 < len(lines) else "")
            )
        )

        if is_boundary:
            chunk_text_str = "\n".join(current_lines)
            chunks.append((chunk_text_str, ChunkMetadata(
                source=source,
                chunk_index=len(chunks),
                start_line=start_line + 1,
                end_line=i + 1,
                content_type="code",
            )))

            overlap_lines = max(2, overlap // 40)
            current_lines = current_lines[-overlap_lines:]
            current_len = sum(len(l) + 1 for l in current_lines)
            start_line = i + 1 - len(current_lines)

    if current_lines:
        chunk_text_str = "\n".join(current_lines)
        if chunks and chunk_text_str == chunks[-1][0]:
            pass
        else:
            chunks.append((chunk_text_str, ChunkMetadata(
                source=source,
                chunk_index=len(chunks),
                start_line=start_line + 1,
                end_line=len(lines),
                content_type="code",
            )))

    for c in chunks:
        c[1].total_chunks = len(chunks)

    return chunks


def _chunk_markdown(
    text: str,
    chunk_size: int,
    overlap: int,
    source: str,
) -> list[tuple[str, ChunkMetadata]]:
    sections = re.split(r"(?m)^(#{1,4}\s+.+)$", text)
    chunks: list[tuple[str, ChunkMetadata]] = []
    current = ""
    current_start = 0
    line_offset = 0

    for section in sections:
        section = section.strip()
        if not section:
            continue

        if len(current) + len(section) + 2 <= chunk_size:
            if current:
                current += "\n\n" + section
            else:
                current = section
                current_start = line_offset
        else:
            if current:
                chunks.append((current, ChunkMetadata(
                    source=source,
                    chunk_index=len(chunks),
                    start_line=current_start,
                    end_line=line_offset,
                    content_type="markdown",
                )))
            current = section
            current_start = line_offset

        line_offset += section.count("\n") + 1

    if current:
        chunks.append((current, ChunkMetadata(
            source=source,
            chunk_index=len(chunks),
            start_line=current_start,
            end_line=line_offset,
            content_type="markdown",
        )))

    for c in chunks:
        c[1].total_chunks = len(chunks)

    return chunks


def _is_code_boundary(current_line: str, next_line: str) -> bool:
    stripped = current_line.strip()
    next_stripped = next_line.strip()

    if stripped == "" and next_stripped != "":
        return True
    if re.match(r"^(def |class |async def |    def |    class )", next_stripped):
        return True
    if stripped.endswith("}") or stripped.endswith(";"):
        return True

    return False
