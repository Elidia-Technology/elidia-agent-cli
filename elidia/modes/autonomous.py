"""Autonomous mode — multi-step planning, task decomposition, and self-correction.

Takes a complex task, uses an LLM to decompose it into subtasks, executes
each subtask via the Agent Loop, observes results, and replans if needed.
Supports checkpoint/resume for long-running tasks.
"""
from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from elidia.api.client import AiUtilsClient, ChatMessage

logger = logging.getLogger(__name__)

DECOMPOSE_PROMPT = (
    "You are a task planner. Break the following complex task into a sequence of "
    "concrete, actionable subtasks that can be executed one at a time.\n\n"
    "Rules:\n"
    "- Each subtask should be independently verifiable.\n"
    "- Order subtasks by dependency — earlier subtasks should not depend on later ones.\n"
    "- Be specific: 'Read file X and extract function Y' not 'understand the code'.\n"
    "- Include a verification step for each subtask.\n"
    "- Keep it to 3-10 subtasks.\n\n"
    "Return ONLY a JSON array of objects:\n"
    '[{{"id": 1, "task": "<description>", "verify": "<how to verify success>"}}, ...]\n\n'
    "Task: {task}\n\nJSON:"
)

REPLAN_PROMPT = (
    "You are a task planner reviewing progress on a complex task.\n\n"
    "Original task: {original_task}\n\n"
    "Completed subtasks:\n{completed}\n\n"
    "Current subtask FAILED:\n"
    "  Task: {failed_task}\n"
    "  Error: {error}\n\n"
    "Remaining subtasks:\n{remaining}\n\n"
    "Should we:\n"
    "1. RETRY the failed subtask with a different approach\n"
    "2. SKIP it and continue with remaining tasks\n"
    "3. REPLAN — create new subtasks to work around the failure\n\n"
    "Return ONLY a JSON object:\n"
    '{{"action": "retry|skip|replan", "reason": "<short explanation>", '
    '"new_tasks": [<only if action=replan, list of new task objects>]}}\n\nJSON:'
)


class SubtaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class Subtask:
    id: int
    task: str
    verify: str
    status: SubtaskStatus = SubtaskStatus.PENDING
    result: str = ""
    error: str = ""
    attempt: int = 0


@dataclass
class AutonomousState:
    original_task: str
    subtasks: list[Subtask] = field(default_factory=list)
    current_index: int = 0
    total_dt: float = 0.0
    started_at: float = 0.0
    replans: int = 0


@dataclass
class AutonomousEvent:
    kind: str  # "plan", "subtask_start", "subtask_result", "replan", "progress", "error", "done"
    data: Any = None


class AutonomousExecutor:
    """Decomposes a task, executes subtasks sequentially, and replans on failure."""

    def __init__(
        self,
        client: AiUtilsClient,
        planner_model: str = "deepseek-chat",
        max_replans: int = 3,
        max_retries_per_task: int = 2,
    ) -> None:
        logger.debug("Entered into AutonomousExecutor.__init__")
        self._client = client
        self._planner_model = planner_model
        self._max_replans = max_replans
        self._max_retries = max_retries_per_task

    async def run(
        self,
        task: str,
        execute_fn: Any = None,
        resume_state: AutonomousState | None = None,
    ) -> AsyncIterator[AutonomousEvent]:
        logger.debug(f"Entered into AutonomousExecutor.run: task={task[:80]!r}")

        if resume_state:
            state = resume_state
            yield AutonomousEvent(kind="progress", data={
                "message": f"Resuming from subtask {state.current_index + 1}/{len(state.subtasks)}",
            })
        else:
            state = AutonomousState(original_task=task, started_at=time.monotonic())
            subtasks = await self._decompose(task)
            state.subtasks = subtasks
            yield AutonomousEvent(kind="plan", data={
                "subtask_count": len(subtasks),
                "subtasks": [{"id": s.id, "task": s.task, "verify": s.verify} for s in subtasks],
            })

        while state.current_index < len(state.subtasks):
            subtask = state.subtasks[state.current_index]

            if subtask.status in (SubtaskStatus.COMPLETED, SubtaskStatus.SKIPPED):
                state.current_index += 1
                continue

            subtask.status = SubtaskStatus.RUNNING
            subtask.attempt += 1
            yield AutonomousEvent(kind="subtask_start", data={
                "id": subtask.id,
                "task": subtask.task,
                "attempt": subtask.attempt,
                "progress": f"{state.current_index + 1}/{len(state.subtasks)}",
            })

            if execute_fn:
                try:
                    result = await execute_fn(subtask.task)
                    subtask.result = str(result)
                    subtask.status = SubtaskStatus.COMPLETED
                    yield AutonomousEvent(kind="subtask_result", data={
                        "id": subtask.id,
                        "status": "completed",
                        "result_preview": subtask.result[:500],
                    })
                except Exception as e:
                    subtask.error = str(e)
                    subtask.status = SubtaskStatus.FAILED
                    yield AutonomousEvent(kind="subtask_result", data={
                        "id": subtask.id,
                        "status": "failed",
                        "error": subtask.error[:500],
                    })

                    if subtask.attempt < self._max_retries:
                        subtask.status = SubtaskStatus.PENDING
                        continue

                    if state.replans < self._max_replans:
                        replan_result = await self._replan(state, subtask)
                        state.replans += 1
                        yield AutonomousEvent(kind="replan", data=replan_result)

                        if replan_result.get("action") == "retry":
                            subtask.status = SubtaskStatus.PENDING
                            subtask.attempt = 0
                            continue
                        elif replan_result.get("action") == "skip":
                            subtask.status = SubtaskStatus.SKIPPED
                        elif replan_result.get("action") == "replan":
                            new_tasks = replan_result.get("new_tasks", [])
                            if new_tasks:
                                max_id = max(s.id for s in state.subtasks)
                                for i, nt in enumerate(new_tasks):
                                    state.subtasks.insert(
                                        state.current_index + 1 + i,
                                        Subtask(
                                            id=max_id + 1 + i,
                                            task=nt.get("task", ""),
                                            verify=nt.get("verify", ""),
                                        ),
                                    )
                            subtask.status = SubtaskStatus.SKIPPED
                    else:
                        subtask.status = SubtaskStatus.FAILED
                        yield AutonomousEvent(kind="error", data={
                            "message": f"Subtask {subtask.id} failed after {self._max_replans} replans",
                        })
            else:
                subtask.result = f"(no executor provided — subtask logged: {subtask.task})"
                subtask.status = SubtaskStatus.COMPLETED
                yield AutonomousEvent(kind="subtask_result", data={
                    "id": subtask.id,
                    "status": "completed",
                    "result_preview": subtask.result,
                })

            state.current_index += 1

        completed = [s for s in state.subtasks if s.status == SubtaskStatus.COMPLETED]
        failed = [s for s in state.subtasks if s.status == SubtaskStatus.FAILED]
        skipped = [s for s in state.subtasks if s.status == SubtaskStatus.SKIPPED]

        yield AutonomousEvent(kind="done", data={
            "total_subtasks": len(state.subtasks),
            "completed": len(completed),
            "failed": len(failed),
            "skipped": len(skipped),
            "replans": state.replans,
            "elapsed_s": round(time.monotonic() - state.started_at, 1),
        })

    async def _decompose(self, task: str) -> list[Subtask]:
        logger.debug(f"Entered into _decompose: task={task[:80]!r}")
        prompt = DECOMPOSE_PROMPT.replace("{task}", task[:4000])
        try:
            response = await self._client.chat_completion(
                messages=[ChatMessage(role="user", content=prompt)],
                model=self._planner_model,
                temperature=0.2,
                max_tokens=2000,
            )
            parsed = _extract_json_array(response.content)
            if parsed:
                return [
                    Subtask(
                        id=item.get("id", i + 1),
                        task=item.get("task", ""),
                        verify=item.get("verify", ""),
                    )
                    for i, item in enumerate(parsed)
                    if item.get("task")
                ]
        except Exception as e:
            logger.warning(f"Task decomposition failed: {e}")

        return [Subtask(id=1, task=task, verify="Task output is complete and correct")]

    async def _replan(self, state: AutonomousState, failed: Subtask) -> dict[str, Any]:
        logger.debug(f"Entered into _replan: failed_id={failed.id}")
        completed_text = "\n".join(
            f"  [{s.id}] {s.task} -> {s.result[:200]}"
            for s in state.subtasks
            if s.status == SubtaskStatus.COMPLETED
        ) or "  (none)"

        remaining_text = "\n".join(
            f"  [{s.id}] {s.task}"
            for s in state.subtasks[state.current_index + 1:]
            if s.status == SubtaskStatus.PENDING
        ) or "  (none)"

        prompt = REPLAN_PROMPT.replace("{original_task}", state.original_task[:2000])
        prompt = prompt.replace("{completed}", completed_text)
        prompt = prompt.replace("{failed_task}", failed.task)
        prompt = prompt.replace("{error}", failed.error[:500])
        prompt = prompt.replace("{remaining}", remaining_text)

        try:
            response = await self._client.chat_completion(
                messages=[ChatMessage(role="user", content=prompt)],
                model=self._planner_model,
                temperature=0.2,
                max_tokens=1000,
            )
            s = response.content.strip()
            a, b = s.find("{"), s.rfind("}")
            if a != -1 and b != -1:
                return json.loads(s[a : b + 1])
        except Exception as e:
            logger.warning(f"Replan failed: {e}")

        return {"action": "skip", "reason": "replanner unavailable"}

    def get_checkpoint(self, state: AutonomousState) -> dict[str, Any]:
        logger.debug("Entered into get_checkpoint")
        return {
            "original_task": state.original_task,
            "subtasks": [
                {
                    "id": s.id,
                    "task": s.task,
                    "verify": s.verify,
                    "status": s.status.value,
                    "result": s.result[:1000],
                    "error": s.error[:500],
                    "attempt": s.attempt,
                }
                for s in state.subtasks
            ],
            "current_index": state.current_index,
            "replans": state.replans,
        }


def _extract_json_array(content: str) -> list[dict[str, Any]] | None:
    logger.debug("Entered into _extract_json_array")
    s = content.strip()
    if "```" in s:
        parts = s.split("```")
        s = parts[1] if len(parts) >= 3 else s.replace("```", "")
        if s.startswith("json"):
            s = s[4:]
    a, b = s.find("["), s.rfind("]")
    if a != -1 and b != -1 and b > a:
        try:
            parsed = json.loads(s[a : b + 1])
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            pass
    return None
