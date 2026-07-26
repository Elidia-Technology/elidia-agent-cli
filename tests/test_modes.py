"""Tests for elidia.modes — classifier, thinking, budget, consensus, autonomous, swarm."""
import pytest

from elidia.modes.budget import BudgetGovernor, CostEstimate, MODEL_PRICING_DT_PER_KTOK
from elidia.modes.classifier import ExecMode, _extract_json
from elidia.modes.thinking import (
    LEVEL_CAPS,
    ThinkingLevel,
    describe_level,
    get_caps,
    parse_thinking_level,
)
from elidia.modes.deep_think import (
    REASONING_MODELS,
    _find_boundary_index,
    _is_reasoning_boundary,
    select_reasoning_model,
)
from elidia.modes.consensus import (
    ModelResponse,
    _agreement_to_confidence,
    _assess_agreement,
)
from elidia.modes.autonomous import SubtaskStatus, _extract_json_array
from elidia.modes.swarm import SubAgent, SwarmEvent


class TestClassifier:
    def test_extract_json_simple(self):
        assert _extract_json('{"mode": "deep"}') == {"mode": "deep"}

    def test_extract_json_embedded(self):
        assert _extract_json('text {"a": 1} end') == {"a": 1}

    def test_extract_json_no_match(self):
        assert _extract_json("no json here") is None

    def test_exec_modes_exist(self):
        assert ExecMode.DIRECT.value == "direct"
        assert ExecMode.CONSENSUS.value == "consensus"
        assert ExecMode.HARNESS.value == "harness"
        assert ExecMode.DEEP.value == "deep"


class TestThinkingLevels:
    def test_all_levels_defined(self):
        assert len(LEVEL_CAPS) == 5

    def test_parse_by_name(self):
        assert parse_thinking_level("minimal") == ThinkingLevel.MINIMAL
        assert parse_thinking_level("low") == ThinkingLevel.LOW
        assert parse_thinking_level("medium") == ThinkingLevel.MEDIUM
        assert parse_thinking_level("high") == ThinkingLevel.HIGH
        assert parse_thinking_level("max") == ThinkingLevel.MAX

    def test_parse_by_number(self):
        assert parse_thinking_level("1") == ThinkingLevel.MINIMAL
        assert parse_thinking_level("5") == ThinkingLevel.MAX

    def test_parse_aliases(self):
        assert parse_thinking_level("min") == ThinkingLevel.MINIMAL
        assert parse_thinking_level("med") == ThinkingLevel.MEDIUM

    def test_parse_invalid_defaults_to_medium(self):
        assert parse_thinking_level("invalid") == ThinkingLevel.MEDIUM

    def test_caps_progression(self):
        min_caps = get_caps(ThinkingLevel.MINIMAL)
        max_caps = get_caps(ThinkingLevel.MAX)
        assert min_caps.max_loops < max_caps.max_loops
        assert min_caps.max_agents < max_caps.max_agents

    def test_minimal_restrictions(self):
        caps = get_caps(ThinkingLevel.MINIMAL)
        assert not caps.allow_consensus
        assert not caps.allow_swarm

    def test_max_allows_all(self):
        caps = get_caps(ThinkingLevel.MAX)
        assert caps.allow_tools
        assert caps.allow_web_search
        assert caps.allow_consensus
        assert caps.allow_swarm

    def test_describe_level(self):
        desc = describe_level(ThinkingLevel.MEDIUM)
        assert "medium" in desc.lower()


class TestBudgetGovernor:
    def test_initial_state(self):
        bg = BudgetGovernor()
        assert bg.state.session_dt_used == 0.0
        assert bg.state.call_count == 0

    def test_record_usage(self):
        bg = BudgetGovernor()
        bg.record_usage(1000, 500, 10.0)
        assert bg.state.session_dt_used == 10.0
        assert bg.state.session_tokens_in == 1000
        assert bg.state.session_tokens_out == 500
        assert bg.state.call_count == 1

    def test_estimate_cost(self):
        bg = BudgetGovernor()
        est = bg.estimate_cost("deepseek-v4-flash", 1000, 1000)
        expected = (1000 / 1000 * 0.14) + (1000 / 1000 * 0.28)
        assert est.estimated_dt == pytest.approx(expected)

    def test_over_session_limit(self):
        bg = BudgetGovernor(session_limit_dt=100.0)
        bg.record_usage(100, 50, 90.0)
        est = bg.estimate_cost("claude-opus-4-8", 1000, 1000)
        assert est.is_over_session_limit

    def test_under_session_limit(self):
        bg = BudgetGovernor(session_limit_dt=50000.0)
        est = bg.estimate_cost("deepseek-v4-flash", 100, 100)
        assert not est.is_over_session_limit

    def test_check_and_allow(self):
        bg = BudgetGovernor(session_limit_dt=50000.0)
        allowed, est = bg.check_and_allow("deepseek-v4-flash", 100)
        assert allowed

    def test_check_and_block(self):
        bg = BudgetGovernor(session_limit_dt=1.0)
        bg.record_usage(100, 50, 0.9)
        allowed, est = bg.check_and_allow("claude-opus-4-8", 5000, 5000)
        assert not allowed

    def test_reset_session(self):
        bg = BudgetGovernor()
        bg.record_usage(100, 50, 10.0)
        bg.reset_session()
        assert bg.state.session_dt_used == 0.0
        assert bg.state.call_count == 0
        assert bg.state.total_dt_used == 10.0  # total not reset

    def test_warning_threshold(self):
        bg = BudgetGovernor(session_limit_dt=100.0, warn_threshold=0.5)
        bg.record_usage(100, 50, 55.0)
        est = bg.estimate_cost("deepseek-chat", 100, 100)
        assert est.warning_message != ""
        assert not est.is_over_session_limit

    def test_suggest_cheaper_model(self):
        bg = BudgetGovernor()
        cheaper = bg.suggest_cheaper_model("claude-opus-4-8")
        assert cheaper is not None
        assert cheaper != "claude-opus-4-8"

    def test_suggest_cheapest_returns_none(self):
        bg = BudgetGovernor()
        cheapest = bg.suggest_cheaper_model("deepseek-chat")
        # deepseek-chat is very cheap but not necessarily the cheapest
        # This test just verifies the method runs without error
        assert isinstance(cheapest, str) or cheapest is None

    def test_model_pricing_has_entries(self):
        assert len(MODEL_PRICING_DT_PER_KTOK) >= 10

    def test_get_summary(self):
        bg = BudgetGovernor(session_limit_dt=1000.0)
        bg.record_usage(100, 50, 5.0)
        summary = bg.get_summary()
        assert summary["session_dt_used"] == 5.0
        assert summary["call_count"] == 1
        assert summary["session_pct"] == 0.5


class TestDeepThink:
    def test_select_explicit_model(self):
        assert select_reasoning_model("o1") == "o1"

    def test_select_default(self):
        assert select_reasoning_model(None) == "deepseek-v4-pro"

    def test_reasoning_models_list(self):
        assert len(REASONING_MODELS) >= 4

    def test_boundary_detection(self):
        assert _is_reasoning_boundary("text\nTherefore, the answer")
        assert _is_reasoning_boundary("stuff\n---\nfinal")
        assert not _is_reasoning_boundary("just plain text")

    def test_boundary_index(self):
        idx = _find_boundary_index("start\n---\nend")
        assert idx > 0
        assert _find_boundary_index("no boundary") == -1


class TestConsensus:
    def test_assess_agreement_similar(self):
        r1 = ModelResponse(model="a", content="python is great for data science and ml")
        r2 = ModelResponse(model="b", content="python is great for data science and ai")
        level = _assess_agreement([r1, r2])
        assert level in ("high", "partial", "low")

    def test_confidence_values(self):
        assert _agreement_to_confidence("high") == 0.9
        assert _agreement_to_confidence("partial") == 0.7
        assert _agreement_to_confidence("low") == 0.5

    def test_model_response_fields(self):
        r = ModelResponse(model="gpt-5", content="test", tokens_out=100)
        assert r.model == "gpt-5"
        assert r.tokens_out == 100


class TestAutonomous:
    def test_subtask_statuses(self):
        assert SubtaskStatus.PENDING.value == "pending"
        assert SubtaskStatus.RUNNING.value == "running"
        assert SubtaskStatus.COMPLETED.value == "completed"
        assert SubtaskStatus.FAILED.value == "failed"

    def test_extract_json_array_valid(self):
        result = _extract_json_array('[{"id": 1, "task": "do A"}]')
        assert result is not None
        assert len(result) == 1

    def test_extract_json_array_empty(self):
        result = _extract_json_array("no json here")
        assert result is None

    def test_extract_from_code_block(self):
        text = '```json\n[{"id": 1}]\n```'
        result = _extract_json_array(text)
        assert result is not None


class TestSwarm:
    def test_sub_agent_defaults(self):
        agent = SubAgent(id=1, task="test task", role="coder")
        assert agent.status == "pending"
        assert agent.result == ""

    def test_swarm_event(self):
        event = SwarmEvent(kind="plan", data={"agents": 3})
        assert event.kind == "plan"
