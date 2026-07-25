import logging

from rich.console import Console

logger = logging.getLogger(__name__)


def render_error(console: Console, message: str) -> None:
    logger.debug(f"Entered into render_error: {message}")
    console.print(f"[red]x[/red] {message}")


def render_success(console: Console, message: str) -> None:
    logger.debug(f"Entered into render_success: {message}")
    console.print(f"[green]v[/green] {message}")


def render_warning(console: Console, message: str) -> None:
    logger.debug(f"Entered into render_warning: {message}")
    console.print(f"[yellow]![/yellow] {message}")


def render_info(console: Console, message: str) -> None:
    logger.debug(f"Entered into render_info: {message}")
    console.print(f"[blue]i[/blue] {message}")


def render_status_bar(console: Console, model: str, mode: str, tokens: int, cost_dt: float) -> None:
    logger.debug(f"Entered into render_status_bar: model={model}, mode={mode}")
    console.print(
        f"[dim]{model} | {mode} | {tokens:,} tokens | {cost_dt:.1f} DT[/dim]"
    )
