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


def _split_oversized(text: str, chunk_size: int, overlap: int) -> list[str]:
    """Split a single block of text larger than chunk_size into fixed-size,
    overlapping pieces. Used when a paragraph/section has no blank-line (or
    heading) boundary to split on and exceeds chunk_size on its own — e.g.
    a file with no blank lines anywhere used to become ONE chunk containing
    the entire document, regardless of size (a real bug: a 21KB single-block
    file produced 1 chunk instead of ~40, diluting the embedding enough that
    a fact in the middle of the file became unfindable by search)."""
    if len(text) <= chunk_size:
        return [text]
    pieces: list[str] = []
    step = max(1, chunk_size - overlap)
    start = 0
    while start < len(text):
        pieces.append(text[start:start + chunk_size])
        if start + chunk_size >= len(text):
            break
        start += step
    return pieces


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

    def flush(end_line: int) -> None:
        nonlocal current
        if current:
            chunks.append((current, ChunkMetadata(
                source=source,
                chunk_index=len(chunks),
                start_line=current_start,
                end_line=end_line,
                content_type=content_type,
            )))
            current = ""

    for para in paragraphs:
        para = para.strip()
        if not para:
            line_offset += 1
            continue

        para_lines = para.count("\n") + 1

        if len(para) > chunk_size:
            flush(line_offset - 1)
            for piece in _split_oversized(para, chunk_size, overlap):
                chunks.append((piece, ChunkMetadata(
                    source=source,
                    chunk_index=len(chunks),
                    start_line=line_offset,
                    end_line=line_offset + para_lines - 1,
                    content_type=content_type,
                )))
            current_start = line_offset + para_lines
        elif len(current) + len(para) + 2 <= chunk_size:
            if current:
                current += "\n\n" + para
            else:
                current = para
                current_start = line_offset
        else:
            flush(line_offset - 1)
            current = para
            current_start = line_offset

        line_offset += para_lines + 1

    flush(line_offset)

    for c in chunks:
        c[1].total_chunks = len(chunks)

    return chunks


def _chunk_code(
    text: str,
    chunk_size: int,
    overlap: int,
    source: str,
) -> list[tuple[str, ChunkMetadata]]:
    raw_lines = text.split("\n")
    # A single line longer than chunk_size (minified/generated code, a long
    # data literal) has no line boundary to split on — expand it into
    # fixed-size pieces up front so the boundary logic below still caps
    # chunk size correctly (same bug class as the paragraph/markdown
    # chunkers: without this, one long line becomes one oversized chunk).
    lines: list[str] = []
    for line in raw_lines:
        if len(line) > chunk_size:
            lines.extend(_split_oversized(line, chunk_size, overlap))
        else:
            lines.append(line)

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

    def flush(end_line: int) -> None:
        nonlocal current
        if current:
            chunks.append((current, ChunkMetadata(
                source=source,
                chunk_index=len(chunks),
                start_line=current_start,
                end_line=end_line,
                content_type="markdown",
            )))
            current = ""

    for section in sections:
        section = section.strip()
        if not section:
            continue

        section_lines = section.count("\n") + 1

        if len(section) > chunk_size:
            # A single section (e.g. one long paragraph under a heading,
            # with no sub-headings) larger than chunk_size — split it
            # rather than emit it whole (same bug class as _chunk_by_paragraphs).
            flush(line_offset)
            for piece in _split_oversized(section, chunk_size, overlap):
                chunks.append((piece, ChunkMetadata(
                    source=source,
                    chunk_index=len(chunks),
                    start_line=line_offset,
                    end_line=line_offset + section_lines,
                    content_type="markdown",
                )))
            current_start = line_offset + section_lines
        elif len(current) + len(section) + 2 <= chunk_size:
            if current:
                current += "\n\n" + section
            else:
                current = section
                current_start = line_offset
        else:
            flush(line_offset)
            current = section
            current_start = line_offset

        line_offset += section_lines

    flush(line_offset)

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
