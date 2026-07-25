import logging
from typing import Any

from elidia.memory.outcomes import OutcomeTracker

logger = logging.getLogger(__name__)


class PatternLearner:
    """Analyzes outcome history and derives model/approach preferences."""

    def __init__(self, tracker: OutcomeTracker) -> None:
        logger.debug("Entered into PatternLearner.__init__")
        self._tracker = tracker

    def analyze(self) -> dict[str, Any]:
        logger.debug("Entered into analyze")
        stats = self._tracker.get_stats()

        preferences: dict[str, str] = {}
        insights: list[str] = []

        task_models: dict[str, list[tuple[str, float, int]]] = {}

        for key, s in stats.items():
            task = s["task_type"]
            model = s["model"]
            count = s["count"]
            if count < 3:
                continue

            success_rate = s["successes"] / count
            task_models.setdefault(task, []).append((model, success_rate, count))

        for task, models in task_models.items():
            models.sort(key=lambda x: (-x[1], -x[2]))
            best_model, best_rate, best_count = models[0]

            if best_rate >= 0.7:
                preferences[task] = best_model
                insights.append(
                    f"{task}: {best_model} performs best ({best_rate:.0%} success, {best_count} runs)"
                )

            if len(models) >= 2:
                worst_model, worst_rate, worst_count = models[-1]
                if worst_rate < 0.4 and worst_count >= 3:
                    insights.append(
                        f"{task}: avoid {worst_model} ({worst_rate:.0%} success, {worst_count} runs)"
                    )

        cost_stats: dict[str, list[tuple[str, float]]] = {}
        for key, s in stats.items():
            if s["count"] < 3:
                continue
            task = s["task_type"]
            model = s["model"]
            avg_cost = s["total_cost_dt"] / s["count"]
            cost_stats.setdefault(task, []).append((model, avg_cost))

        for task, costs in cost_stats.items():
            costs.sort(key=lambda x: x[1])
            cheapest, cheap_cost = costs[0]
            if len(costs) >= 2:
                priciest, price_cost = costs[-1]
                if price_cost > cheap_cost * 3:
                    insights.append(
                        f"{task}: {cheapest} is {price_cost / max(cheap_cost, 0.001):.1f}x cheaper than {priciest}"
                    )

        return {
            "preferences": preferences,
            "insights": insights,
            "analyzed_combinations": len(stats),
        }

    def get_recommended_model(self, task_type: str) -> str | None:
        logger.debug(f"Entered into get_recommended_model: task_type={task_type}")
        return self._tracker.get_best_model(task_type)
