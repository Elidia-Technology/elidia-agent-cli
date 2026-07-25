import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from elidia.api.client import AiUtilsClient, ChatMessage
from elidia.mcp.registry import MCPRegistry
from elidia.models.router import ModelRouter
from elidia.modes.budget import BudgetGovernor
from elidia.modes.classifier import ExecMode, classify_mode
from elidia.modes.consensus import run_consensus
from elidia.modes.deep_think import deep_think_stream, select_reasoning_model
from elidia.modes.thinking import ThinkingLevel, get_caps
from elidia.permissions.audit import AuditLogger
from elidia.permissions.manager import PermissionManager
from elidia.tools.base import ToolRegistry

logger = logging.getLogger(__name__)

MAX_TOOL_LOOPS = 25


@dataclass
class AgentEvent:
    kind: str  # "thinking", "content", "tool_call", "tool_result", "usage", "error", "done", "mode_info", "budget_warning"
    data: Any = None


@dataclass
class AgentState:
    messages: list[ChatMessage] = field(default_factory=list)
    model: str = ""
    mode: str = "chat"
    exec_mode: ExecMode = ExecMode.DIRECT
    thinking_level: ThinkingLevel = ThinkingLevel.MEDIUM
    loop_count: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    total_cost_dt: float = 0.0
    tools_called: list[str] = field(default_factory=list)


class AgentLoop:
    """Core agent loop: route → act → observe → reflect."""

    def __init__(
        self,
        client: AiUtilsClient,
        tool_registry: ToolRegistry,
        mcp_registry: MCPRegistry | None = None,
        model_router: ModelRouter | None = None,
        permission_manager: PermissionManager | None = None,
        audit: AuditLogger | None = None,
        budget: BudgetGovernor | None = None,
        max_loops: int = MAX_TOOL_LOOPS,
        thinking_level: ThinkingLevel | None = None,
    ) -> None:
        logger.debug("Entered into AgentLoop.__init__")
        self._client = client
        self._tools = tool_registry
        self._mcp = mcp_registry
        self._router = model_router or ModelRouter()
        self._permissions = permission_manager
        self._audit = audit
        self._budget = budget or BudgetGovernor()
        self._max_loops = max_loops
        self._thinking_level = thinking_level or ThinkingLevel.MEDIUM

    async def run(
        self,
        messages: list[ChatMessage],
        mode: str = "chat",
        forced_model: str | None = None,
        session_id: str = "",
        thinking_level: ThinkingLevel | None = None,
    ) -> AsyncIterator[AgentEvent]:
        logger.debug(f"Entered into AgentLoop.run: mode={mode}, msg_count={len(messages)}")

        state = AgentState(messages=list(messages), mode=mode)
        state.thinking_level = thinking_level or self._thinking_level
        caps = get_caps(state.thinking_level)

        user_text = messages[-1].content if messages else ""

        try:
            mode_decision = await classify_mode(self._client, user_text)
            state.exec_mode = mode_decision.mode
            yield AgentEvent(kind="mode_info", data={
                "exec_mode": state.exec_mode.value,
                "confidence": mode_decision.confidence,
                "reason": mode_decision.reason,
            })
        except Exception as e:
            logger.warning(f"Mode classification failed, defaulting to DIRECT: {e}")
            state.exec_mode = ExecMode.DIRECT

        decision = self._router.route(user_text, mode=mode)
        state.model = forced_model or decision.model
        yield AgentEvent(kind="thinking", data={"model": state.model, "reason": decision.reason})

        if state.exec_mode == ExecMode.DEEP:
            async for event in self._run_deep_think(state, user_text, session_id):
                yield event
            return

        if state.exec_mode == ExecMode.CONSENSUS:
            async for event in self._run_consensus(state, user_text, session_id):
                yield event
            return

        effective_max_loops = min(self._max_loops, caps.max_loops)
        all_tool_schemas = self._build_tool_schemas() if caps.allow_tools else []

        while state.loop_count <= effective_max_loops:
            state.loop_count += 1
            logger.debug(f"Agent loop iteration {state.loop_count}, model={state.model}")

            api_messages = self._build_api_messages(state)

            request_payload: dict[str, Any] = {
                "model": state.model,
                "messages": api_messages,
                "stream": False,
                "temperature": 0.3 if mode == "code" else 0.7,
            }

            if all_tool_schemas:
                request_payload["tools"] = all_tool_schemas
                request_payload["tool_choice"] = "auto"

            if self._budget:
                est_input = sum(len(m.content) // 4 for m in state.messages)
                allowed, cost_est = self._budget.check_and_allow(state.model, est_input)
                if not allowed:
                    yield AgentEvent(kind="budget_warning", data={
                        "message": cost_est.warning_message,
                        "estimated_dt": cost_est.estimated_dt,
                    })
                    cheaper = self._budget.suggest_cheaper_model(state.model)
                    if cheaper:
                        state.model = cheaper
                        yield AgentEvent(kind="thinking", data={
                            "model": state.model,
                            "reason": "Budget: switched to cheaper model",
                        })
                    else:
                        yield AgentEvent(kind="error", data=f"Budget exceeded: {cost_est.warning_message}")
                        return
                elif cost_est.warning_message:
                    yield AgentEvent(kind="budget_warning", data={
                        "message": cost_est.warning_message,
                        "estimated_dt": cost_est.estimated_dt,
                    })

            try:
                response = await self._client.chat_completion(
                    messages=state.messages,
                    model=state.model,
                    temperature=request_payload.get("temperature", 0.7),
                )
            except RuntimeError as e:
                yield AgentEvent(kind="error", data=str(e))
                return

            state.tokens_in += response.tokens_in
            state.tokens_out += response.tokens_out
            cost = response.tokens_in * 0.001 + response.tokens_out * 0.002
            state.total_cost_dt += cost

            if self._budget:
                self._budget.record_usage(response.tokens_in, response.tokens_out, cost)

            yield AgentEvent(kind="usage", data={
                "tokens_in": response.tokens_in,
                "tokens_out": response.tokens_out,
                "cost_dt": cost,
                "elapsed_ms": response.elapsed_ms,
            })

            tool_calls = self._extract_tool_calls(response.content)

            if not tool_calls:
                if response.content:
                    yield AgentEvent(kind="content", data=response.content)
                state.messages.append(ChatMessage(role="assistant", content=response.content))
                break

            state.messages.append(ChatMessage(role="assistant", content=response.content))

            for tc in tool_calls:
                tool_name = tc["name"]
                tool_args = tc.get("arguments", {})
                call_id = tc.get("id", tool_name)

                yield AgentEvent(kind="tool_call", data={"name": tool_name, "arguments": tool_args, "id": call_id})

                if self._permissions:
                    action_type = self._classify_tool_action(tool_name, tool_args)
                    allowed = self._permissions.check(
                        action=action_type,
                        session_id=session_id,
                        path=tool_args.get("path"),
                        command=tool_args.get("command"),
                        description=f"Tool call: {tool_name}({json.dumps(tool_args, default=str)[:200]})",
                    )
                    if not allowed:
                        result_text = f"Permission denied for {tool_name}"
                        yield AgentEvent(kind="tool_result", data={"name": tool_name, "content": result_text, "is_error": True})
                        state.messages.append(ChatMessage(role="user", content=f"[Tool result for {tool_name}]: {result_text}"))
                        continue

                result_text, is_error = await self._execute_tool(tool_name, tool_args)
                state.tools_called.append(tool_name)

                yield AgentEvent(kind="tool_result", data={"name": tool_name, "content": result_text[:2000], "is_error": is_error})

                if self._audit:
                    self._audit.log_tool_call(
                        tool_name=tool_name,
                        arguments=tool_args,
                        result_preview=result_text[:500],
                        session_id=session_id,
                    )

                state.messages.append(ChatMessage(
                    role="user",
                    content=f"[Tool result for {tool_name}]:\n{result_text[:8000]}",
                ))

        else:
            yield AgentEvent(kind="error", data=f"Agent exceeded maximum tool loop count ({self._max_loops})")

        yield AgentEvent(kind="done", data={
            "loops": state.loop_count,
            "tools_called": state.tools_called,
            "total_tokens_in": state.tokens_in,
            "total_tokens_out": state.tokens_out,
            "total_cost_dt": state.total_cost_dt,
        })

    def _build_tool_schemas(self) -> list[dict[str, Any]]:
        logger.debug("Entered into _build_tool_schemas")
        schemas = self._tools.get_schemas_for_llm()
        if self._mcp:
            schemas.extend(self._mcp.get_tool_schemas_for_llm())
        return schemas

    def _build_api_messages(self, state: AgentState) -> list[dict[str, str]]:
        logger.debug("Entered into _build_api_messages")
        system_prompt = self._get_system_prompt(state.mode)
        result: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
        for msg in state.messages:
            result.append({"role": msg.role, "content": msg.content})
        return result

    def _get_system_prompt(self, mode: str) -> str:
        logger.debug(f"Entered into _get_system_prompt: mode={mode}")

        tool_names = [t.name for t in self._tools.list_tools()]
        if self._mcp:
            tool_names.extend(t.name for t in self._mcp.list_all_tools())

        tool_list = ", ".join(tool_names) if tool_names else "none"

        base = (
            "You are Elidia, an AI coding agent. You help users with software engineering tasks "
            "by reading files, writing code, running commands, and searching the web.\n\n"
            f"Available tools: {tool_list}\n\n"
            "To use a tool, respond with a JSON block:\n"
            "```tool\n"
            '{"name": "tool_name", "arguments": {"arg1": "value1"}}\n'
            "```\n\n"
            "You can call multiple tools by including multiple ```tool blocks.\n"
            "After tool results are returned, continue reasoning and call more tools or give your final answer.\n"
            "When you have enough information, give your final response directly without tool blocks.\n"
        )

        mode_prompts = {
            "code": "Focus on writing clean, correct, production-quality code. Prefer editing existing files over creating new ones.",
            "research": "Focus on thorough research. Search the web, read documentation, and synthesize findings.",
            "think": "Think step by step. Show your reasoning process before arriving at conclusions.",
            "create": "Focus on creative content generation — writing, brainstorming, design.",
            "chat": "Be helpful and conversational. Use tools when needed to answer questions accurately.",
        }
        base += "\n" + mode_prompts.get(mode, mode_prompts["chat"])

        return base

    def _extract_tool_calls(self, content: str) -> list[dict[str, Any]]:
        logger.debug("Entered into _extract_tool_calls")
        calls: list[dict[str, Any]] = []

        import re
        pattern = r"```tool\s*\n(.*?)```"
        matches = re.findall(pattern, content, re.DOTALL)

        for match in matches:
            try:
                parsed = json.loads(match.strip())
                if isinstance(parsed, dict) and "name" in parsed:
                    calls.append(parsed)
            except json.JSONDecodeError:
                logger.warning(f"Failed to parse tool call JSON: {match[:100]}")

        return calls

    async def _execute_tool(self, name: str, arguments: dict[str, Any]) -> tuple[str, bool]:
        logger.debug(f"Entered into _execute_tool: name={name}")

        builtin = self._tools.get(name)
        if builtin:
            result = await self._tools.call(name, arguments)
            return result.content, result.is_error

        if self._mcp:
            mcp_match = self._mcp.find_tool(name)
            if mcp_match:
                result = await self._mcp.call_tool(name, arguments)
                content = "\n".join(
                    item.get("text", str(item)) if isinstance(item, dict) else str(item)
                    for item in result.content
                )
                return content, result.is_error

        return f"Tool '{name}' not found in any registry", True

    def _classify_tool_action(self, tool_name: str, args: dict[str, Any]) -> str:
        logger.debug(f"Entered into _classify_tool_action: tool_name={tool_name}")

        action_map = {
            "file_read": "file_read",
            "file_list": "file_read",
            "file_glob": "file_read",
            "file_grep": "file_read",
            "file_write": "file_write",
            "file_edit": "file_write",
            "file_delete": "file_delete",
            "command_exec": "command_exec",
            "git_status": "file_read",
            "git_diff": "file_read",
            "git_log": "file_read",
            "git_branch": "file_read",
            "git_commit": "command_exec",
            "web_search": "web_search",
            "http_fetch": "web_search",
        }
        return action_map.get(tool_name, "mcp_call_session")

    async def _run_deep_think(
        self, state: AgentState, user_text: str, session_id: str,
    ) -> AsyncIterator[AgentEvent]:
        logger.debug(f"Entered into _run_deep_think: model={state.model}")
        reasoning_model = select_reasoning_model(state.model)
        state.model = reasoning_model

        yield AgentEvent(kind="thinking", data={
            "model": reasoning_model,
            "reason": "Deep think: using reasoning model",
        })

        full_reasoning = ""
        full_content = ""

        try:
            async for event in deep_think_stream(
                client=self._client,
                messages=state.messages,
                model=reasoning_model,
            ):
                if event.kind == "reasoning":
                    full_reasoning += event.data
                elif event.kind == "content":
                    full_content += event.data
                elif event.kind == "usage":
                    state.tokens_in += event.data.get("tokens_in", 0)
                    state.tokens_out += event.data.get("tokens_out", 0)
                    cost = event.data.get("cost_dt", 0.0)
                    state.total_cost_dt += cost
                    if self._budget:
                        self._budget.record_usage(
                            event.data.get("tokens_in", 0),
                            event.data.get("tokens_out", 0),
                            cost,
                        )
                    yield AgentEvent(kind="usage", data=event.data)
                elif event.kind == "error":
                    yield AgentEvent(kind="error", data=event.data)
                    return
        except Exception as e:
            yield AgentEvent(kind="error", data=str(e))
            return

        output = full_content or full_reasoning
        if output:
            yield AgentEvent(kind="content", data=output)

        yield AgentEvent(kind="done", data={
            "loops": 1,
            "tools_called": [],
            "total_tokens_in": state.tokens_in,
            "total_tokens_out": state.tokens_out,
            "total_cost_dt": state.total_cost_dt,
            "exec_mode": "deep",
        })

    async def _run_consensus(
        self, state: AgentState, user_text: str, session_id: str,
    ) -> AsyncIterator[AgentEvent]:
        logger.debug("Entered into _run_consensus")

        yield AgentEvent(kind="thinking", data={
            "model": "consensus",
            "reason": "Running multiple models in parallel for consensus",
        })

        try:
            result = await run_consensus(
                client=self._client,
                message=user_text,
                context=[m.content for m in state.messages[:-1]],
            )
        except Exception as e:
            yield AgentEvent(kind="error", data=f"Consensus failed: {e}")
            return

        output_parts = [f"**Consensus Result** (agreement: {result.agreement_level}, confidence: {result.confidence:.0%})\n"]
        output_parts.append(result.synthesis)
        output_parts.append("\n\n---\n**Individual Responses:**")
        for r in result.responses:
            output_parts.append(f"\n**{r.model}**: {r.content[:500]}")

        full_output = "\n".join(output_parts)
        yield AgentEvent(kind="content", data=full_output)

        total_tokens = sum(r.tokens_out for r in result.responses)
        yield AgentEvent(kind="usage", data={
            "tokens_in": 0,
            "tokens_out": total_tokens,
            "cost_dt": 0.0,
        })

        yield AgentEvent(kind="done", data={
            "loops": 1,
            "tools_called": [],
            "total_tokens_in": 0,
            "total_tokens_out": total_tokens,
            "total_cost_dt": 0.0,
            "exec_mode": "consensus",
        })
