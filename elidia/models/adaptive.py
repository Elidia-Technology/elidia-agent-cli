import logging

from elidia.memory.outcomes import OutcomeTracker
from elidia.memory.patterns import PatternLearner
from elidia.models.router import ModelRouter, RouteDecision

logger = logging.getLogger(__name__)


class AdaptiveRouter:
    """Wraps ModelRouter, overriding static rules with learned preferences when available."""

    def __init__(
        self,
        base_router: ModelRouter,
        learner: PatternLearner,
        tracker: OutcomeTracker,
        min_observations: int = 5,
    ) -> None:
        logger.debug("Entered into AdaptiveRouter.__init__")
        self._base = base_router
        self._learner = learner
        self._tracker = tracker
        self._min_observations = min_observations

    def route(self, user_message: str, mode: str = "chat") -> RouteDecision:
        logger.debug(f"Entered into route: mode={mode}")

        base_decision = self._base.route(user_message, mode=mode)

        task_type = base_decision.task_type if hasattr(base_decision, "task_type") else mode

        learned_model = self._learner.get_recommended_model(task_type)
        if learned_model:
            stats = self._tracker.get_stats(model=learned_model, task_type=task_type)
            total_count = sum(s.get("count", 0) for s in stats.values())

            if total_count >= self._min_observations:
                logger.info(
                    f"Adaptive override: {base_decision.model} -> {learned_model} "
                    f"for task_type={task_type} (based on {total_count} observations)"
                )
                return RouteDecision(
                    model=learned_model,
                    reason=f"adaptive: {learned_model} learned from {total_count} outcomes",
                    task_type=task_type,
                )

        return base_decision

    def force_model(self, model: str | None) -> None:
        logger.debug(f"Entered into force_model: model={model}")
        self._base.force_model(model)
