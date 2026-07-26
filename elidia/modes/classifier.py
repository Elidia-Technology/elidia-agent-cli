"""LLM-based mode classifier — zero keyword/regex matching.

Analyzes the user's message and selects the best execution mode:
  DIRECT    — single LLM answer (greetings, facts, generation requests)
  CONSENSUS — 2-3 models in parallel, compare, synthesize
  HARNESS   — multi-step pipeline: plan → research → draft → synthesize
  DEEP      — web search + RAG + synthesize with citations
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any

from elidia.api.client import AiUtilsClient, ChatMessage

logger = logging.getLogger(__name__)


class ExecMode(str, Enum):
    DIRECT = "direct"
    CONSENSUS = "consensus"
    HARNESS = "harness"
    DEEP = "deep"


@dataclass
class ModeDecision:
    mode: ExecMode
    reason: str
    cost_label: str
    tool_slug: str = ""


MODE_CLASSIFIER_PROMPT = (
    "You are a request complexity classifier. Analyze the user's message and "
    "classify it into exactly ONE of these execution modes:\n\n"
    "DIRECT — a simple question, greeting, or request that a single LLM can "
    "answer directly. Also use DIRECT for any request to generate, create, make, "
    "or produce images, videos, audio, music, voiceovers, or any visual/audio "
    "content. Examples: 'hello', 'what is 2+2', 'write a poem', 'generate an image'.\n\n"
    "CONSENSUS — a question where getting the RIGHT answer matters and different "
    "models might disagree. Best for comparisons, recommendations, risk assessment, "
    "purchasing decisions. Examples: 'React vs Vue?', 'best database for my use case'.\n\n"
    "HARNESS — a multi-step task that needs research THEN analysis THEN output. "
    "User explicitly or implicitly wants a structured deliverable. Examples: "
    "'research AI trends and write a report', 'draft a project plan with timeline'.\n\n"
    "DEEP — a research question that needs web search AND document search to answer "
    "properly, OR any legal/compliance/regulatory question, OR any request for current "
    "stock prices, exchange rates, or market data. Examples: 'latest quantum computing "
    "breakthroughs', 'GDPR compliance for healthcare', 'current Bitcoin price'.\n\n"
    "Return ONLY a JSON object:\n"
    '{"mode": "direct|consensus|harness|deep", "reason": "<one short sentence>"}\n'
    "No prose, no markdown, no explanation.\n\n"
    'User message: "{message}"\n\nJSON:'
)

FRESHNESS_PROMPT = (
    "You are a query freshness detector. Analyze if the user's message requires "
    "CURRENT, up-to-date information from live news or the internet.\n\n"
    "Return ONLY a JSON object:\n"
    '{"is_news": true/false, "freshness_needed": true/false, '
    '"reason": "<one short sentence>"}\n\n'
    "- is_news: true for 'latest news', 'current events', 'what happened today'.\n"
    "- freshness_needed: true when answering requires data the LLM may not have "
    "(stock prices, weather, recent events, sports scores).\n"
    "- For general knowledge, coding, creative writing: both false.\n\n"
    'User message: "{message}"\n\nJSON:'
)

CLASSIFIER_MODEL = "deepseek-v4-flash"


@dataclass
class FreshnessDecision:
    is_news: bool
    freshness_needed: bool
    reason: str


async def detect_freshness(
    client: AiUtilsClient,
    message: str,
    classifier_model: str = CLASSIFIER_MODEL,
) -> FreshnessDecision:
    logger.debug(f"Entered into detect_freshness: msg={message[:80]!r}")
    prompt = FRESHNESS_PROMPT.replace("{message}", message[:2000])
    try:
        response = await client.chat_completion(
            messages=[ChatMessage(role="user", content=prompt)],
            model=classifier_model,
            temperature=0.0,
            max_tokens=80,
        )
        parsed = _extract_json(response.content)
        if parsed:
            return FreshnessDecision(
                is_news=bool(parsed.get("is_news", False)),
                freshness_needed=bool(parsed.get("freshness_needed", False)),
                reason=str(parsed.get("reason", ""))[:200],
            )
    except Exception as e:
        logger.warning(f"Freshness detector failed: {e}")
    return FreshnessDecision(is_news=False, freshness_needed=False, reason="detector unavailable")


async def classify_mode(
    client: AiUtilsClient,
    message: str,
    classifier_model: str = CLASSIFIER_MODEL,
) -> ModeDecision:
    logger.debug(f"Entered into classify_mode: msg={message[:60]!r}")
    prompt = MODE_CLASSIFIER_PROMPT.replace("{message}", message[:2000])
    try:
        response = await client.chat_completion(
            messages=[ChatMessage(role="user", content=prompt)],
            model=classifier_model,
            temperature=0.0,
            max_tokens=100,
        )
        parsed = _extract_json(response.content)
        if parsed:
            mode_str = (parsed.get("mode") or "direct").lower()
            try:
                mode = ExecMode(mode_str)
            except ValueError:
                mode = ExecMode.DIRECT
            cost_labels = {
                ExecMode.DIRECT: "cheapest",
                ExecMode.CONSENSUS: "consensus",
                ExecMode.HARNESS: "multi-step",
                ExecMode.DEEP: "research",
            }
            reason = str(parsed.get("reason", ""))[:200]
            tool_slug = str(parsed.get("tool_slug", "") or "")
            if tool_slug == "null":
                tool_slug = ""
            logger.info(f"Mode classified: {mode.value} — {reason}")
            return ModeDecision(
                mode=mode,
                reason=reason,
                cost_label=cost_labels.get(mode, "cheapest"),
                tool_slug=tool_slug,
            )
    except Exception as e:
        logger.warning(f"Mode classifier failed: {e}")

    return ModeDecision(
        mode=ExecMode.DIRECT,
        reason="classifier unavailable; defaulting to direct",
        cost_label="cheapest",
    )


def _extract_json(content: str) -> dict[str, Any] | None:
    logger.debug("Entered into _extract_json")
    s = content.strip()
    if "```" in s:
        parts = s.split("```")
        s = parts[1] if len(parts) >= 3 else s.replace("```", "")
    a, b = s.find("{"), s.rfind("}")
    if a != -1 and b != -1 and b > a:
        try:
            return json.loads(s[a : b + 1])
        except json.JSONDecodeError:
            pass
    return None
