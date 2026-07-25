"""Agent swarm — supervisor spawns parallel sub-agents for complex tasks.

The supervisor decomposes work, assigns sub-agents (each with its own
agent loop context), runs them concurrently, and aggregates results.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from elidia.api.client import AiUtilsClient, ChatMessage

logger = logging.getLogger(__name__)

SUPERVISOR_PROMPT = (
    "You are a supervisor coordinating multiple AI agents. You have been given "
    "a complex task that benefits from parallel execution.\n\n"
    "Break the task into {max_agents} or fewer independent sub-tasks that can "
    "run in parallel. Each sub-task should be self-contained — an agent working "
    "on sub-task 2 should not need results from sub-task 1.\n\n"
    "Return ONLY a JSON array of objects:\n"
    '[{{"id": 1, "task": "<description>", "role": "<agent role: coder|researcher|analyst|writer>"}}, ...]\n\n'
    "Task: {task}\n\nJSON:"
)

AGGREGATE_PROMPT = (
    "You are a supervisor aggregating results from {count} parallel agents.\n\n"
    "Original task: {task}\n\n"
    "{results}\n\n"
    "Combine these results into a single, coherent response. Resolve any "
    "conflicts or overlaps. Produce a complete answer to the original task."
)


@dataclass
class SubAgent:
    id: int
    task: str
    role: str
    status: str = "pending"  # pending, running, completed, failed
    result: str = ""
    error: str = ""
    tokens_used: int = 0
    elapsed_ms: int = 0


@dataclass
class SwarmEvent:
    kind: str  # "plan", "agent_start", "agent_done", "aggregate", "error", "done"
    data: Any = None


@dataclass
class SwarmResult:
    aggregated: str
    agents: list[SubAgent]
    total_tokens: int = 0
    elapsed_ms: int = 0


class AgentSwarm:
    """Supervisor that spawns and coordinates parallel sub-agents."""

    def __init__(
        self,
        client: AiUtilsClient,
        execute_fn: Callable[[str, str], Coroutine[Any, Any, str]] | None = None,
        supervisor_model: str = "deepseek-chat",
        agent_model: str = "deepseek-chat",
        max_agents: int = 5,
    ) -> None:
        logger.debug(f"Entered into AgentSwarm.__init__: max_agents={max_agents}")
        self._client = client
        self._execute_fn = execute_fn
        self._supervisor_model = supervisor_model
        self._agent_model = agent_model
        self._max_agents = max_agents

    async def run(self, task: str) -> AsyncIterator[SwarmEvent]:
        logger.debug(f"Entered into AgentSwarm.run: task={task[:80]!r}")
        start = time.monotonic()

        agents = await self._plan(task)
        yield SwarmEvent(kind="plan", data={
            "agent_count": len(agents),
            "agents": [{"id": a.id, "task": a.task, "role": a.role} for a in agents],
        })

        if not agents:
            yield SwarmEvent(kind="error", data="Failed to decompose task into sub-agents")
            return

        for agent in agents:
            agent.status = "running"
            yield SwarmEvent(kind="agent_start", data={"id": agent.id, "task": agent.task, "role": agent.role})

        tasks_coros = [self._run_agent(agent) for agent in agents]
        results = await asyncio.gather(*tasks_coros, return_exceptions=True)

        for i, result in enumerate(results):
            if isinstance(result, Exception):
                agents[i].status = "failed"
                agents[i].error = str(result)
            yield SwarmEvent(kind="agent_done", data={
                "id": agents[i].id,
                "status": agents[i].status,
                "result_preview": agents[i].result[:300] if agents[i].result else agents[i].error[:300],
            })

        completed = [a for a in agents if a.status == "completed"]
        if not completed:
            yield SwarmEvent(kind="error", data="All sub-agents failed")
            yield SwarmEvent(kind="done", data={
                "completed": 0, "failed": len(agents),
                "elapsed_ms": int((time.monotonic() - start) * 1000),
            })
            return

        aggregated = await self._aggregate(task, completed)
        yield SwarmEvent(kind="aggregate", data={"content": aggregated})

        total_tokens = sum(a.tokens_used for a in agents)
        elapsed = int((time.monotonic() - start) * 1000)

        yield SwarmEvent(kind="done", data={
            "completed": len(completed),
            "failed": len(agents) - len(completed),
            "total_tokens": total_tokens,
            "elapsed_ms": elapsed,
        })

    async def _plan(self, task: str) -> list[SubAgent]:
        logger.debug(f"Entered into _plan: task={task[:80]!r}")
        prompt = SUPERVISOR_PROMPT.replace("{max_agents}", str(self._max_agents))
        prompt = prompt.replace("{task}", task[:4000])

        try:
            response = await self._client.chat_completion(
                messages=[ChatMessage(role="user", content=prompt)],
                model=self._supervisor_model,
                temperature=0.2,
                max_tokens=1500,
            )
            parsed = _extract_json_array(response.content)
            if parsed:
                return [
                    SubAgent(
                        id=item.get("id", i + 1),
                        task=item.get("task", ""),
                        role=item.get("role", "analyst"),
                    )
                    for i, item in enumerate(parsed[:self._max_agents])
                    if item.get("task")
                ]
        except Exception as e:
            logger.warning(f"Swarm planning failed: {e}")

        return [SubAgent(id=1, task=task, role="analyst")]

    async def _run_agent(self, agent: SubAgent) -> None:
        logger.debug(f"Entered into _run_agent: id={agent.id}, role={agent.role}")
        start = time.monotonic()

        try:
            if self._execute_fn:
                result = await self._execute_fn(agent.task, agent.role)
                agent.result = str(result)
            else:
                response = await self._client.chat_completion(
                    messages=[ChatMessage(role="user", content=agent.task)],
                    model=self._agent_model,
                    temperature=0.5,
                )
                agent.result = response.content
                agent.tokens_used = response.tokens_in + response.tokens_out

            agent.status = "completed"
        except Exception as e:
            agent.status = "failed"
            agent.error = str(e)
            raise

        agent.elapsed_ms = int((time.monotonic() - start) * 1000)

    async def _aggregate(self, original_task: str, completed: list[SubAgent]) -> str:
        logger.debug(f"Entered into _aggregate: count={len(completed)}")
        results_text = "\n\n".join(
            f"--- Agent {a.id} ({a.role}) ---\nTask: {a.task}\nResult:\n{a.result[:3000]}"
            for a in completed
        )

        prompt = AGGREGATE_PROMPT.replace("{count}", str(len(completed)))
        prompt = prompt.replace("{task}", original_task[:2000])
        prompt = prompt.replace("{results}", results_text[:12000])

        try:
            response = await self._client.chat_completion(
                messages=[ChatMessage(role="user", content=prompt)],
                model=self._supervisor_model,
                temperature=0.3,
            )
            return response.content
        except Exception as e:
            logger.warning(f"Aggregation failed: {e}")
            return "\n\n".join(a.result for a in completed)


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
