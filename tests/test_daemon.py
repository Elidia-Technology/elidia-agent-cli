"""Tests for elidia.daemon.manager — background task management."""
import asyncio

import pytest

from elidia.daemon.manager import DaemonManager, DaemonTask


class TestDaemonManager:
    def test_add_watcher(self):
        dm = DaemonManager()
        task_id = dm.add_watcher("w", "/tmp", patterns=["*.py"])
        assert task_id.startswith("watch_")
        tasks = dm.list_tasks()
        assert len(tasks) == 1
        assert tasks[0].type == "watcher"

    def test_add_schedule(self):
        dm = DaemonManager()
        task_id = dm.add_schedule("s", 60, "echo hello")
        assert task_id.startswith("sched_")
        tasks = dm.list_tasks()
        assert len(tasks) == 1
        assert tasks[0].type == "schedule"

    def test_add_webhook(self):
        dm = DaemonManager()
        task_id = dm.add_webhook("h", "/hook", 9999)
        assert task_id.startswith("hook_")
        tasks = dm.list_tasks()
        assert len(tasks) == 1
        assert tasks[0].type == "webhook"

    def test_list_tasks(self):
        dm = DaemonManager()
        dm.add_watcher("w", "/tmp")
        dm.add_schedule("s", 60, "echo")
        assert len(dm.list_tasks()) == 2

    @pytest.mark.asyncio
    async def test_remove_task(self):
        dm = DaemonManager()
        tid = dm.add_watcher("w", "/tmp")
        assert len(dm.list_tasks()) == 1
        result = await dm.remove_task(tid)
        assert result
        assert len(dm.list_tasks()) == 0

    @pytest.mark.asyncio
    async def test_remove_nonexistent(self):
        dm = DaemonManager()
        result = await dm.remove_task("nonexistent")
        assert not result

    def test_get_status(self):
        dm = DaemonManager()
        dm.add_watcher("w", "/tmp")
        dm.add_schedule("s", 60)
        status = dm.get_status()
        assert status["task_count"] == 2
        assert status["active"] == 0
        assert not status["running"]
        assert len(status["tasks"]) == 2

    def test_task_initial_state(self):
        dm = DaemonManager()
        tid = dm.add_watcher("w", "/tmp")
        tasks = dm.list_tasks()
        assert tasks[0].status == "stopped"
        assert tasks[0].run_count == 0
        assert tasks[0].error == ""


class TestDaemonTask:
    def test_dataclass_fields(self):
        t = DaemonTask(id="t1", name="test", type="watcher")
        assert t.status == "stopped"
        assert t.run_count == 0
        assert t.last_run == 0.0
