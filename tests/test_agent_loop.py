"""Tests for elidia.agent.loop — the core agent loop.

Regression coverage for the critical bug found 2026-07-26: the system
prompt (tool schemas + Context Fabric) was built (_build_api_messages)
but never actually sent to the model — the real API call used
state.messages directly, which has no system-role message at all. Verified
live at the time: asked the agent to list files via file_list, and instead
of a real tool call it fabricated a plausible-looking directory listing.
This file existed before that fix as an empty gap — there was no test
coverage for agent/loop.py at all prior to this.
"""
from unittest.mock import AsyncMock

import pytest

from elidia.agent.loop import AgentLoop
from elidia.api.client import ChatMessage, ChatResponse
from elidia.tools.base import ToolDefinition, ToolRegistry, ToolResult


class FakeClient:
    """Records every chat_completion call; returns queued canned responses in order."""

    def __init__(self, responses: list[ChatResponse]):
        self._responses = list(responses)
        self.calls: list[dict] = []

    async def chat_completion(self, messages, model, temperature=0.7, max_tokens=None):
        self.calls.append({"messages": messages, "model": model, "temperature": temperature})
        if not self._responses:
            return ChatResponse(content="", model=model)
        return self._responses.pop(0)


def _classify_direct_response() -> ChatResponse:
    return ChatResponse(content='{"mode": "direct", "reason": "simple task"}', model="classifier")


async def _fake_tool_handler(**kwargs) -> ToolResult:
    return ToolResult(content="real-tool-result-42", metadata={"called_with": kwargs})


def _registry_with_test_tool() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(ToolDefinition(
        name="get_answer",
        description="Returns the real answer to a question",
        parameters={"type": "object", "properties": {
            "topic": {"type": "string", "description": "What to ask about"},
        }, "required": ["topic"]},
        handler=_fake_tool_handler,
        category="test",
    ))
    return registry


class TestBuildApiMessages:
    def test_prepends_system_message(self):
        loop = AgentLoop(client=AsyncMock(), tool_registry=ToolRegistry())
        from elidia.agent.loop import AgentState
        state = AgentState(messages=[ChatMessage(role="user", content="hi")])

        result = loop._build_api_messages(state, user_message="hi")

        assert len(result) == 2
        assert result[0].role == "system"
        assert isinstance(result[0], ChatMessage)
        assert result[1].role == "user"
        assert result[1].content == "hi"

    def test_returns_chat_message_objects_not_dicts(self):
        """The exact type mismatch that caused the bug: chat_completion()
        expects list[ChatMessage] (accesses .role/.content), not list[dict]."""
        loop = AgentLoop(client=AsyncMock(), tool_registry=ToolRegistry())
        from elidia.agent.loop import AgentState
        state = AgentState(messages=[])

        result = loop._build_api_messages(state)
        for msg in result:
            assert isinstance(msg, ChatMessage)
            assert hasattr(msg, "role")
            assert hasattr(msg, "content")


class TestFormatToolsForPrompt:
    def test_includes_description_and_parameters_not_just_names(self):
        """The actual root cause: the system prompt used to list bare tool
        names with zero schema info, so the model had no way to know what
        arguments a tool took."""
        registry = _registry_with_test_tool()
        loop = AgentLoop(client=AsyncMock(), tool_registry=registry)

        formatted = loop._format_tools_for_prompt()

        assert "get_answer" in formatted
        assert "Returns the real answer to a question" in formatted
        assert "topic" in formatted
        assert "required" in formatted

    def test_empty_registry_says_none(self):
        loop = AgentLoop(client=AsyncMock(), tool_registry=ToolRegistry())
        assert loop._format_tools_for_prompt() == "none"


class TestSystemPromptActuallyReachesTheModel:
    """The core regression test: run() must call chat_completion with a
    message list that includes the system prompt, not state.messages
    passed through untouched."""

    @pytest.mark.asyncio
    async def test_main_loop_sends_system_prompt(self):
        client = FakeClient(responses=[
            _classify_direct_response(),
            ChatResponse(content="A plain answer with no tool calls.", model="test-model"),
        ])
        loop = AgentLoop(client=client, tool_registry=ToolRegistry())

        events = []
        async for event in loop.run(messages=[ChatMessage(role="user", content="hello")]):
            events.append(event)

        # Second call is the actual reasoning turn (first was mode classification).
        assert len(client.calls) == 2
        reasoning_call = client.calls[1]
        sent_messages = reasoning_call["messages"]
        assert sent_messages[0].role == "system"
        assert "Elidia" in sent_messages[0].content

    @pytest.mark.asyncio
    async def test_system_prompt_lists_registered_tools_with_schema(self):
        registry = _registry_with_test_tool()
        client = FakeClient(responses=[
            _classify_direct_response(),
            ChatResponse(content="No tool needed here.", model="test-model"),
        ])
        loop = AgentLoop(client=client, tool_registry=registry)

        async for _ in loop.run(messages=[ChatMessage(role="user", content="hello")]):
            pass

        system_content = client.calls[1]["messages"][0].content
        assert "get_answer" in system_content
        assert "Returns the real answer to a question" in system_content


class TestToolCallExecutesForReal:
    """Proves a model-emitted ```tool block results in the actual tool
    handler running and a real result being fed back — not a fabricated
    answer standing in for it."""

    @pytest.mark.asyncio
    async def test_tool_call_is_extracted_and_executed(self):
        registry = _registry_with_test_tool()
        tool_call_response = ChatResponse(
            content='I need to look this up.\n```tool\n{"name": "get_answer", "arguments": {"topic": "life"}}\n```',
            model="test-model",
        )
        final_response = ChatResponse(content="The real answer is: real-tool-result-42.", model="test-model")
        client = FakeClient(responses=[_classify_direct_response(), tool_call_response, final_response])
        loop = AgentLoop(client=client, tool_registry=registry)

        events = []
        async for event in loop.run(messages=[ChatMessage(role="user", content="what's the answer?")]):
            events.append(event)

        tool_result_events = [e for e in events if e.kind == "tool_result"]
        assert len(tool_result_events) == 1
        assert "real-tool-result-42" in tool_result_events[0].data["content"]
        assert tool_result_events[0].data["is_error"] is False

        # The follow-up call must include the real tool result in its context.
        followup_messages = client.calls[2]["messages"]
        tool_result_text = " ".join(extract_content(m) for m in followup_messages)
        assert "real-tool-result-42" in tool_result_text

    @pytest.mark.asyncio
    async def test_no_tool_call_produces_plain_content_event(self):
        client = FakeClient(responses=[
            _classify_direct_response(),
            ChatResponse(content="Just a plain response.", model="test-model"),
        ])
        loop = AgentLoop(client=client, tool_registry=ToolRegistry())

        events = []
        async for event in loop.run(messages=[ChatMessage(role="user", content="hi")]):
            events.append(event)

        content_events = [e for e in events if e.kind == "content"]
        assert len(content_events) == 1
        assert content_events[0].data == "Just a plain response."
        tool_result_events = [e for e in events if e.kind == "tool_result"]
        assert len(tool_result_events) == 0


def extract_content(msg: ChatMessage) -> str:
    return msg.content if isinstance(msg.content, str) else str(msg.content)


class TestExtractToolCalls:
    def test_parses_single_tool_block(self):
        loop = AgentLoop(client=AsyncMock(), tool_registry=ToolRegistry())
        content = '```tool\n{"name": "file_read", "arguments": {"path": "x.py"}}\n```'
        calls = loop._extract_tool_calls(content)
        assert len(calls) == 1
        assert calls[0]["name"] == "file_read"
        assert calls[0]["arguments"] == {"path": "x.py"}

    def test_parses_multiple_tool_blocks(self):
        loop = AgentLoop(client=AsyncMock(), tool_registry=ToolRegistry())
        content = (
            '```tool\n{"name": "a", "arguments": {}}\n```\n'
            'some text\n'
            '```tool\n{"name": "b", "arguments": {"x": 1}}\n```'
        )
        calls = loop._extract_tool_calls(content)
        assert [c["name"] for c in calls] == ["a", "b"]

    def test_no_tool_blocks_returns_empty(self):
        loop = AgentLoop(client=AsyncMock(), tool_registry=ToolRegistry())
        assert loop._extract_tool_calls("just a plain sentence.") == []

    def test_malformed_json_is_skipped_not_crashed(self):
        loop = AgentLoop(client=AsyncMock(), tool_registry=ToolRegistry())
        content = '```tool\nnot valid json\n```'
        assert loop._extract_tool_calls(content) == []
