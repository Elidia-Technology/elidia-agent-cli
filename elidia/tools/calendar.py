"""Calendar Skills v1 — local .ics file read/write.

Local .ics instead of Google/Outlook Calendar for this pass, same
reasoning as Email Skills' app-password-instead-of-OAuth scope decision
(AIUT-2137, AIUT-2138): cloud calendar APIs need OAuth app registration
outside what a code change alone delivers, and can't be honestly
live-verified without it. Local .ics is genuinely useful today — Apple
Calendar, Outlook desktop, and many other apps can import/export it, and
the same parsing (icalendar library) carries over unchanged when cloud
OAuth is added later, since cloud calendar APIs also return/accept
.ics-format event data.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from pathlib import Path

from elidia.tools.base import ToolDefinition, ToolRegistry, ToolResult

logger = logging.getLogger(__name__)


def _parse_datetime(value: str) -> datetime:
    """Accept a few common formats: ISO 8601, with or without a time component."""
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise ValueError(f"Could not parse datetime: {value!r} (expected e.g. '2026-08-01T14:00:00' or '2026-08-01')")


async def _calendar_list_events(path: str, start: str | None = None, end: str | None = None) -> ToolResult:
    logger.debug(f"Entered into _calendar_list_events: path={path}, start={start}, end={end}")
    try:
        from icalendar import Calendar
    except ImportError:
        return ToolResult(content="icalendar not installed: pip install elidia-agent-cli[calendar]", is_error=True)

    p = Path(path)
    if not p.exists():
        return ToolResult(content=f"File not found: {path}", is_error=True)

    try:
        range_start = _parse_datetime(start) if start else None
        range_end = _parse_datetime(end) if end else None
    except ValueError as e:
        return ToolResult(content=str(e), is_error=True)

    try:
        cal = Calendar.from_ical(p.read_bytes())
    except Exception as e:
        return ToolResult(content=f"Could not parse {path}: {e}", is_error=True)

    lines = []
    count = 0
    for component in cal.walk("VEVENT"):
        ev_start = _as_datetime(component.get("dtstart"))
        ev_end = _as_datetime(component.get("dtend"))
        if range_start and ev_end and ev_end < range_start:
            continue
        if range_end and ev_start and ev_start > range_end:
            continue
        summary = str(component.get("summary", "(no title)"))
        lines.append(f"{summary} | {ev_start} - {ev_end} | uid={component.get('uid', '')}")
        count += 1

    if not lines:
        return ToolResult(content="No events found in the given range.")
    return ToolResult(content="\n".join(lines), metadata={"count": count})


def _as_datetime(prop) -> datetime | None:
    if prop is None:
        return None
    value = prop.dt
    if isinstance(value, datetime):
        return value
    # date-only (all-day event) — normalize to midnight for comparison
    return datetime(value.year, value.month, value.day)


async def _calendar_add_event(
    path: str, summary: str, start: str, end: str, description: str | None = None,
) -> ToolResult:
    logger.debug(f"Entered into _calendar_add_event: path={path}, summary={summary}")
    try:
        from icalendar import Calendar, Event
    except ImportError:
        return ToolResult(content="icalendar not installed: pip install elidia-agent-cli[calendar]", is_error=True)

    try:
        start_dt = _parse_datetime(start)
        end_dt = _parse_datetime(end)
    except ValueError as e:
        return ToolResult(content=str(e), is_error=True)
    if end_dt <= start_dt:
        return ToolResult(content="Event end must be after start.", is_error=True)

    p = Path(path)
    try:
        if p.exists():
            cal = Calendar.from_ical(p.read_bytes())
        else:
            cal = Calendar()
            cal.add("prodid", "-//Elidia Agent CLI//elidia.dev//")
            cal.add("version", "2.0")
    except Exception as e:
        return ToolResult(content=f"Could not parse existing {path}: {e}", is_error=True)

    event = Event()
    event.add("summary", summary)
    event.add("dtstart", start_dt)
    event.add("dtend", end_dt)
    event.add("dtstamp", datetime.now())
    event.add("uid", f"{uuid.uuid4()}@elidia-cli")
    if description:
        event.add("description", description)
    cal.add_component(event)

    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(cal.to_ical())
    except Exception as e:
        return ToolResult(content=f"Could not write {path}: {e}", is_error=True)

    return ToolResult(content=f"Added '{summary}' ({start_dt} - {end_dt}) to {path}")


async def _calendar_find_conflicts(path: str, start: str, end: str) -> ToolResult:
    logger.debug(f"Entered into _calendar_find_conflicts: path={path}, start={start}, end={end}")
    try:
        from icalendar import Calendar
    except ImportError:
        return ToolResult(content="icalendar not installed: pip install elidia-agent-cli[calendar]", is_error=True)

    p = Path(path)
    if not p.exists():
        return ToolResult(content=f"File not found: {path}", is_error=True)

    try:
        range_start = _parse_datetime(start)
        range_end = _parse_datetime(end)
    except ValueError as e:
        return ToolResult(content=str(e), is_error=True)

    try:
        cal = Calendar.from_ical(p.read_bytes())
    except Exception as e:
        return ToolResult(content=f"Could not parse {path}: {e}", is_error=True)

    conflicts = []
    for component in cal.walk("VEVENT"):
        ev_start = _as_datetime(component.get("dtstart"))
        ev_end = _as_datetime(component.get("dtend"))
        if ev_start is None or ev_end is None:
            continue
        # Standard interval overlap test.
        if ev_start < range_end and ev_end > range_start:
            summary = str(component.get("summary", "(no title)"))
            conflicts.append(f"{summary}: {ev_start} - {ev_end}")

    if not conflicts:
        return ToolResult(content=f"No conflicts for {range_start} - {range_end}.", metadata={"conflict_count": 0})
    return ToolResult(
        content="Conflicts found:\n" + "\n".join(conflicts),
        metadata={"conflict_count": len(conflicts)},
    )


def register_calendar_tools(registry: ToolRegistry) -> None:
    logger.debug("Entered into register_calendar_tools")

    registry.register(ToolDefinition(
        name="calendar_list_events", description="List events from a local .ics calendar file, optionally filtered by date range",
        parameters={"type": "object", "properties": {
            "path": {"type": "string", "description": "Path to the .ics file"},
            "start": {"type": "string", "description": "Range start, ISO format (e.g. 2026-08-01)"},
            "end": {"type": "string", "description": "Range end, ISO format"},
        }, "required": ["path"]},
        handler=_calendar_list_events, category="calendar",
    ))
    registry.register(ToolDefinition(
        name="calendar_add_event", description="Add a new event to a local .ics calendar file (creates the file if it doesn't exist)",
        parameters={"type": "object", "properties": {
            "path": {"type": "string", "description": "Path to the .ics file"},
            "summary": {"type": "string", "description": "Event title"},
            "start": {"type": "string", "description": "Start time, ISO format (e.g. 2026-08-01T14:00:00)"},
            "end": {"type": "string", "description": "End time, ISO format"},
            "description": {"type": "string", "description": "Optional event description"},
        }, "required": ["path", "summary", "start", "end"]},
        handler=_calendar_add_event, category="calendar",
    ))
    registry.register(ToolDefinition(
        name="calendar_find_conflicts", description="Check whether a proposed time range overlaps any existing event",
        parameters={"type": "object", "properties": {
            "path": {"type": "string", "description": "Path to the .ics file"},
            "start": {"type": "string", "description": "Proposed start time, ISO format"},
            "end": {"type": "string", "description": "Proposed end time, ISO format"},
        }, "required": ["path", "start", "end"]},
        handler=_calendar_find_conflicts, category="calendar",
    ))
