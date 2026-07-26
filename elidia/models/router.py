import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class RouteDecision:
    model: str
    reason: str
    task_type: str


TASK_TYPE_DEFAULTS = {
    "chat": "deepseek-v4-flash",
    "code": "claude-sonnet-4-6",
    "reasoning": "deepseek-v4-pro",
    "creative": "gpt-5",
    "vision": "gpt-4o",
    "embedding": "text-embedding-3-small",
    "cheap": "gpt-4.1-mini",
}


class ModelRouter:
    """Selects the best model for a given task.

    Routes by mode (LLM-classified or user-specified), not keyword matching.
    The mode classifier (classify_mode) determines HOW to execute; the router
    determines WHICH model to use based on that classification.
    """

    def __init__(self, config_models: dict[str, str] | None = None):
        logger.debug("Entered into ModelRouter.__init__")
        self._overrides = config_models or {}
        self._forced_model: str | None = None

    def force_model(self, model: str | None) -> None:
        """Override auto-routing with a specific model. None to revert to auto."""
        logger.debug(f"Entered into force_model: model={model}")
        self._forced_model = model

    def route(self, user_message: str, mode: str = "chat") -> RouteDecision:
        """Select the best model for the given message and mode."""
        logger.debug(f"Entered into route: mode={mode}, msg_len={len(user_message)}")

        if self._forced_model:
            return RouteDecision(
                model=self._forced_model,
                reason="User-selected model override",
                task_type="override",
            )

        task_type = self._classify_task(user_message, mode)
        model = self._overrides.get(task_type) or TASK_TYPE_DEFAULTS.get(task_type, "deepseek-v4-flash")

        return RouteDecision(
            model=model,
            reason=f"Auto-routed for {task_type} task",
            task_type=task_type,
        )

    def _classify_task(self, message: str, mode: str) -> str:
        """Classify the task type from the mode (LLM-determined or user-specified).

        Mode-to-task mapping:
          code      → code tasks (claude-sonnet-4-6)
          research  → reasoning tasks (deepseek-v4-pro)
          think     → reasoning (deepseek-v4-pro)
          create    → creative tasks (gpt-5)
          chat      → general chat (deepseek-v4-flash), or cheap for trivial messages

        No keyword/regex matching — the LLM classifier (classify_mode) determines
        the execution mode, and this method simply maps that mode to the appropriate
        model category.
        """
        logger.debug(f"Entered into _classify_task: mode={mode}")

        if mode == "code":
            return "code"
        if mode == "research" or mode == "think":
            return "reasoning"
        if mode == "create":
            return "creative"

        # For default chat mode: use cheap model for very short messages,
        # standard chat model for everything else.
        if len(message.strip()) < 20:
            return "cheap"

        return "chat"

    def get_model_for_type(self, task_type: str) -> str:
        """Get the configured model for a specific task type."""
        logger.debug(f"Entered into get_model_for_type: task_type={task_type}")
        return self._overrides.get(task_type) or TASK_TYPE_DEFAULTS.get(task_type, "deepseek-v4-flash")
