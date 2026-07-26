"""Workflow engine — executes YAML-defined pipelines with conditions, loops, and parallelism.

A workflow is a sequence of steps. Each step can be:
  - llm: send a prompt to an LLM and capture the response
  - tool: execute a built-in or MCP tool
  - shell: run a shell command
  - parallel: execute sub-steps concurrently
  - loop: iterate over a list and execute body per item

Steps can have conditions (Jinja2-style expressions) and capture outputs
into context variables for use in later steps.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from elidia.api.client import AiUtilsClient, ChatMessage

logger = logging.getLogger(__name__)


@dataclass
class WorkflowStep:
    name: str
    type: str  # "llm", "tool", "shell", "parallel", "loop"
    config: dict[str, Any] = field(default_factory=dict)
    condition: str = ""
    output_var: str = ""
    steps: list[WorkflowStep] = field(default_factory=list)


@dataclass
class Workflow:
    name: str
    description: str = ""
    steps: list[WorkflowStep] = field(default_factory=list)
    variables: dict[str, Any] = field(default_factory=dict)


@dataclass
class StepResult:
    name: str
    status: str  # "completed", "skipped", "failed"
    output: str = ""
    error: str = ""
    elapsed_ms: int = 0


@dataclass
class WorkflowEvent:
    kind: str  # "start", "step_start", "step_done", "progress", "error", "done"
    data: Any = None


def parse_workflow(source: str | Path) -> Workflow:
    logger.debug("Entered into parse_workflow")
    if isinstance(source, Path) or (isinstance(source, str) and source.endswith((".yml", ".yaml"))):
        path = Path(source)
        if not path.exists():
            raise FileNotFoundError(f"Workflow file not found: {path}")
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    else:
        raw = yaml.safe_load(source)

    if not isinstance(raw, dict):
        raise ValueError("Workflow must be a YAML mapping")

    name = raw.get("name", "unnamed")
    description = raw.get("description", "")
    variables = raw.get("variables", {})

    steps = _parse_steps(raw.get("steps", []))

    return Workflow(
        name=name,
        description=description,
        steps=steps,
        variables=variables,
    )


def workflow_requires_llm(workflow: Workflow) -> bool:
    """True if any step (recursively, including inside parallel/loop) is an 'llm' step."""
    logger.debug(f"Entered into workflow_requires_llm: name={workflow.name}")

    def _any_llm(steps: list[WorkflowStep]) -> bool:
        for step in steps:
            if step.type == "llm":
                return True
            if step.steps and _any_llm(step.steps):
                return True
        return False

    return _any_llm(workflow.steps)


def _parse_steps(raw_steps: list[dict[str, Any]]) -> list[WorkflowStep]:
    logger.debug(f"Entered into _parse_steps: count={len(raw_steps)}")
    steps: list[WorkflowStep] = []
    for raw in raw_steps:
        if not isinstance(raw, dict):
            continue

        step_type = raw.get("type", "llm")
        sub_steps: list[WorkflowStep] = []
        if step_type in ("parallel", "loop") and "steps" in raw:
            sub_steps = _parse_steps(raw["steps"])

        config = {k: v for k, v in raw.items() if k not in ("name", "type", "condition", "output", "steps")}

        steps.append(WorkflowStep(
            name=raw.get("name", f"step_{len(steps) + 1}"),
            type=step_type,
            config=config,
            condition=raw.get("condition", ""),
            output_var=raw.get("output", ""),
            steps=sub_steps,
        ))
    return steps


class WorkflowExecutor:
    """Executes a parsed Workflow step by step."""

    def __init__(
        self,
        client: AiUtilsClient | None = None,
        tool_execute_fn: Any = None,
        model: str = "deepseek-v4-flash",
    ) -> None:
        logger.debug("Entered into WorkflowExecutor.__init__")
        self._client = client
        self._tool_fn = tool_execute_fn
        self._model = model

    async def run(self, workflow: Workflow) -> AsyncIterator[WorkflowEvent]:
        logger.debug(f"Entered into WorkflowExecutor.run: name={workflow.name}")
        start = time.monotonic()
        context: dict[str, Any] = dict(workflow.variables)
        results: list[StepResult] = []

        yield WorkflowEvent(kind="start", data={
            "name": workflow.name,
            "description": workflow.description,
            "step_count": len(workflow.steps),
        })

        for step in workflow.steps:
            async for event in self._execute_step(step, context, results):
                yield event

        elapsed = int((time.monotonic() - start) * 1000)
        completed = sum(1 for r in results if r.status == "completed")
        failed = sum(1 for r in results if r.status == "failed")
        skipped = sum(1 for r in results if r.status == "skipped")

        yield WorkflowEvent(kind="done", data={
            "name": workflow.name,
            "total_steps": len(results),
            "completed": completed,
            "failed": failed,
            "skipped": skipped,
            "elapsed_ms": elapsed,
            "context_keys": list(context.keys()),
        })

    async def _execute_step(
        self,
        step: WorkflowStep,
        context: dict[str, Any],
        results: list[StepResult],
    ) -> AsyncIterator[WorkflowEvent]:
        logger.debug(f"Entered into _execute_step: name={step.name}, type={step.type}")

        if step.condition:
            if not _evaluate_condition(step.condition, context):
                result = StepResult(name=step.name, status="skipped")
                results.append(result)
                yield WorkflowEvent(kind="step_done", data={
                    "name": step.name, "status": "skipped", "reason": f"condition false: {step.condition}",
                })
                return

        yield WorkflowEvent(kind="step_start", data={
            "name": step.name, "type": step.type,
        })

        start = time.monotonic()
        output = ""
        error = ""
        status = "completed"

        try:
            if step.type == "llm":
                output = await self._execute_llm(step, context)
            elif step.type == "tool":
                output = await self._execute_tool(step, context)
            elif step.type == "shell":
                output = await self._execute_shell(step, context)
            elif step.type == "parallel":
                output = await self._execute_parallel(step, context, results)
            elif step.type == "loop":
                output = await self._execute_loop(step, context, results)
            else:
                error = f"Unknown step type: {step.type}"
                status = "failed"
        except Exception as e:
            error = str(e)
            status = "failed"
            logger.warning(f"Step {step.name} failed: {e}")

        elapsed = int((time.monotonic() - start) * 1000)

        if step.output_var and output:
            context[step.output_var] = output

        result = StepResult(name=step.name, status=status, output=output, error=error, elapsed_ms=elapsed)
        results.append(result)

        yield WorkflowEvent(kind="step_done", data={
            "name": step.name,
            "status": status,
            "output_preview": output[:300] if output else error[:300],
            "elapsed_ms": elapsed,
        })

    async def _execute_llm(self, step: WorkflowStep, context: dict[str, Any]) -> str:
        logger.debug(f"Entered into _execute_llm: step={step.name}")
        if not self._client:
            raise RuntimeError("No LLM client available")

        prompt = _render_template(step.config.get("prompt", ""), context)
        model = step.config.get("model", self._model)
        temperature = step.config.get("temperature", 0.7)
        max_tokens = step.config.get("max_tokens")

        response = await self._client.chat_completion(
            messages=[ChatMessage(role="user", content=prompt)],
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return response.content

    async def _execute_tool(self, step: WorkflowStep, context: dict[str, Any]) -> str:
        logger.debug(f"Entered into _execute_tool: step={step.name}")
        if not self._tool_fn:
            raise RuntimeError("No tool executor available")

        tool_name = step.config.get("tool", "")
        arguments = {k: _render_template(str(v), context) for k, v in step.config.get("arguments", {}).items()}

        result = await self._tool_fn(tool_name, arguments)
        if hasattr(result, "content"):
            return result.content
        return str(result)

    async def _execute_shell(self, step: WorkflowStep, context: dict[str, Any]) -> str:
        logger.debug(f"Entered into _execute_shell: step={step.name}")
        command = _render_template(step.config.get("command", ""), context)
        timeout = step.config.get("timeout", 30)
        cwd = step.config.get("cwd")

        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError as err:
            proc.kill()
            raise RuntimeError(f"Command timed out after {timeout}s: {command}") from err

        output = stdout.decode("utf-8", errors="replace")
        if proc.returncode != 0:
            err = stderr.decode("utf-8", errors="replace")
            raise RuntimeError(f"Command failed (exit {proc.returncode}): {err}")

        return output

    async def _execute_parallel(
        self, step: WorkflowStep, context: dict[str, Any], results: list[StepResult],
    ) -> str:
        logger.debug(f"Entered into _execute_parallel: sub_steps={len(step.steps)}")
        outputs: list[str] = []

        async def run_sub(sub: WorkflowStep) -> str:
            sub_output = ""
            sub_context = dict(context)
            async for _ in self._execute_step(sub, sub_context, results):
                pass
            if sub.output_var and sub.output_var in sub_context:
                sub_output = str(sub_context[sub.output_var])
                context[sub.output_var] = sub_output
            return sub_output

        coros = [run_sub(sub) for sub in step.steps]
        sub_results = await asyncio.gather(*coros, return_exceptions=True)

        for r in sub_results:
            if isinstance(r, Exception):
                outputs.append(f"(error: {r})")
            else:
                outputs.append(str(r))

        return "\n---\n".join(outputs)

    async def _execute_loop(
        self, step: WorkflowStep, context: dict[str, Any], results: list[StepResult],
    ) -> str:
        logger.debug(f"Entered into _execute_loop: sub_steps={len(step.steps)}")
        items_key = step.config.get("items", "")
        item_var = step.config.get("item_var", "item")

        items = context.get(items_key, [])
        if isinstance(items, str):
            items = items.split(",")

        outputs: list[str] = []
        for idx, item in enumerate(items):
            loop_context = dict(context)
            loop_context[item_var] = item
            loop_context["loop_index"] = idx

            for sub in step.steps:
                async for _ in self._execute_step(sub, loop_context, results):
                    pass
                if sub.output_var and sub.output_var in loop_context:
                    outputs.append(str(loop_context[sub.output_var]))
                    context.setdefault(f"{sub.output_var}_list", []).append(loop_context[sub.output_var])

        return "\n".join(outputs)


def _render_template(template: str, context: dict[str, Any]) -> str:
    logger.debug(f"Entered into _render_template: len={len(template)}")
    result = template
    for key, value in context.items():
        result = result.replace(f"{{{{{key}}}}}", str(value))
        result = result.replace(f"${{{key}}}", str(value))
    return result


def _evaluate_condition(condition: str, context: dict[str, Any]) -> bool:
    logger.debug(f"Entered into _evaluate_condition: {condition!r}")
    cond = condition.strip()

    for key, value in context.items():
        cond = cond.replace(f"{{{{{key}}}}}", repr(value))
        cond = cond.replace(f"${{{key}}}", repr(value))

    cond = cond.replace("{{", "").replace("}}", "")

    if " == " in cond:
        parts = cond.split(" == ", 1)
        left = parts[0].strip().strip("'\"")
        right = parts[1].strip().strip("'\"")
        left_val = str(context.get(left, left))
        right_val = str(context.get(right, right))
        return left_val == right_val

    if " != " in cond:
        parts = cond.split(" != ", 1)
        left = parts[0].strip().strip("'\"")
        right = parts[1].strip().strip("'\"")
        left_val = str(context.get(left, left))
        right_val = str(context.get(right, right))
        return left_val != right_val

    if " in " in cond:
        parts = cond.split(" in ", 1)
        needle_key = parts[0].strip().strip("'\"")
        haystack_key = parts[1].strip()
        needle = str(context.get(needle_key, needle_key))
        haystack = context.get(haystack_key, "")
        return needle in str(haystack)

    var_name = cond.strip()
    if var_name not in context:
        return False
    val = context[var_name]
    return bool(val) and val != "false" and val != "0" and val != ""
