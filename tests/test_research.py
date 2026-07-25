"""Tests for elidia.research — orchestrator, sources, export."""
import tempfile
from pathlib import Path

import pytest

from elidia.research.export import ExportResult, export_html, export_markdown
from elidia.research.orchestrator import SearchResult, _extract_sources
from elidia.research.sources import MCP_SEARCH_SOURCES, ResearchSources, SourceConfig


class TestSearchResult:
    def test_basic_fields(self):
        r = SearchResult(query="test", title="Title", url="http://example.com")
        assert r.query == "test"
        assert r.title == "Title"
        assert r.url == "http://example.com"

    def test_optional_fields(self):
        r = SearchResult(query="q", title="T", url="http://x.com", snippet="snip", source="web")
        assert r.snippet == "snip"
        assert r.source == "web"


class TestExtractSources:
    def test_deduplication(self):
        results = [
            SearchResult(query="q", title="A", url="http://a.com"),
            SearchResult(query="q", title="B", url="http://b.com"),
            SearchResult(query="q", title="C", url="http://a.com"),
        ]
        sources = _extract_sources(results)
        assert len(sources) == 2

    def test_empty_input(self):
        assert _extract_sources([]) == []

    def test_preserves_fields(self):
        results = [
            SearchResult(query="q", title="Title", url="http://x.com", source="web"),
        ]
        sources = _extract_sources(results)
        assert sources[0]["title"] == "Title"
        assert sources[0]["url"] == "http://x.com"


class TestResearchSources:
    def test_mcp_sources_count(self):
        assert len(MCP_SEARCH_SOURCES) >= 5

    def test_init_no_registries(self):
        rs = ResearchSources()
        assert len(rs._source_configs) >= 5

    def test_get_available_empty(self):
        rs = ResearchSources()
        avail = rs.get_available_sources()
        assert len(avail) == 0

    def test_enable_disable_source(self):
        rs = ResearchSources()
        server_name = MCP_SEARCH_SOURCES[0][0]
        rs.disable_source(server_name)
        assert not rs._source_configs[server_name].enabled
        rs.enable_source(server_name)
        assert rs._source_configs[server_name].enabled


class TestExportMarkdown:
    def test_basic_export(self):
        result = export_markdown("# Report\nContent here.")
        assert isinstance(result, ExportResult)
        assert "Report" in result.content
        assert "Content" in result.content

    def test_with_sources(self):
        sources = [{"title": "Source 1", "url": "http://s1.com", "source": "web"}]
        result = export_markdown("Report body", sources=sources)
        assert "Source 1" in result.content
        assert "http://s1.com" in result.content

    def test_file_output(self):
        with tempfile.NamedTemporaryFile(suffix=".md", delete=False) as f:
            path = Path(f.name)
        result = export_markdown("Test content", output_path=path)
        assert path.exists()
        content = path.read_text()
        assert "Test content" in content
        path.unlink()


class TestExportHtml:
    def test_basic_export(self):
        result = export_html("# Heading\n**bold** text")
        assert "<h2>Heading</h2>" in result.content
        assert "<strong>bold</strong>" in result.content

    def test_self_contained(self):
        result = export_html("# Test\nContent")
        assert "<style>" in result.content
        assert "</style>" in result.content

    def test_file_output(self):
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
            path = Path(f.name)
        result = export_html("Test HTML", output_path=path)
        assert path.exists()
        content = path.read_text()
        assert "Test HTML" in content
        path.unlink()
