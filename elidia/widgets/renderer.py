"""CLI widget renderer — renders WidgetRequests using prompt_toolkit.

Converts the abstract widget protocol into interactive terminal prompts
using prompt_toolkit for text input and rich for display formatting.
"""
from __future__ import annotations

import logging
from typing import Any

from rich.console import Console
from rich.panel import Panel

from elidia.widgets.protocol import (
    WidgetRequest,
    WidgetResponse,
    WidgetType,
    validate_response,
)

logger = logging.getLogger(__name__)


class CliWidgetRenderer:
    """Renders widget requests as interactive CLI prompts."""

    def __init__(self, console: Console | None = None) -> None:
        logger.debug("Entered into CliWidgetRenderer.__init__")
        self._console = console or Console()

    def render(self, request: WidgetRequest) -> WidgetResponse:
        logger.debug(f"Entered into render: id={request.id}, type={request.type.value}")

        if request.description:
            self._console.print(Panel(
                f"[bold]{request.title}[/bold]\n{request.description}",
                border_style="cyan",
            ))
        else:
            self._console.print(f"\n[bold cyan]{request.title}[/bold cyan]")

        while True:
            try:
                if request.type == WidgetType.TEXT:
                    response = self._render_text(request)
                elif request.type == WidgetType.SELECT:
                    response = self._render_select(request)
                elif request.type == WidgetType.CONFIRM:
                    response = self._render_confirm(request)
                elif request.type == WidgetType.MCQ:
                    response = self._render_mcq(request)
                elif request.type == WidgetType.PASSWORD:
                    response = self._render_password(request)
                elif request.type == WidgetType.FORM:
                    response = self._render_form(request)
                else:
                    self._console.print(f"[yellow]Unknown widget type: {request.type}[/yellow]")
                    return WidgetResponse(id=request.id, cancelled=True)
            except (EOFError, KeyboardInterrupt):
                return WidgetResponse(id=request.id, cancelled=True)

            errors = validate_response(request, response)
            if not errors:
                return response

            for err in errors:
                self._console.print(f"  [red]{err}[/red]")

    def _render_text(self, request: WidgetRequest) -> WidgetResponse:
        logger.debug(f"Entered into _render_text: id={request.id}")
        field = request.fields[0] if request.fields else None
        default = request.default or (field.default if field else "")

        prompt = "  > "
        if default:
            prompt = f"  [{default}] > "

        if field and field.multiline:
            self._console.print("  [dim](Enter text, blank line to finish)[/dim]")
            lines: list[str] = []
            while True:
                line = input("  | ")
                if not line and lines:
                    break
                lines.append(line)
            value = "\n".join(lines)
        else:
            value = input(prompt).strip()
            if not value and default:
                value = default

        return WidgetResponse(id=request.id, values={"value": value})

    def _render_select(self, request: WidgetRequest) -> WidgetResponse:
        logger.debug(f"Entered into _render_select: id={request.id}")
        active_options = [o for o in request.options if not o.disabled]

        for i, opt in enumerate(active_options, 1):
            desc = f" — {opt.description}" if opt.description else ""
            marker = " *" if opt.value == request.default else ""
            self._console.print(f"  [cyan]{i}[/cyan]) {opt.label}{desc}{marker}")

        prompt = f"  Select [1-{len(active_options)}]: "
        raw = input(prompt).strip()

        if raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < len(active_options):
                return WidgetResponse(id=request.id, values={"value": active_options[idx].value})

        for opt in active_options:
            if raw.lower() == opt.value.lower() or raw.lower() == opt.label.lower():
                return WidgetResponse(id=request.id, values={"value": opt.value})

        return WidgetResponse(id=request.id, values={"value": raw})

    def _render_confirm(self, request: WidgetRequest) -> WidgetResponse:
        logger.debug(f"Entered into _render_confirm: id={request.id}")
        default = request.default.lower() if request.default else "no"
        hint = "[Y/n]" if default == "yes" else "[y/N]"

        raw = input(f"  {hint} > ").strip().lower()

        if not raw:
            value = default
        elif raw in ("y", "yes"):
            value = "yes"
        elif raw in ("n", "no"):
            value = "no"
        else:
            value = raw

        return WidgetResponse(id=request.id, values={"value": value, "confirmed": value == "yes"})

    def _render_mcq(self, request: WidgetRequest) -> WidgetResponse:
        logger.debug(f"Entered into _render_mcq: id={request.id}")
        active_options = [o for o in request.options if not o.disabled]

        for i, opt in enumerate(active_options, 1):
            desc = f" — {opt.description}" if opt.description else ""
            self._console.print(f"  [cyan]{i}[/cyan]) [ ] {opt.label}{desc}")

        self._console.print("  [dim]Enter numbers separated by commas (e.g. 1,3,4)[/dim]")
        raw = input("  Select: ").strip()

        selected: list[str] = []
        for part in raw.split(","):
            part = part.strip()
            if part.isdigit():
                idx = int(part) - 1
                if 0 <= idx < len(active_options):
                    selected.append(active_options[idx].value)
            else:
                for opt in active_options:
                    if part.lower() == opt.value.lower() or part.lower() == opt.label.lower():
                        selected.append(opt.value)

        return WidgetResponse(id=request.id, values={"selections": selected})

    def _render_password(self, request: WidgetRequest) -> WidgetResponse:
        logger.debug(f"Entered into _render_password: id={request.id}")
        import getpass
        value = getpass.getpass("  Password: ")
        return WidgetResponse(id=request.id, values={"value": value})

    def _render_form(self, request: WidgetRequest) -> WidgetResponse:
        logger.debug(f"Entered into _render_form: id={request.id}")
        values: dict[str, Any] = {}

        for field in request.fields:
            required_marker = " [red]*[/red]" if field.required else ""
            self._console.print(f"\n  [bold]{field.label}[/bold]{required_marker}")

            if field.type == WidgetType.SELECT and field.options:
                sub_request = WidgetRequest(
                    id=f"{request.id}_{field.name}",
                    type=WidgetType.SELECT,
                    title=field.label,
                    options=field.options,
                    default=field.default,
                )
                sub_response = self._render_select(sub_request)
                values[field.name] = sub_response.values.get("value", "")

            elif field.type == WidgetType.MCQ and field.options:
                sub_request = WidgetRequest(
                    id=f"{request.id}_{field.name}",
                    type=WidgetType.MCQ,
                    title=field.label,
                    options=field.options,
                )
                sub_response = self._render_mcq(sub_request)
                values[field.name] = sub_response.values.get("selections", [])

            elif field.type == WidgetType.CONFIRM:
                sub_request = WidgetRequest(
                    id=f"{request.id}_{field.name}",
                    type=WidgetType.CONFIRM,
                    title=field.label,
                    default=field.default,
                )
                sub_response = self._render_confirm(sub_request)
                values[field.name] = sub_response.values.get("confirmed", False)

            elif field.type == WidgetType.PASSWORD:
                sub_request = WidgetRequest(
                    id=f"{request.id}_{field.name}",
                    type=WidgetType.PASSWORD,
                    title=field.label,
                )
                sub_response = self._render_password(sub_request)
                values[field.name] = sub_response.values.get("value", "")

            else:
                prompt = "  > "
                if field.default:
                    prompt = f"  [{field.default}] > "
                if field.placeholder:
                    self._console.print(f"  [dim]({field.placeholder})[/dim]")
                val = input(prompt).strip()
                if not val and field.default:
                    val = field.default
                values[field.name] = val

        return WidgetResponse(id=request.id, values=values)
