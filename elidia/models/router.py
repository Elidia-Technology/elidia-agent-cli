import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class RouteDecision:
    model: str
    reason: str
    task_type: str


TASK_TYPE_DEFAULTS = {
    "chat": "deepseek-chat",
    "code": "claude-sonnet-5",
    "reasoning": "deepseek-reasoner",
    "creative": "gpt-5",
    "vision": "gpt-5-vision",
    "embedding": "bge-m3",
    "cheap": "gpt-5-mini",
}

CODE_KEYWORDS = {
    "code", "function", "debug", "refactor", "test", "implement", "fix", "bug",
    "class", "method", "api", "endpoint", "database", "schema", "migration",
    "deploy", "dockerfile", "ci", "lint", "typescript", "python", "rust", "java",
}

REASONING_KEYWORDS = {
    "analyze", "compare", "evaluate", "prove", "derive", "calculate",
    "strategy", "plan", "architecture", "tradeoff", "reasoning", "logic",
    "math", "theorem", "proof", "optimize",
}

CREATIVE_KEYWORDS = {
    "generate image", "create image", "draw", "design", "logo", "illustration",
    "video", "animation", "music", "song", "voice", "voiceover", "audio",
    "narration", "tts", "text to speech",
}


class ModelRouter:
    """Selects the best model for a given task.

    Phase 0: static rule-based routing.
    Phase 2 will replace this with adaptive routing based on outcome_memory.
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
        model = self._overrides.get(task_type) or TASK_TYPE_DEFAULTS.get(task_type, "deepseek-chat")

        return RouteDecision(
            model=model,
            reason=f"Auto-routed for {task_type} task",
            task_type=task_type,
        )

    def _classify_task(self, message: str, mode: str) -> str:
        """Classify the task type from the message content and mode."""
        logger.debug(f"Entered into _classify_task: mode={mode}")

        if mode == "code":
            return "code"
        if mode == "research" or mode == "think":
            return "reasoning"
        if mode == "create":
            return "creative"

        msg_lower = message.lower()

        for kw in CREATIVE_KEYWORDS:
            if kw in msg_lower:
                return "creative"

        words = set(msg_lower.split())

        code_score = len(words & CODE_KEYWORDS)
        reasoning_score = len(words & REASONING_KEYWORDS)

        if code_score >= 2:
            return "code"
        if reasoning_score >= 2:
            return "reasoning"

        if len(message) < 50:
            return "cheap"

        return "chat"

    def get_model_for_type(self, task_type: str) -> str:
        """Get the configured model for a specific task type."""
        logger.debug(f"Entered into get_model_for_type: task_type={task_type}")
        return self._overrides.get(task_type) or TASK_TYPE_DEFAULTS.get(task_type, "deepseek-chat")
