"""Tests for elidia.workflow.engine — YAML workflow parsing and execution."""
import asyncio

import pytest

from elidia.workflow.engine import (
    Workflow,
    WorkflowExecutor,
    _evaluate_condition,
    _render_template,
    parse_workflow,
)


class TestParseWorkflow:
    def test_basic_parse(self):
        wf = parse_workflow("name: test\nsteps:\n  - name: s1\n    type: llm\n    prompt: hi")
        assert wf.name == "test"
        assert len(wf.steps) == 1
        assert wf.steps[0].name == "s1"
        assert wf.steps[0].type == "llm"

    def test_with_variables(self):
        wf = parse_workflow("name: t\nvariables:\n  x: 42\nsteps: []")
        assert wf.variables["x"] == 42

    def test_with_description(self):
        wf = parse_workflow("name: t\ndescription: A test\nsteps: []")
        assert wf.description == "A test"

    def test_nested_parallel(self):
        yaml_text = """
name: parallel_test
steps:
  - name: group
    type: parallel
    steps:
      - name: a
        type: shell
        command: echo a
      - name: b
        type: shell
        command: echo b
"""
        wf = parse_workflow(yaml_text)
        assert wf.steps[0].type == "parallel"
        assert len(wf.steps[0].steps) == 2

    def test_condition_and_output(self):
        yaml_text = """
name: cond
steps:
  - name: step1
    type: shell
    command: echo hello
    output: result
    condition: "should_run"
"""
        wf = parse_workflow(yaml_text)
        assert wf.steps[0].condition == "should_run"
        assert wf.steps[0].output_var == "result"

    def test_invalid_yaml_raises(self):
        with pytest.raises(ValueError):
            parse_workflow("not_a_dict")


class TestRenderTemplate:
    def test_double_braces(self):
        assert _render_template("Hello {{name}}", {"name": "World"}) == "Hello World"

    def test_dollar_braces(self):
        assert _render_template("Hi ${name}", {"name": "Bob"}) == "Hi Bob"

    def test_multiple_vars(self):
        result = _render_template("{{a}} + {{b}}", {"a": "1", "b": "2"})
        assert result == "1 + 2"

    def test_no_vars(self):
        assert _render_template("plain text", {}) == "plain text"


class TestEvaluateCondition:
    def test_truthy(self):
        assert _evaluate_condition("x", {"x": True})
        assert _evaluate_condition("x", {"x": "yes"})

    def test_falsy(self):
        assert not _evaluate_condition("x", {"x": False})
        assert not _evaluate_condition("x", {"x": ""})
        assert not _evaluate_condition("x", {"x": "false"})

    def test_equality(self):
        assert _evaluate_condition("a == b", {"a": "1", "b": "1"})
        assert not _evaluate_condition("a == b", {"a": "1", "b": "2"})

    def test_inequality(self):
        assert _evaluate_condition("a != b", {"a": "1", "b": "2"})
        assert not _evaluate_condition("a != b", {"a": "1", "b": "1"})

    def test_in_operator(self):
        assert _evaluate_condition("x in items", {"x": "a", "items": "abc"})


class TestWorkflowExecutor:
    @pytest.mark.asyncio
    async def test_shell_step(self):
        wf = parse_workflow("""
name: shell_test
steps:
  - name: echo
    type: shell
    command: echo hello
    output: result
""")
        executor = WorkflowExecutor()
        events = []
        async for event in executor.run(wf):
            events.append(event)

        kinds = [e.kind for e in events]
        assert "start" in kinds
        assert "done" in kinds
        done = [e for e in events if e.kind == "done"][0]
        assert done.data["completed"] == 1

    @pytest.mark.asyncio
    async def test_condition_skip(self):
        wf = parse_workflow("""
name: skip_test
steps:
  - name: skipped
    type: shell
    command: echo never
    condition: "run_flag"
""")
        executor = WorkflowExecutor()
        events = []
        async for event in executor.run(wf):
            events.append(event)

        done = [e for e in events if e.kind == "done"][0]
        assert done.data["skipped"] == 1
        assert done.data["completed"] == 0

    @pytest.mark.asyncio
    async def test_variable_substitution(self):
        wf = parse_workflow("""
name: var_test
variables:
  greeting: hello
steps:
  - name: echo
    type: shell
    command: "echo {{greeting}} world"
    output: result
""")
        executor = WorkflowExecutor()
        events = []
        async for event in executor.run(wf):
            events.append(event)

        step_done = [e for e in events if e.kind == "step_done" and e.data["name"] == "echo"][0]
        assert "hello world" in step_done.data["output_preview"]

    @pytest.mark.asyncio
    async def test_chained_steps(self):
        wf = parse_workflow("""
name: chain
steps:
  - name: step1
    type: shell
    command: echo first
    output: r1
  - name: step2
    type: shell
    command: echo second
    output: r2
""")
        executor = WorkflowExecutor()
        events = []
        async for event in executor.run(wf):
            events.append(event)

        done = [e for e in events if e.kind == "done"][0]
        assert done.data["completed"] == 2
