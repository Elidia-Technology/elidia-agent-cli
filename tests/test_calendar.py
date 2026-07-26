"""Tests for elidia.tools.calendar — local .ics read/write."""
from pathlib import Path

import pytest
from icalendar import Calendar, Event

from elidia.tools import ToolRegistry, create_default_registry
from elidia.tools.calendar import (
    _calendar_add_event,
    _calendar_find_conflicts,
    _calendar_list_events,
    register_calendar_tools,
)


def _make_ics(tmp_dir: Path, events: list[tuple[str, str, str]]) -> Path:
    """events: list of (summary, start_iso, end_iso)."""
    from datetime import datetime

    cal = Calendar()
    cal.add("prodid", "-//Test//")
    cal.add("version", "2.0")
    for i, (summary, start, end) in enumerate(events):
        ev = Event()
        ev.add("summary", summary)
        ev.add("dtstart", datetime.fromisoformat(start))
        ev.add("dtend", datetime.fromisoformat(end))
        ev.add("uid", f"fixture-{i}@test")
        cal.add_component(ev)
    path = tmp_dir / "calendar.ics"
    path.write_bytes(cal.to_ical())
    return path


class TestRegistration:
    def test_registers_three_tools(self):
        registry = ToolRegistry()
        register_calendar_tools(registry)
        names = {t.name for t in registry.list_tools()}
        assert names == {"calendar_list_events", "calendar_add_event", "calendar_find_conflicts"}

    def test_wired_into_default_registry(self):
        registry = create_default_registry()
        assert registry.get("calendar_list_events") is not None


class TestListEvents:
    @pytest.mark.asyncio
    async def test_list_all_events(self, tmp_dir: Path):
        path = _make_ics(tmp_dir, [
            ("Standup", "2026-08-01T09:00:00", "2026-08-01T09:15:00"),
            ("Design review", "2026-08-02T14:00:00", "2026-08-02T15:00:00"),
        ])
        result = await _calendar_list_events(str(path))
        assert not result.is_error
        assert "Standup" in result.content
        assert "Design review" in result.content
        assert result.metadata["count"] == 2

    @pytest.mark.asyncio
    async def test_list_filtered_by_range(self, tmp_dir: Path):
        path = _make_ics(tmp_dir, [
            ("Early meeting", "2026-08-01T09:00:00", "2026-08-01T10:00:00"),
            ("Late meeting", "2026-08-10T09:00:00", "2026-08-10T10:00:00"),
        ])
        result = await _calendar_list_events(str(path), start="2026-08-01", end="2026-08-05")
        assert "Early meeting" in result.content
        assert "Late meeting" not in result.content

    @pytest.mark.asyncio
    async def test_list_missing_file(self, tmp_dir: Path):
        result = await _calendar_list_events(str(tmp_dir / "nope.ics"))
        assert result.is_error

    @pytest.mark.asyncio
    async def test_list_empty_calendar(self, tmp_dir: Path):
        path = _make_ics(tmp_dir, [])
        result = await _calendar_list_events(str(path))
        assert not result.is_error
        assert "No events" in result.content


class TestAddEvent:
    @pytest.mark.asyncio
    async def test_add_to_new_file(self, tmp_dir: Path):
        path = tmp_dir / "new_calendar.ics"
        result = await _calendar_add_event(
            str(path), "Kickoff", "2026-09-01T10:00:00", "2026-09-01T11:00:00",
            description="Project kickoff meeting",
        )
        assert not result.is_error
        assert path.exists()

        # Round-trip: re-parse and confirm the event is really there, well-formed.
        list_result = await _calendar_list_events(str(path))
        assert "Kickoff" in list_result.content

    @pytest.mark.asyncio
    async def test_add_to_existing_file_preserves_prior_events(self, tmp_dir: Path):
        path = _make_ics(tmp_dir, [("Existing event", "2026-08-01T09:00:00", "2026-08-01T10:00:00")])
        await _calendar_add_event(str(path), "New event", "2026-08-02T09:00:00", "2026-08-02T10:00:00")

        result = await _calendar_list_events(str(path))
        assert "Existing event" in result.content
        assert "New event" in result.content
        assert result.metadata["count"] == 2

    @pytest.mark.asyncio
    async def test_add_rejects_end_before_start(self, tmp_dir: Path):
        path = tmp_dir / "calendar.ics"
        result = await _calendar_add_event(
            str(path), "Backwards", "2026-08-01T11:00:00", "2026-08-01T10:00:00",
        )
        assert result.is_error

    @pytest.mark.asyncio
    async def test_added_event_is_valid_icalendar(self, tmp_dir: Path):
        """Round-trip through the real icalendar parser, not just our own reader —
        proves the file is importable elsewhere (Apple Calendar, Outlook, etc.)."""
        path = tmp_dir / "calendar.ics"
        await _calendar_add_event(str(path), "Valid Event", "2026-08-01T10:00:00", "2026-08-01T11:00:00")

        parsed = Calendar.from_ical(path.read_bytes())
        events = list(parsed.walk("VEVENT"))
        assert len(events) == 1
        assert str(events[0].get("summary")) == "Valid Event"
        assert events[0].get("uid") is not None


class TestFindConflicts:
    @pytest.mark.asyncio
    async def test_detects_overlap(self, tmp_dir: Path):
        path = _make_ics(tmp_dir, [("Busy", "2026-08-01T10:00:00", "2026-08-01T11:00:00")])
        result = await _calendar_find_conflicts(str(path), "2026-08-01T10:30:00", "2026-08-01T11:30:00")
        assert not result.is_error
        assert "Busy" in result.content
        assert result.metadata["conflict_count"] == 1

    @pytest.mark.asyncio
    async def test_no_conflict_for_adjacent_slot(self, tmp_dir: Path):
        path = _make_ics(tmp_dir, [("Busy", "2026-08-01T10:00:00", "2026-08-01T11:00:00")])
        result = await _calendar_find_conflicts(str(path), "2026-08-01T11:00:00", "2026-08-01T12:00:00")
        assert result.metadata["conflict_count"] == 0

    @pytest.mark.asyncio
    async def test_no_conflict_for_free_slot(self, tmp_dir: Path):
        path = _make_ics(tmp_dir, [("Busy", "2026-08-01T10:00:00", "2026-08-01T11:00:00")])
        result = await _calendar_find_conflicts(str(path), "2026-08-05T10:00:00", "2026-08-05T11:00:00")
        assert "No conflicts" in result.content


class TestFileContextRouting:
    @pytest.mark.asyncio
    async def test_build_file_context_formats_ics_readably(self, tmp_dir: Path):
        from elidia.cli.main import _build_file_context

        path = _make_ics(tmp_dir, [("Board meeting", "2026-08-01T09:00:00", "2026-08-01T10:00:00")])
        ctx = await _build_file_context((str(path),))
        assert "Board meeting" in ctx
        assert "BEGIN:VEVENT" not in ctx  # readable summary, not raw markup
