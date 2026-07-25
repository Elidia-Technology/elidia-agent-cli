import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from elidia.memory.store import MemoryEntry, MemoryStore, MemoryTier

logger = logging.getLogger(__name__)


@dataclass
class Outcome:
    model: str
    task_type: str
    approach: str
    success: bool
    tokens_used: int = 0
    elapsed_ms: int = 0
    cost_dt: float = 0.0
    domain: str = ""
    notes: str = ""
    timestamp: float = 0.0


class OutcomeTracker:
    """Tracks success/failure per model+approach+domain for adaptive routing."""

    def __init__(self, store: MemoryStore) -> None:
        logger.debug("Entered into OutcomeTracker.__init__")
        self._store = store

    def record(self, outcome: Outcome) -> str:
        logger.debug(f"Entered into record: model={outcome.model}, task={outcome.task_type}, success={outcome.success}")
        if not outcome.timestamp:
            outcome.timestamp = time.time()

        key = f"outcome:{outcome.model}:{outcome.task_type}"
        entry = MemoryEntry(
            tier=MemoryTier.USER,
            key=key,
            content=json.dumps({
                "model": outcome.model,
                "task_type": outcome.task_type,
                "approach": outcome.approach,
                "success": outcome.success,
                "tokens_used": outcome.tokens_used,
                "elapsed_ms": outcome.elapsed_ms,
                "cost_dt": outcome.cost_dt,
                "domain": outcome.domain,
                "notes": outcome.notes,
            }, default=str),
            source="outcome_tracker",
            metadata={
                "type": "outcome",
                "model": outcome.model,
                "task_type": outcome.task_type,
                "success": outcome.success,
            },
        )
        return self._store.save(entry)

    def get_stats(self, model: str = "", task_type: str = "") -> dict[str, Any]:
        logger.debug(f"Entered into get_stats: model={model}, task_type={task_type}")

        prefix = "outcome:"
        if model:
            prefix += f"{model}:"
            if task_type:
                prefix += task_type

        entries = self._store.search_text(prefix, tier=MemoryTier.USER, limit=200)

        stats: dict[str, dict[str, Any]] = {}
        for entry in entries:
            if not entry.key.startswith("outcome:"):
                continue
            try:
                data = json.loads(entry.content)
            except json.JSONDecodeError:
                continue

            m = data.get("model", "?")
            t = data.get("task_type", "?")
            stat_key = f"{m}:{t}"

            if stat_key not in stats:
                stats[stat_key] = {
                    "model": m,
                    "task_type": t,
                    "successes": 0,
                    "failures": 0,
                    "total_tokens": 0,
                    "total_cost_dt": 0.0,
                    "avg_elapsed_ms": 0.0,
                    "count": 0,
                }

            s = stats[stat_key]
            s["count"] += 1
            if data.get("success"):
                s["successes"] += 1
            else:
                s["failures"] += 1
            s["total_tokens"] += data.get("tokens_used", 0)
            s["total_cost_dt"] += data.get("cost_dt", 0.0)
            elapsed = data.get("elapsed_ms", 0)
            s["avg_elapsed_ms"] = (s["avg_elapsed_ms"] * (s["count"] - 1) + elapsed) / s["count"]

        return stats

    def get_best_model(self, task_type: str) -> str | None:
        logger.debug(f"Entered into get_best_model: task_type={task_type}")
        stats = self.get_stats(task_type=task_type)
        if not stats:
            return None

        best_model = None
        best_rate = -1.0

        for key, s in stats.items():
            if s["task_type"] != task_type:
                continue
            if s["count"] < 3:
                continue
            rate = s["successes"] / s["count"]
            if rate > best_rate:
                best_rate = rate
                best_model = s["model"]

        return best_model
