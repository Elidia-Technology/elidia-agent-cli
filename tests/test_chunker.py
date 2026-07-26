"""Tests for elidia.rag.chunker — real bug found live during RAG testing:
a paragraph/section/line larger than chunk_size was emitted as ONE
oversized chunk instead of being split, regardless of how large it was.
A 21KB single-block file (no blank lines) produced 1 chunk instead of
~40, diluting the embedding enough that a fact in the middle of the file
became unfindable by rag_search. Fixed for all three content types."""
from elidia.rag.chunker import _split_oversized, chunk_text


class TestSplitOversized:
    def test_short_text_returned_as_single_piece(self):
        assert _split_oversized("short", chunk_size=512, overlap=64) == ["short"]

    def test_splits_into_capped_pieces(self):
        text = "x" * 1200
        pieces = _split_oversized(text, chunk_size=512, overlap=64)
        assert all(len(p) <= 512 for p in pieces)
        assert len(pieces) > 1

    def test_pieces_overlap(self):
        text = "0123456789" * 100  # 1000 chars, easy to find overlap boundaries
        pieces = _split_oversized(text, chunk_size=512, overlap=64)
        assert pieces[0][-64:] == pieces[1][:64]

    def test_reconstructs_full_text_coverage(self):
        text = "abcdefghij" * 200  # 2000 chars
        pieces = _split_oversized(text, chunk_size=300, overlap=50)
        assert pieces[-1].endswith(text[-1])
        assert "".join(pieces)[: len(text)].startswith(text[:250])


class TestChunkByParagraphsOversized:
    """content_type='text' — the case that broke on a real 21KB log-style file."""

    def test_single_block_no_blank_lines_gets_split(self):
        lines = [f"Line {i}: filler content about the migration project." for i in range(400)]
        lines.insert(200, "CRITICAL_MARKER: the database migration deadline is 2026-09-15.")
        text = "\n".join(lines)

        chunks = chunk_text(text, chunk_size=512, content_type="text")

        assert len(chunks) > 1
        assert all(len(c[0]) <= 512 for c in chunks)
        marker_chunks = [c for c in chunks if "CRITICAL_MARKER" in c[0]]
        assert len(marker_chunks) == 1
        assert len(marker_chunks[0][0]) < 1000  # isolated, not buried in a 21KB blob

    def test_total_chunks_metadata_is_consistent(self):
        text = "y" * 3000
        chunks = chunk_text(text, chunk_size=512, content_type="text")
        assert all(meta.total_chunks == len(chunks) for _, meta in chunks)
        assert [meta.chunk_index for _, meta in chunks] == list(range(len(chunks)))

    def test_normal_small_paragraphs_still_merge_as_before(self):
        # Regression check: the fix must not change behavior for the
        # common case (several short, blank-line-separated paragraphs).
        text = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
        chunks = chunk_text(text, chunk_size=512, content_type="text")
        assert len(chunks) == 1
        assert "First paragraph." in chunks[0][0]
        assert "Third paragraph." in chunks[0][0]


class TestChunkMarkdownOversized:
    def test_single_giant_section_gets_split(self):
        text = "# Title\n\n" + ("word " * 3000)  # ~15KB, one section, no sub-headings
        chunks = chunk_text(text, chunk_size=512, content_type="markdown")
        assert len(chunks) > 1
        assert all(len(c[0]) <= 512 for c in chunks)

    def test_normal_headed_sections_still_split_on_headings(self):
        text = "# A\n\nShort intro.\n\n## B\n\nAnother short bit."
        chunks = chunk_text(text, chunk_size=512, content_type="markdown")
        assert len(chunks) == 1  # both sections comfortably fit in one chunk together


class TestChunkCodeOversized:
    def test_single_long_line_gets_split_not_left_whole(self):
        code = "x = 1\n" + ("a" * 5000) + "\ny = 2\n"
        chunks = chunk_text(code, chunk_size=512, content_type="code")
        assert len(chunks) > 1
        # The old bug produced one ~5012-char chunk; must be meaningfully bounded now.
        assert all(len(c[0]) < 2000 for c in chunks)

    def test_normal_short_lines_unaffected(self):
        code = "\n".join(f"line_{i} = {i}" for i in range(20))
        chunks = chunk_text(code, chunk_size=512, content_type="code")
        assert len(chunks) == 1
        assert "line_0 = 0" in chunks[0][0]
        assert "line_19 = 19" in chunks[0][0]
