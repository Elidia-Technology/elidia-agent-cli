import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from elidia.api.client import AiUtilsClient, ChatMessage, extract_text
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
        memory_store: Any = None,
        rag_engine: Any = None,
        persona_engine: Any = None,
        project_path: str = "",
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
        self._memory_store = memory_store
        self._rag_engine = rag_engine
        self._persona_engine = persona_engine
        self._project_path = project_path

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

        last_content = messages[-1].content if messages else ""
        has_vision = isinstance(last_content, list)
        # Classification/routing need plain text; the full multimodal
        # content (text + image blocks) stays intact in state.messages for
        # the actual API call.
        user_text = extract_text(last_content)

        try:
            mode_decision = await classify_mode(self._client, user_text)
            state.exec_mode = mode_decision.mode
            yield AgentEvent(kind="mode_info", data={
                "exec_mode": state.exec_mode.value,
                "cost_label": mode_decision.cost_label,
                "reason": mode_decision.reason,
            })
        except Exception as e:
            logger.warning(f"Mode classification failed, defaulting to DIRECT: {e}")
            state.exec_mode = ExecMode.DIRECT

        if has_vision and not forced_model:
            state.model = self._router.get_model_for_type("vision")
            decision_reason = "Vision content attached"
        else:
            decision = self._router.route(user_text, mode=mode)
            state.model = forced_model or decision.model
            decision_reason = decision.reason
        yield AgentEvent(kind="thinking", data={"model": state.model, "reason": decision_reason})

        if state.exec_mode == ExecMode.DEEP:
            async for event in self._run_deep_think(state, user_text, session_id):
                yield event
            return

        if state.exec_mode == ExecMode.CONSENSUS:
            async for event in self._run_consensus(state, user_text, session_id):
                yield event
            return

        if state.exec_mode == ExecMode.HARNESS:
            async for event in self._run_harness(state, user_text, session_id):
                yield event
            return

        effective_max_loops = min(self._max_loops, caps.max_loops)
        all_tool_schemas = self._build_tool_schemas() if caps.allow_tools else []

        while state.loop_count <= effective_max_loops:
            state.loop_count += 1
            logger.debug(f"Agent loop iteration {state.loop_count}, model={state.model}")

            api_messages = self._build_api_messages(state, user_message=user_text)

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
                est_input = sum(len(extract_text(m.content)) // 4 for m in state.messages)
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

    def _build_api_messages(self, state: AgentState, user_message: str = "") -> list[dict[str, str]]:
        logger.debug("Entered into _build_api_messages")
        system_prompt = self._get_system_prompt(state.mode, user_message)
        result: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
        for msg in state.messages:
            result.append({"role": msg.role, "content": msg.content})
        return result

    def _get_system_prompt(self, mode: str, user_message: str = "") -> str:
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

        # Context Fabric: assemble memory + RAG + persona + project rules
        context_parts: list[str] = []

        # 1. Persona overlay (if active)
        if self._persona_engine:
            persona_overlay = self._persona_engine.get_system_prompt_overlay()
            if persona_overlay:
                context_parts.append(f"[Persona]\n{persona_overlay}")

        # 2. Memory recall — semantic search against user's persistent memory
        if self._memory_store and user_message:
            try:
                recalled = self._memory_store.search_text(
                    query=user_message, tier=None, limit=5,
                )
                if recalled:
                    memories = "\n".join(
                        f"- {m.key}: {m.content[:300]}" for m in recalled
                    )
                    context_parts.append(f"[Relevant Memories]\n{memories}")
            except Exception as e:
                logger.debug(f"Memory recall skipped: {e}")

        # 3. RAG — search local documents for relevant context
        if self._rag_engine and user_message:
            try:
                rag_results = self._rag_engine.search(
                    query=user_message, limit=3,
                    project_path=self._project_path,
                )
                if rag_results:
                    docs = "\n---\n".join(
                        f"[{r.document.source}]\n{r.document.content[:500]}"
                        for r in rag_results[:3]
                    )
                    context_parts.append(f"[Relevant Documents]\n{docs}")
            except Exception as e:
                logger.debug(f"RAG search skipped: {e}")

        # 4. Project rules
        if self._project_path:
            try:
                from elidia.config.rules import load_project_rules, format_rules_for_system_prompt
                rules = load_project_rules(self._project_path)
                if rules:
                    context_parts.append(format_rules_for_system_prompt(rules))
            except Exception:
                pass

        if context_parts:
            base += "\n\n--- Context ---\n" + "\n\n".join(context_parts) + "\n---\n"

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
            "browser_navigate": "browser_read",
            "browser_extract_links": "browser_read",
            "browser_screenshot": "browser_read",
            "browser_click": "browser_interact",
            "browser_type": "browser_interact",
            "read_docx": "file_read",
            "read_xlsx": "file_read",
            "read_pptx": "file_read",
            "write_docx": "file_write",
            "write_xlsx": "file_write",
            "db_connect": "db_query",
            "db_query": "db_query",
            "db_list_tables": "db_query",
            "db_describe_table": "db_query",
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
                messages=[ChatMessage(role=m.role, content=m.content) for m in state.messages],
            )
        except Exception as e:
            yield AgentEvent(kind="error", data=f"Consensus failed: {e}")
            return

        output_parts = [f"**Consensus Result** (agreement: {result.agreement}, confidence: {result.confidence:.0%})\n"]
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

    async def _run_harness(
        self, state: AgentState, user_text: str, session_id: str,
    ) -> AsyncIterator[AgentEvent]:
        """SUPERVISOR node — task decomposition + parallel agent execution.

        Uses AutonomousExecutor to:
        1. Decompose the task into subtasks (LLM-based planning)
        2. Execute each subtask via the agent loop's tool infrastructure
        3. Replan on failure, retry, or skip
        4. Checkpoint after each subtask for resume capability
        """
        logger.debug("Entered into _run_harness")

        yield AgentEvent(kind="thinking", data={
            "model": state.model,
            "reason": "Harness mode — decomposing task into subtasks",
        })

        from elidia.modes.autonomous import AutonomousExecutor, AutonomousState

        executor = AutonomousExecutor(
            client=self._client,
            planner_model=state.model,
            max_replans=3,
            max_retries_per_task=2,
        )

        # Build an execute function that uses the agent loop's tools
        async def execute_subtask(subtask_text: str) -> str:
            subtask_state = AgentState(
                messages=[ChatMessage(role="user", content=subtask_text)],
                mode=state.mode,
                thinking_level=state.thinking_level,
            )
            subtask_state.exec_mode = ExecMode.DIRECT
            subtask_state.model = state.model

            caps = get_caps(state.thinking_level)
            all_tool_schemas = self._build_tool_schemas() if caps.allow_tools else []

            max_loops = min(5, caps.max_loops)
            results: list[str] = []

            while subtask_state.loop_count < max_loops:
                subtask_state.loop_count += 1
                api_messages = self._build_api_messages(subtask_state, user_message=subtask_text)

                request_payload: dict[str, Any] = {
                    "model": subtask_state.model,
                    "messages": api_messages,
                    "stream": False,
                    "temperature": 0.3,
                }
                if all_tool_schemas:
                    request_payload["tools"] = all_tool_schemas
                    request_payload["tool_choice"] = "auto"

                response = await self._client.chat_completion(**request_payload)
                content = response.content
                tool_calls = getattr(response, "tool_calls", None) or []

                if tool_calls and all_tool_schemas:
                    for tc in tool_calls:
                        tool_name = tc.get("function", {}).get("name", "") if isinstance(tc, dict) else getattr(tc, "function", {}).get("name", "")
                        tool_args = tc.get("function", {}).get("arguments", {}) if isinstance(tc, dict) else getattr(tc, "function", {}).get("arguments", {})
                        if isinstance(tool_args, str):
                            try:
                                import json
                                tool_args = json.loads(tool_args)
                            except Exception:
                                tool_args = {}
                        if tool_name:
                            result_text, is_error = await self._execute_tool(tool_name, tool_args)
                            results.append(f"Tool {tool_name}: {result_text[:500]}")
                elif content:
                    results.append(content)
                    break
                else:
                    break

            return "\n".join(results) if results else "No output produced"

        try:
            async for event in executor.run(
                task=user_text,
                execute_fn=execute_subtask,
            ):
                if event.kind == "plan":
                    subtask_count = event.data.get("subtask_count", 0)
                    subtasks = event.data.get("subtasks", [])
                    yield AgentEvent(kind="tool_call", data={
                        "name": "plan",
                        "arguments": {"subtask_count": subtask_count},
                    })
                    for st in subtasks:
                        yield AgentEvent(kind="tool_result", data={
                            "name": f"subtask_{st['id']}",
                            "content": st["task"],
                        })

                elif event.kind == "subtask_start":
                    yield AgentEvent(kind="tool_call", data={
                        "name": f"subtask_{event.data['id']}",
                        "arguments": {
                            "task": event.data["task"],
                            "attempt": event.data["attempt"],
                        },
                    })

                elif event.kind == "subtask_result":
                    status = event.data["status"]
                    result_preview = event.data.get("result_preview", "")[:300]
                    yield AgentEvent(kind="tool_result", data={
                        "name": f"subtask_{event.data['id']}",
                        "content": f"[{status}] {result_preview}",
                        "is_error": status == "failed",
                    })

                elif event.kind == "replan":
                    yield AgentEvent(kind="tool_result", data={
                        "name": "replan",
                        "content": event.data.get("reason", "Replan triggered"),
                    })

                elif event.kind == "done":
                    completed = event.data.get("completed", 0)
                    total = event.data.get("total_subtasks", 0)
                    failed = event.data.get("failed", 0)

                    summary_parts = [f"**Task Complete:** {completed}/{total} subtasks completed"]
                    if failed:
                        summary_parts.append(f" ({failed} failed)")
                    yield AgentEvent(kind="content", data="\n".join(summary_parts))
                    yield AgentEvent(kind="done", data={
                        "loops": total,
                        "tools_called": [f"subtask_{i}" for i in range(1, total + 1)],
                        "total_tokens_in": 0,
                        "total_tokens_out": 0,
                        "total_cost_dt": 0.0,
                        "exec_mode": "harness",
                    })

                elif event.kind == "error":
                    yield AgentEvent(kind="error", data=event.data.get("message", str(event.data)))

        except Exception as e:
            yield AgentEvent(kind="error", data=f"Harness execution failed: {e}")
