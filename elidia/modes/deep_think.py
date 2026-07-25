"""Deep think mode — routes to reasoning models and displays chain-of-thought.

Selects reasoning-capable models (deepseek-reasoner, o1, etc.) and renders
the thinking/reasoning tokens to the terminal so the user can follow the
model's step-by-step reasoning process.
"""
from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass

from elidia.api.client import AiUtilsClient, ChatMessage

logger = logging.getLogger(__name__)

REASONING_MODELS = [
    "deepseek-reasoner",
    "o1",
    "o1-mini",
    "gemini-2.5-pro",
    "qwen-3-235b",
    "claude-opus-4-8",
]

DEFAULT_REASONING_MODEL = "deepseek-reasoner"


@dataclass
class DeepThinkResult:
    reasoning: str
    answer: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    reasoning_tokens: int = 0
    elapsed_ms: int = 0


@dataclass
class ThinkEvent:
    kind: str  # "reasoning", "content", "usage", "error", "done"
    data: str | dict = ""


async def deep_think_stream(
    client: AiUtilsClient,
    messages: list[ChatMessage],
    model: str = DEFAULT_REASONING_MODEL,
    max_tokens: int | None = None,
) -> AsyncIterator[ThinkEvent]:
    logger.debug(f"Entered into deep_think_stream: model={model}, msgs={len(messages)}")

    system_msg = ChatMessage(
        role="system",
        content=(
            "Think step by step. Show your complete reasoning process. "
            "Break complex problems into smaller parts and work through each one. "
            "After your reasoning, provide a clear final answer."
        ),
    )
    full_messages = [system_msg] + messages

    start = time.monotonic()
    reasoning_text = ""
    answer_text = ""
    in_reasoning = True
    tokens_in = 0
    tokens_out = 0

    async for event in client.chat_completion_stream(
        messages=full_messages,
        model=model,
        temperature=0.0,
        max_tokens=max_tokens,
    ):
        if event.event_type == "content":
            chunk = event.data
            if in_reasoning and _is_reasoning_boundary(answer_text + chunk):
                in_reasoning = False
                boundary_idx = _find_boundary_index(answer_text + chunk)
                if boundary_idx >= 0:
                    remaining_reasoning = (answer_text + chunk)[:boundary_idx]
                    answer_start = (answer_text + chunk)[boundary_idx:]
                    if remaining_reasoning:
                        reasoning_text += remaining_reasoning
                        yield ThinkEvent(kind="reasoning", data=remaining_reasoning)
                    answer_text = answer_start
                    if answer_start:
                        yield ThinkEvent(kind="content", data=answer_start)
                    continue

            if in_reasoning:
                reasoning_text += chunk
                yield ThinkEvent(kind="reasoning", data=chunk)
            else:
                answer_text += chunk
                yield ThinkEvent(kind="content", data=chunk)

        elif event.event_type == "usage" and isinstance(event.data, dict):
            tokens_in = event.data.get("prompt_tokens", 0)
            tokens_out = event.data.get("completion_tokens", 0)
            yield ThinkEvent(kind="usage", data=event.data)

        elif event.event_type == "error":
            msg = event.data.get("message", str(event.data)) if isinstance(event.data, dict) else str(event.data)
            yield ThinkEvent(kind="error", data=msg)
            return

    elapsed_ms = int((time.monotonic() - start) * 1000)

    if in_reasoning and reasoning_text and not answer_text:
        answer_text = reasoning_text
        reasoning_text = ""

    yield ThinkEvent(kind="done", data={
        "reasoning_length": len(reasoning_text),
        "answer_length": len(answer_text),
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "elapsed_ms": elapsed_ms,
    })


async def deep_think(
    client: AiUtilsClient,
    messages: list[ChatMessage],
    model: str = DEFAULT_REASONING_MODEL,
    max_tokens: int | None = None,
) -> DeepThinkResult:
    logger.debug(f"Entered into deep_think: model={model}")
    result = DeepThinkResult(reasoning="", answer="", model=model)
    start = time.monotonic()

    async for event in deep_think_stream(client, messages, model, max_tokens):
        if event.kind == "reasoning":
            result.reasoning += event.data
        elif event.kind == "content":
            result.answer += event.data
        elif event.kind == "usage" and isinstance(event.data, dict):
            result.tokens_in = event.data.get("prompt_tokens", 0)
            result.tokens_out = event.data.get("completion_tokens", 0)
        elif event.kind == "error":
            raise RuntimeError(str(event.data))

    result.elapsed_ms = int((time.monotonic() - start) * 1000)
    result.reasoning_tokens = len(result.reasoning.split())
    return result


def select_reasoning_model(
    preferred: str | None = None,
    available_models: list[str] | None = None,
) -> str:
    logger.debug(f"Entered into select_reasoning_model: preferred={preferred}")
    if preferred and preferred in REASONING_MODELS:
        return preferred
    if available_models:
        for m in REASONING_MODELS:
            if m in available_models:
                return m
    return DEFAULT_REASONING_MODEL


REASONING_BOUNDARIES = [
    "\n## Answer",
    "\n## Final Answer",
    "\n**Answer:**",
    "\n**Final Answer:**",
    "\nTherefore,",
    "\nIn conclusion,",
    "\nTo summarize,",
    "\n---\n",
]


def _is_reasoning_boundary(text: str) -> bool:
    logger.debug("Entered into _is_reasoning_boundary")
    return any(marker in text for marker in REASONING_BOUNDARIES)


def _find_boundary_index(text: str) -> int:
    logger.debug("Entered into _find_boundary_index")
    earliest = -1
    for marker in REASONING_BOUNDARIES:
        idx = text.find(marker)
        if idx >= 0 and (earliest < 0 or idx < earliest):
            earliest = idx
    return earliest
