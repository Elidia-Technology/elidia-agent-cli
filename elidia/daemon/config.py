"""Declarative task config for the background daemon.

DaemonManager itself has no persistence — tasks are added programmatically
via add_watcher/add_schedule/add_cron_schedule/add_webhook. For a real
background process (started once, outliving the CLI invocation that
started it) to know what to run, those declarations need to live
somewhere on disk that survives process boundaries. TOML file at
~/.elidia/daemon.toml, matching this project's existing config.toml
convention.
"""
from __future__ import annotations

import logging
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class WatcherConfig:
    name: str
    path: str
    patterns: list[str] = field(default_factory=lambda: ["*"])
    interval: float = 2.0


@dataclass
class ScheduleConfig:
    name: str
    command: str = ""
    cron: str = ""
    interval_seconds: float = 0.0


@dataclass
class WebhookConfig:
    name: str
    path: str = "/webhook"
    port: int = 8765


@dataclass
class CodingTaskConfig:
    name: str
    description: str  # natural-language task prompt
    schedule_cron: str = ""  # cron expression, e.g. "0 */6 * * *"
    working_dir: str = "."
    model: str = "claude-sonnet-4-6"
    max_iterations: int = 25
    auto_commit: bool = False  # whether to git commit changes on success


@dataclass
class DaemonConfig:
    watchers: list[WatcherConfig] = field(default_factory=list)
    schedules: list[ScheduleConfig] = field(default_factory=list)
    webhooks: list[WebhookConfig] = field(default_factory=list)
    coding_tasks: list[CodingTaskConfig] = field(default_factory=list)


def load_daemon_config(path: Path) -> DaemonConfig:
    """Load declared daemon tasks from a TOML file. Missing file -> empty config (valid — a daemon with nothing configured yet)."""
    logger.debug(f"Entered into load_daemon_config: path={path}")
    if not path.exists():
        return DaemonConfig()

    with open(path, "rb") as f:
        raw = tomllib.load(f)

    watchers = [
        WatcherConfig(
            name=w["name"], path=w["path"],
            patterns=w.get("patterns", ["*"]), interval=w.get("interval", 2.0),
        )
        for w in raw.get("watcher", [])
    ]
    schedules = [
        ScheduleConfig(
            name=s["name"], command=s.get("command", ""),
            cron=s.get("cron", ""), interval_seconds=s.get("interval_seconds", 0.0),
        )
        for s in raw.get("schedule", [])
    ]
    webhooks = [
        WebhookConfig(name=w["name"], path=w.get("path", "/webhook"), port=w.get("port", 8765))
        for w in raw.get("webhook", [])
    ]
    coding_tasks = [
        CodingTaskConfig(
            name=c["name"], description=c["description"],
            schedule_cron=c.get("schedule_cron", ""),
            working_dir=c.get("working_dir", "."),
            model=c.get("model", "claude-sonnet-4-6"),
            max_iterations=c.get("max_iterations", 25),
            auto_commit=c.get("auto_commit", False),
        )
        for c in raw.get("coding_task", [])
    ]

    logger.info(f"Loaded daemon config: {len(watchers)} watchers, {len(schedules)} schedules, {len(webhooks)} webhooks, {len(coding_tasks)} coding tasks")
    return DaemonConfig(watchers=watchers, schedules=schedules, webhooks=webhooks, coding_tasks=coding_tasks)


def write_example_daemon_config(path: Path) -> None:
    """Write a commented-out example config — used by `elidia daemon init`."""
    logger.debug(f"Entered into write_example_daemon_config: path={path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# Elidia daemon task configuration.\n"
        "# Uncomment and edit, then run `elidia daemon start` (or `elidia daemon restart`\n"
        "# if it's already running) to pick up changes.\n\n"
        "# [[watcher]]\n"
        '# name = "src-watch"\n'
        '# path = "./src"\n'
        '# patterns = ["*.py"]\n'
        "# interval = 2.0\n\n"
        "# [[schedule]]\n"
        '# name = "nightly-report"\n'
        '# cron = "0 9 * * *"\n'
        '# command = "echo hello"\n\n'
        "# [[webhook]]\n"
        '# name = "deploy-hook"\n'
        '# path = "/webhook"\n'
        "# port = 8765\n\n"
        "# [[coding_task]]\n"
        '# name = "auto-fix-lints"\n'
        '# description = "Fix all lint errors in the current project and commit the changes"\n'
        '# schedule_cron = "0 */6 * * *"\n'
        '# working_dir = "."\n'
        '# model = "claude-sonnet-4-6"\n'
        '# max_iterations = 25\n'
        "# auto_commit = false\n",
        encoding="utf-8",
    )
