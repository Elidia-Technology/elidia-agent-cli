"""Daemon mode — background task manager with watchers, schedules, and webhooks.

Manages long-running background tasks:
  - File watchers: monitor directories for changes
  - Scheduled tasks: run at intervals or cron-like expressions
  - Webhook listener: HTTP endpoint that triggers workflows
"""
from __future__ import annotations

import asyncio
import datetime
import json
import logging
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class DaemonTask:
    id: str
    name: str
    type: str  # "watcher", "schedule", "webhook"
    config: dict[str, Any] = field(default_factory=dict)
    status: str = "stopped"  # "running", "stopped", "error"
    last_run: float = 0.0
    run_count: int = 0
    error: str = ""


@dataclass
class DaemonEvent:
    kind: str  # "task_start", "task_stop", "task_trigger", "task_error", "status"
    data: Any = None


class DaemonManager:
    """Manages background daemon tasks."""

    def __init__(self) -> None:
        logger.debug("Entered into DaemonManager.__init__")
        self._tasks: dict[str, DaemonTask] = {}
        self._handles: dict[str, asyncio.Task[None]] = {}
        self._callbacks: dict[str, Callable[..., Coroutine[Any, Any, None]]] = {}
        self._running = False
        self._webhook_server: asyncio.Server | None = None

    async def start(self) -> None:
        logger.debug("Entered into DaemonManager.start")
        self._running = True
        for _task_id, task in self._tasks.items():
            if task.status == "stopped":
                await self._start_task(task)

    async def stop(self) -> None:
        logger.debug("Entered into DaemonManager.stop")
        self._running = False
        for task_id in list(self._handles.keys()):
            await self._stop_task(task_id)
        if self._webhook_server:
            self._webhook_server.close()
            await self._webhook_server.wait_closed()
            self._webhook_server = None

    def add_watcher(
        self,
        name: str,
        path: str,
        patterns: list[str] | None = None,
        interval: float = 2.0,
        callback: Callable[..., Coroutine[Any, Any, None]] | None = None,
    ) -> str:
        logger.debug(f"Entered into add_watcher: name={name}, path={path}")
        task_id = f"watch_{name}_{int(time.time())}"
        task = DaemonTask(
            id=task_id,
            name=name,
            type="watcher",
            config={
                "path": path,
                "patterns": patterns or ["*"],
                "interval": interval,
            },
        )
        self._tasks[task_id] = task
        if callback:
            self._callbacks[task_id] = callback
        return task_id

    def add_schedule(
        self,
        name: str,
        interval_seconds: float,
        command: str = "",
        callback: Callable[..., Coroutine[Any, Any, None]] | None = None,
    ) -> str:
        logger.debug(f"Entered into add_schedule: name={name}, interval={interval_seconds}")
        task_id = f"sched_{name}_{int(time.time())}"
        task = DaemonTask(
            id=task_id,
            name=name,
            type="schedule",
            config={
                "interval": interval_seconds,
                "command": command,
            },
        )
        self._tasks[task_id] = task
        if callback:
            self._callbacks[task_id] = callback
        return task_id

    def add_cron_schedule(
        self,
        name: str,
        cron_expr: str,
        command: str = "",
        callback: Callable[..., Coroutine[Any, Any, None]] | None = None,
    ) -> str:
        """Add a cron-expression-based schedule.

        Supports standard 5-field cron syntax:
          minute hour day-of-month month day-of-week

        Examples:
          "0 9 * * 1-5"  → 9:00 AM weekdays
          "*/15 * * * *"  → every 15 minutes
          "0 0 1 * *"     → midnight on the 1st of each month
        """
        logger.debug(f"Entered into add_cron_schedule: name={name}, cron={cron_expr}")
        _validate_cron_expr(cron_expr)
        task_id = f"cron_{name}_{int(time.time())}"
        task = DaemonTask(
            id=task_id,
            name=name,
            type="schedule",
            config={
                "cron": cron_expr,
                "command": command,
            },
        )
        self._tasks[task_id] = task
        if callback:
            self._callbacks[task_id] = callback
        return task_id

    def add_webhook(
        self,
        name: str,
        path: str = "/webhook",
        port: int = 8765,
        callback: Callable[..., Coroutine[Any, Any, None]] | None = None,
    ) -> str:
        logger.debug(f"Entered into add_webhook: name={name}, path={path}, port={port}")
        task_id = f"hook_{name}_{int(time.time())}"
        task = DaemonTask(
            id=task_id,
            name=name,
            type="webhook",
            config={
                "path": path,
                "port": port,
            },
        )
        self._tasks[task_id] = task
        if callback:
            self._callbacks[task_id] = callback
        return task_id

    def add_coding_task(
        self,
        name: str,
        description: str,
        cron: str,
        working_dir: str = ".",
        model: str = "claude-sonnet-4-6",
        max_iterations: int = 25,
        auto_commit: bool = False,
        callback: Callable[..., Coroutine[Any, Any, None]] | None = None,
    ) -> str:
        """Register an autonomous coding task (AIUT-2155). Runs on a cron
        schedule — the callback receives the task config and should
        initialise an AgentLoop, run the agent against the description,
        and report results."""
        logger.debug(f"Entered into add_coding_task: name={name}, cron={cron}")
        import asyncio as _asyncio
        task_id = f"code_{name}_{int(time.time())}"
        task = DaemonTask(
            id=task_id, name=name, type="coding",
            config={"description": description, "cron": cron, "working_dir": working_dir, "model": model, "max_iterations": max_iterations, "auto_commit": auto_commit},
        )
        self._tasks[task_id] = task

        return self.add_cron_schedule(name=name, cron_expr=cron, command="echo ok", callback=callback)

    async def remove_task(self, task_id: str) -> bool:
        logger.debug(f"Entered into remove_task: id={task_id}")
        if task_id in self._handles:
            await self._stop_task(task_id)
        if task_id in self._tasks:
            del self._tasks[task_id]
            self._callbacks.pop(task_id, None)
            return True
        return False

    def list_tasks(self) -> list[DaemonTask]:
        logger.debug("Entered into list_tasks")
        return list(self._tasks.values())

    def get_status(self) -> dict[str, Any]:
        logger.debug("Entered into get_status")
        return {
            "running": self._running,
            "task_count": len(self._tasks),
            "active": sum(1 for t in self._tasks.values() if t.status == "running"),
            "tasks": [
                {
                    "id": t.id,
                    "name": t.name,
                    "type": t.type,
                    "status": t.status,
                    "run_count": t.run_count,
                    "last_run": t.last_run,
                    "error": t.error,
                }
                for t in self._tasks.values()
            ],
        }

    async def _start_task(self, task: DaemonTask) -> None:
        logger.debug(f"Entered into _start_task: id={task.id}, type={task.type}")
        if task.id in self._handles:
            return

        if task.type == "watcher":
            self._handles[task.id] = asyncio.create_task(self._run_watcher(task))
        elif task.type == "schedule":
            self._handles[task.id] = asyncio.create_task(self._run_schedule(task))
        elif task.type == "webhook":
            self._handles[task.id] = asyncio.create_task(self._run_webhook(task))

        task.status = "running"

    async def _stop_task(self, task_id: str) -> None:
        logger.debug(f"Entered into _stop_task: id={task_id}")
        handle = self._handles.pop(task_id, None)
        if handle and not handle.done():
            handle.cancel()
            try:
                await handle
            except asyncio.CancelledError:
                pass
        if task_id in self._tasks:
            self._tasks[task_id].status = "stopped"

    async def _run_watcher(self, task: DaemonTask) -> None:
        logger.debug(f"Entered into _run_watcher: id={task.id}")
        watch_path = Path(task.config["path"])
        interval = task.config.get("interval", 2.0)
        patterns = task.config.get("patterns", ["*"])

        file_states: dict[str, float] = {}
        if watch_path.is_dir():
            for pattern in patterns:
                for f in watch_path.rglob(pattern):
                    if f.is_file():
                        file_states[str(f)] = f.stat().st_mtime

        try:
            while self._running:
                await asyncio.sleep(interval)
                if not watch_path.exists():
                    continue

                current: dict[str, float] = {}
                for pattern in patterns:
                    for f in watch_path.rglob(pattern):
                        if f.is_file():
                            current[str(f)] = f.stat().st_mtime

                changed = [p for p, mt in current.items() if file_states.get(p) != mt]
                deleted = [p for p in file_states if p not in current]

                if changed or deleted:
                    task.run_count += 1
                    task.last_run = time.time()
                    callback = self._callbacks.get(task.id)
                    if callback:
                        try:
                            await callback(changed=changed, deleted=deleted, task=task)
                        except Exception as e:
                            task.error = str(e)
                            logger.warning(f"Watcher callback error: {e}")

                file_states = current

        except asyncio.CancelledError:
            pass

    async def _run_schedule(self, task: DaemonTask) -> None:
        logger.debug(f"Entered into _run_schedule: id={task.id}")
        cron_expr = task.config.get("cron", "")
        interval = task.config.get("interval", 0.0)
        command = task.config.get("command", "")

        try:
            while self._running:
                if cron_expr:
                    sleep_s = _next_cron_interval(cron_expr)
                elif interval > 0:
                    sleep_s = interval
                else:
                    sleep_s = 60.0

                await asyncio.sleep(sleep_s)
                task.run_count += 1
                task.last_run = time.time()

                callback = self._callbacks.get(task.id)
                if callback:
                    try:
                        await callback(task=task)
                    except Exception as e:
                        task.error = str(e)
                        logger.warning(f"Schedule callback error: {e}")
                elif command:
                    try:
                        proc = await asyncio.create_subprocess_shell(
                            command,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                        )
                        await asyncio.wait_for(proc.communicate(), timeout=30)
                    except Exception as e:
                        task.error = str(e)
                        logger.warning(f"Schedule command error: {e}")

        except asyncio.CancelledError:
            pass

    async def _run_webhook(self, task: DaemonTask) -> None:
        logger.debug(f"Entered into _run_webhook: id={task.id}")
        port = task.config.get("port", 8765)
        expected_path = task.config.get("path", "/webhook")

        async def handle_request(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                request_line = await asyncio.wait_for(reader.readline(), timeout=10)
                line = request_line.decode("utf-8", errors="replace").strip()
                parts = line.split(" ")
                method = parts[0] if parts else "GET"
                path = parts[1] if len(parts) > 1 else "/"

                headers: dict[str, str] = {}
                content_length = 0
                while True:
                    header_line = await asyncio.wait_for(reader.readline(), timeout=5)
                    header = header_line.decode("utf-8", errors="replace").strip()
                    if not header:
                        break
                    if ":" in header:
                        key, val = header.split(":", 1)
                        headers[key.strip().lower()] = val.strip()
                        if key.strip().lower() == "content-length":
                            content_length = int(val.strip())

                body = b""
                if content_length > 0:
                    body = await asyncio.wait_for(reader.read(min(content_length, 65536)), timeout=10)

                if path == expected_path and method == "POST":
                    task.run_count += 1
                    task.last_run = time.time()

                    payload: dict[str, Any] = {}
                    if body:
                        try:
                            payload = json.loads(body)
                        except json.JSONDecodeError:
                            payload = {"raw": body.decode("utf-8", errors="replace")}

                    callback = self._callbacks.get(task.id)
                    if callback:
                        try:
                            await callback(payload=payload, task=task)
                        except Exception as e:
                            task.error = str(e)

                    response = 'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n{"status": "ok"}'
                else:
                    response = 'HTTP/1.1 404 Not Found\r\nContent-Type: application/json\r\n\r\n{"error": "not found"}'

                writer.write(response.encode())
                await writer.drain()
            except Exception as e:
                logger.debug(f"Webhook request error: {e}")
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass

        try:
            self._webhook_server = await asyncio.start_server(handle_request, "127.0.0.1", port)
            logger.info(f"Webhook listener started on 127.0.0.1:{port}{expected_path}")
            async with self._webhook_server:
                await self._webhook_server.serve_forever()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            task.status = "error"
            task.error = str(e)
            logger.warning(f"Webhook server error: {e}")


# --- Cron helpers ---


def _validate_cron_expr(expr: str) -> None:
    """Validate a 5-field cron expression. Raises ValueError on invalid syntax."""
    fields = expr.strip().split()
    if len(fields) != 5:
        raise ValueError(f"Cron expression must have 5 fields, got {len(fields)}: {expr}")
    ranges = [
        (0, 59, "minute"), (0, 23, "hour"), (1, 31, "day"),
        (1, 12, "month"), (0, 7, "weekday"),
    ]
    for field_val, (lo, hi, name) in zip(fields, ranges, strict=True):
        for part in field_val.split(","):
            part = part.strip()
            if part == "*":
                continue
            if "/" in part:
                base, _, step_str = part.partition("/")
                if base == "*":
                    base = str(lo)
                if not step_str.isdigit():
                    raise ValueError(f"Invalid step in cron field '{name}': {part}")
                part = base
            if "-" in part:
                a, _, b = part.partition("-")
                part = a
            if part == "*":
                continue
            if not part.isdigit():
                raise ValueError(f"Invalid cron field '{name}': {part}")
            n = int(part)
            if not (lo <= n <= hi):
                raise ValueError(f"Cron field '{name}' value {n} out of range [{lo},{hi}]")


def _cron_field_matches(value: int, field: str, lo: int) -> bool:
    """Check if a single value matches a cron field expression."""
    if field == "*":
        return True
    for alt in field.split(","):
        alt = alt.strip()
        step = 1
        if "/" in alt:
            base_str, _, step_str = alt.partition("/")
            step = int(step_str)
            alt = base_str
        if "-" in alt:
            a_str, _, b_str = alt.partition("-")
            a, b = int(a_str), int(b_str)
            if a <= value <= b:
                return (value - a) % step == 0
        elif alt == "*":
            return value % step == 0
        else:
            if int(alt) == value:
                return True
    return False


def _next_cron_interval(cron_expr: str) -> float:
    """Calculate seconds until the next cron trigger time.

    Finds the next datetime that matches the cron expression and returns
    the sleep interval needed. Capped at 60s for responsiveness.
    """
    logger.debug(f"Entered into _next_cron_interval: cron={cron_expr}")
    now = datetime.datetime.now()
    fields = cron_expr.strip().split()
    minute_f, hour_f, day_f, month_f, weekday_f = fields

    # Check each minute for up to 2 hours ahead
    current = now.replace(second=0, microsecond=0) + datetime.timedelta(minutes=1)
    deadline = now + datetime.timedelta(hours=2)

    while current <= deadline:
        wd = (current.weekday() + 1) % 7  # Convert to 0=Sun for cron compat
        if (_cron_field_matches(current.minute, minute_f, 0)
                and _cron_field_matches(current.hour, hour_f, 0)
                and _cron_field_matches(current.day, day_f, 1)
                and _cron_field_matches(current.month, month_f, 1)
                and _cron_field_matches(wd, weekday_f, 0)):
            delta = (current - now).total_seconds()
            return min(max(delta, 1.0), 60.0)

        current += datetime.timedelta(minutes=1)

    # No match in next 2 hours — check every 60s
    return 60.0
