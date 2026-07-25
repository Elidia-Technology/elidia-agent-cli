"""Thinking levels — controls depth of agent execution.

5 levels from minimal (cheapest, fastest) to max (unlimited resources):
  minimal — quick answer, no tools, capped output
  low     — basic tool use, limited loops
  medium  — standard operation (default)
  high    — deep analysis, multiple agent loops, extended wall clock
  max     — unlimited, full swarm capability
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import IntEnum

logger = logging.getLogger(__name__)


class ThinkingLevel(IntEnum):
    MINIMAL = 1
    LOW = 2
    MEDIUM = 3
    HIGH = 4
    MAX = 5


@dataclass(frozen=True)
class ThinkingCaps:
    level: ThinkingLevel
    max_dt: float
    max_wall_seconds: float
    max_loops: int
    max_agents: int
    max_output_tokens: int
    allow_tools: bool
    allow_web_search: bool
    allow_consensus: bool
    allow_swarm: bool


LEVEL_CAPS: dict[ThinkingLevel, ThinkingCaps] = {
    ThinkingLevel.MINIMAL: ThinkingCaps(
        level=ThinkingLevel.MINIMAL,
        max_dt=5.0,
        max_wall_seconds=120.0,
        max_loops=3,
        max_agents=1,
        max_output_tokens=1024,
        allow_tools=False,
        allow_web_search=False,
        allow_consensus=False,
        allow_swarm=False,
    ),
    ThinkingLevel.LOW: ThinkingCaps(
        level=ThinkingLevel.LOW,
        max_dt=25.0,
        max_wall_seconds=300.0,
        max_loops=10,
        max_agents=2,
        max_output_tokens=4096,
        allow_tools=True,
        allow_web_search=False,
        allow_consensus=False,
        allow_swarm=False,
    ),
    ThinkingLevel.MEDIUM: ThinkingCaps(
        level=ThinkingLevel.MEDIUM,
        max_dt=75.0,
        max_wall_seconds=900.0,
        max_loops=25,
        max_agents=4,
        max_output_tokens=8192,
        allow_tools=True,
        allow_web_search=True,
        allow_consensus=True,
        allow_swarm=False,
    ),
    ThinkingLevel.HIGH: ThinkingCaps(
        level=ThinkingLevel.HIGH,
        max_dt=200.0,
        max_wall_seconds=3600.0,
        max_loops=50,
        max_agents=8,
        max_output_tokens=16384,
        allow_tools=True,
        allow_web_search=True,
        allow_consensus=True,
        allow_swarm=True,
    ),
    ThinkingLevel.MAX: ThinkingCaps(
        level=ThinkingLevel.MAX,
        max_dt=500.0,
        max_wall_seconds=14400.0,
        max_loops=100,
        max_agents=12,
        max_output_tokens=32768,
        allow_tools=True,
        allow_web_search=True,
        allow_consensus=True,
        allow_swarm=True,
    ),
}

LEVEL_NAMES: dict[str, ThinkingLevel] = {
    "minimal": ThinkingLevel.MINIMAL,
    "min": ThinkingLevel.MINIMAL,
    "1": ThinkingLevel.MINIMAL,
    "low": ThinkingLevel.LOW,
    "2": ThinkingLevel.LOW,
    "medium": ThinkingLevel.MEDIUM,
    "med": ThinkingLevel.MEDIUM,
    "3": ThinkingLevel.MEDIUM,
    "high": ThinkingLevel.HIGH,
    "4": ThinkingLevel.HIGH,
    "max": ThinkingLevel.MAX,
    "5": ThinkingLevel.MAX,
}


def parse_thinking_level(value: str) -> ThinkingLevel:
    logger.debug(f"Entered into parse_thinking_level: value={value!r}")
    level = LEVEL_NAMES.get(value.lower().strip())
    if level is None:
        logger.warning(f"Unknown thinking level '{value}', defaulting to medium")
        return ThinkingLevel.MEDIUM
    return level


def get_caps(level: ThinkingLevel) -> ThinkingCaps:
    logger.debug(f"Entered into get_caps: level={level.name}")
    return LEVEL_CAPS[level]


def describe_level(level: ThinkingLevel) -> str:
    logger.debug(f"Entered into describe_level: level={level.name}")
    caps = LEVEL_CAPS[level]
    dt_str = f"{caps.max_dt:.0f} DT" if caps.max_dt > 0 else "unlimited"
    features = []
    if caps.allow_tools:
        features.append("tools")
    if caps.allow_web_search:
        features.append("web")
    if caps.allow_consensus:
        features.append("consensus")
    if caps.allow_swarm:
        features.append("swarm")
    feat_str = ", ".join(features) if features else "no tools"
    return (
        f"{level.name.lower()} (level {level.value}): "
        f"max {dt_str}, {caps.max_wall_seconds:.0f}s wall, "
        f"{caps.max_loops} loops, {caps.max_agents} agents, "
        f"[{feat_str}]"
    )
