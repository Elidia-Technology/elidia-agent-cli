"""Progress indicators — spinners and progress bars for async operations.

Provides reusable wrappers around Rich's progress display components:
  - Spinner: for indeterminate-length operations (LLM generation, search)
  - ProgressBar: for determinate operations (file download, batch processing)
  - StageTracker: for multi-stage pipelines (research, workflow)
"""
from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager

from rich.console import Console
from rich.live import Live
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.spinner import Spinner
from rich.text import Text

logger = logging.getLogger(__name__)


@contextmanager
def spinner(
    console: Console,
    message: str = "Working...",
    spinner_name: str = "dots",
    style: str = "cyan",
) -> Iterator[Live]:
    logger.debug(f"Entered into spinner: message={message}")
    display = Text.assemble(
        (" ", ""),
        (message, style),
    )
    spin = Spinner(spinner_name, text=display, style=style)
    with Live(spin, console=console, transient=True, refresh_per_second=12) as live:
        yield live


@asynccontextmanager
async def async_spinner(
    console: Console,
    message: str = "Working...",
    spinner_name: str = "dots",
    style: str = "cyan",
) -> AsyncIterator[Live]:
    logger.debug(f"Entered into async_spinner: message={message}")
    display = Text.assemble(
        (" ", ""),
        (message, style),
    )
    spin = Spinner(spinner_name, text=display, style=style)
    with Live(spin, console=console, transient=True, refresh_per_second=12) as live:
        yield live


def create_progress_bar(
    console: Console,
    transient: bool = True,
) -> Progress:
    logger.debug("Entered into create_progress_bar")
    return Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=30),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console,
        transient=transient,
    )


class StageTracker:
    """Tracks progress through a multi-stage pipeline."""

    def __init__(self, console: Console, stages: list[str]) -> None:
        logger.debug(f"Entered into StageTracker.__init__: stages={len(stages)}")
        self._console = console
        self._stages = stages
        self._current: int = -1
        self._completed: list[bool] = [False] * len(stages)
        self._timings: list[float] = [0.0] * len(stages)
        self._start_time: float = 0.0

    def start(self) -> None:
        logger.debug("Entered into StageTracker.start")
        self._start_time = time.monotonic()

    def advance(self, stage_index: int | None = None) -> None:
        logger.debug(f"Entered into StageTracker.advance: index={stage_index}")
        if self._current >= 0 and self._current < len(self._stages):
            self._completed[self._current] = True
            self._timings[self._current] = time.monotonic() - self._start_time

        if stage_index is not None:
            self._current = stage_index
        else:
            self._current += 1

        if self._current < len(self._stages):
            self._start_time = time.monotonic()
            name = self._stages[self._current]
            progress = f"[{self._current + 1}/{len(self._stages)}]"
            self._console.print(f"  [cyan]{progress}[/cyan] {name}")

    def complete(self) -> None:
        logger.debug("Entered into StageTracker.complete")
        if self._current >= 0 and self._current < len(self._stages):
            self._completed[self._current] = True
            self._timings[self._current] = time.monotonic() - self._start_time

    def render_summary(self) -> None:
        logger.debug("Entered into StageTracker.render_summary")
        total_time = sum(self._timings)
        completed = sum(1 for c in self._completed if c)
        self._console.print(
            f"\n[bold]Pipeline:[/bold] {completed}/{len(self._stages)} stages "
            f"({total_time:.1f}s)"
        )

    @property
    def current_stage(self) -> str | None:
        if 0 <= self._current < len(self._stages):
            return self._stages[self._current]
        return None

    @property
    def is_complete(self) -> bool:
        return all(self._completed)


class DownloadTracker:
    """Tracks file download progress."""

    def __init__(self, console: Console) -> None:
        logger.debug("Entered into DownloadTracker.__init__")
        self._console = console
        self._progress: Progress | None = None
        self._task_id: TaskID | None = None

    def start(self, filename: str, total_bytes: int) -> Progress:
        logger.debug(f"Entered into DownloadTracker.start: file={filename}, bytes={total_bytes}")
        self._progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(bar_width=30),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeElapsedColumn(),
            console=self._console,
            transient=True,
        )
        self._task_id = self._progress.add_task(filename, total=total_bytes)
        return self._progress

    def update(self, bytes_downloaded: int) -> None:
        if self._progress and self._task_id is not None:
            self._progress.update(self._task_id, completed=bytes_downloaded)

    def finish(self) -> None:
        logger.debug("Entered into DownloadTracker.finish")
        if self._progress and self._task_id is not None:
            self._progress.update(self._task_id, completed=self._progress.tasks[0].total or 0)
