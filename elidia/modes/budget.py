"""Budget governance — session-level cost caps and pre-execution estimation.

Tracks running costs (DT and tokens), warns before exceeding thresholds,
and hard-blocks when session or total limits are reached.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

MODEL_PRICING_DT_PER_KTOK: dict[str, tuple[float, float]] = {
    "deepseek-chat": (0.14, 0.28),
    "deepseek-reasoner": (0.55, 2.19),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-haiku-4-5": (0.80, 4.0),
    "claude-opus-4-8": (15.0, 75.0),
    "gpt-5": (5.0, 15.0),
    "gpt-5-mini": (0.15, 0.60),
    "gpt-5-vision": (5.0, 15.0),
    "gemini-2.5-pro": (1.25, 10.0),
    "gemini-2.5-flash": (0.15, 0.60),
    "llama-4-maverick": (0.20, 0.60),
    "qwen-3-235b": (0.30, 1.20),
}

DEFAULT_PRICING: tuple[float, float] = (1.0, 3.0)


@dataclass
class CostEstimate:
    model: str
    estimated_input_tokens: int
    estimated_output_tokens: int
    estimated_dt: float
    is_over_session_limit: bool = False
    is_over_total_limit: bool = False
    warning_message: str = ""


@dataclass
class BudgetState:
    session_dt_used: float = 0.0
    session_tokens_in: int = 0
    session_tokens_out: int = 0
    total_dt_used: float = 0.0
    call_count: int = 0


class BudgetGovernor:
    """Enforces cost limits on LLM and tool calls within a session."""

    def __init__(
        self,
        session_limit_dt: float = 50000.0,
        total_limit_dt: float = 0.0,
        warn_threshold: float = 0.8,
    ) -> None:
        logger.debug(
            f"Entered into BudgetGovernor.__init__: "
            f"session_limit={session_limit_dt}, total_limit={total_limit_dt}"
        )
        self._session_limit = session_limit_dt
        self._total_limit = total_limit_dt
        self._warn_threshold = warn_threshold
        self._state = BudgetState()

    @property
    def state(self) -> BudgetState:
        return self._state

    def estimate_cost(
        self,
        model: str,
        input_tokens: int,
        expected_output_tokens: int = 2000,
    ) -> CostEstimate:
        logger.debug(
            f"Entered into estimate_cost: model={model}, "
            f"in_tok={input_tokens}, out_tok={expected_output_tokens}"
        )
        pricing = MODEL_PRICING_DT_PER_KTOK.get(model, DEFAULT_PRICING)
        in_cost = (input_tokens / 1000.0) * pricing[0]
        out_cost = (expected_output_tokens / 1000.0) * pricing[1]
        estimated_dt = in_cost + out_cost

        est = CostEstimate(
            model=model,
            estimated_input_tokens=input_tokens,
            estimated_output_tokens=expected_output_tokens,
            estimated_dt=estimated_dt,
        )

        projected = self._state.session_dt_used + estimated_dt

        if self._session_limit > 0 and projected > self._session_limit:
            est.is_over_session_limit = True
            est.warning_message = (
                f"This call (~{estimated_dt:.1f} DT) would exceed session limit "
                f"({projected:.1f}/{self._session_limit:.1f} DT)."
            )
        elif self._session_limit > 0 and projected > self._session_limit * self._warn_threshold:
            pct = (projected / self._session_limit) * 100
            est.warning_message = (
                f"Session budget at {pct:.0f}% ({projected:.1f}/{self._session_limit:.1f} DT)."
            )

        if self._total_limit > 0:
            total_projected = self._state.total_dt_used + estimated_dt
            if total_projected > self._total_limit:
                est.is_over_total_limit = True
                est.warning_message = (
                    f"This call (~{estimated_dt:.1f} DT) would exceed total limit "
                    f"({total_projected:.1f}/{self._total_limit:.1f} DT)."
                )

        return est

    def check_and_allow(
        self,
        model: str,
        input_tokens: int,
        expected_output_tokens: int = 2000,
    ) -> tuple[bool, CostEstimate]:
        logger.debug(f"Entered into check_and_allow: model={model}")
        est = self.estimate_cost(model, input_tokens, expected_output_tokens)
        allowed = not est.is_over_session_limit and not est.is_over_total_limit
        return allowed, est

    def record_usage(
        self,
        tokens_in: int,
        tokens_out: int,
        cost_dt: float,
    ) -> None:
        logger.debug(
            f"Entered into record_usage: in={tokens_in}, out={tokens_out}, cost={cost_dt:.2f}"
        )
        self._state.session_dt_used += cost_dt
        self._state.session_tokens_in += tokens_in
        self._state.session_tokens_out += tokens_out
        self._state.total_dt_used += cost_dt
        self._state.call_count += 1

    def reset_session(self) -> None:
        logger.debug("Entered into reset_session")
        self._state.session_dt_used = 0.0
        self._state.session_tokens_in = 0
        self._state.session_tokens_out = 0
        self._state.call_count = 0

    def get_summary(self) -> dict[str, Any]:
        logger.debug("Entered into get_summary")
        s = self._state
        return {
            "session_dt_used": round(s.session_dt_used, 2),
            "session_limit_dt": self._session_limit,
            "session_pct": round((s.session_dt_used / self._session_limit) * 100, 1) if self._session_limit > 0 else 0.0,
            "session_tokens_in": s.session_tokens_in,
            "session_tokens_out": s.session_tokens_out,
            "total_dt_used": round(s.total_dt_used, 2),
            "total_limit_dt": self._total_limit,
            "call_count": s.call_count,
        }

    def suggest_cheaper_model(self, current_model: str) -> str | None:
        logger.debug(f"Entered into suggest_cheaper_model: current={current_model}")
        current_pricing = MODEL_PRICING_DT_PER_KTOK.get(current_model, DEFAULT_PRICING)
        current_avg = (current_pricing[0] + current_pricing[1]) / 2

        cheapest_model: str | None = None
        cheapest_avg = current_avg

        for model, (inp, out) in MODEL_PRICING_DT_PER_KTOK.items():
            avg = (inp + out) / 2
            if avg < cheapest_avg and model != current_model:
                cheapest_avg = avg
                cheapest_model = model

        return cheapest_model
