"""AstrBot execution-entry wrapper.

AstrBot's event hooks observe tool calls but do not provide a documented veto.
This wrapper sits at FunctionToolExecutor.execute(), before the executor reaches
the tool handler, MCP call, or Handoff implementation.
"""

from __future__ import annotations

import contextvars
from collections.abc import AsyncGenerator, Callable
from typing import Any

from astrbot.core import logger
from astrbot.core.agent.handoff import HandoffTool
from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner
from astrbot.core.astr_agent_tool_exec import FunctionToolExecutor
from mcp.types import CallToolResult, TextContent

from .guard import BudgetLedger, GuardConfig

_SUBAGENT_DEPTH: contextvars.ContextVar[int] = contextvars.ContextVar(
    "subagent_router_guard_depth", default=0
)
_HANDOFF_AVAILABLE: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "subagent_router_guard_handoff_available", default=False
)


class GuardRuntime:
    """Owns reversible process-level wrappers for one loaded plugin instance."""

    _active: "GuardRuntime | None" = None
    _original_execute: Any = None
    _original_handle: Any = None

    def __init__(self, config_getter: Callable[[], dict[str, Any]]) -> None:
        self._config_getter = config_getter
        self._ledger = BudgetLedger()
        self._installed = False

    def install(self) -> None:
        active = type(self)._active
        if active is not None:
            # AstrBot may construct the replacement plugin before terminating the
            # old one. Transfer ownership so the old instance cannot restore the
            # wrappers underneath the replacement during that reload window.
            type(self)._active = self
            self._installed = True
            return

        cls = type(self)
        # Keep the descriptor, rather than the bound method returned by attribute
        # access, so an unload restores the class exactly as AstrBot installed it.
        cls._original_execute = FunctionToolExecutor.__dict__["execute"]
        cls._original_handle = ToolLoopAgentRunner._handle_function_tools

        async def guarded_execute(
            executor_cls: type[FunctionToolExecutor], tool: Any, run_context: Any, **tool_args: Any
        ) -> AsyncGenerator[Any, None]:
            runtime = GuardRuntime._active
            original_descriptor = GuardRuntime._original_execute
            if runtime is None or original_descriptor is None:
                original = original_descriptor.__get__(None, executor_cls)
                async for item in original(tool, run_context, **tool_args):
                    yield item
                return

            config = GuardConfig.from_mapping(runtime._config_getter())
            tool_name = str(getattr(tool, "name", ""))
            is_handoff = runtime._is_handoff(tool, config)
            decision = await runtime._ledger.decide(
                run=run_context,
                tool_name=tool_name,
                is_subagent=_SUBAGENT_DEPTH.get() > 0,
                is_handoff=is_handoff,
                handoff_available=_HANDOFF_AVAILABLE.get(),
                config=config,
            )
            runtime._debug(run_context, "subagent" if _SUBAGENT_DEPTH.get() else "main", tool_name, decision)
            if decision.action == "delegate":
                yield CallToolResult(
                    content=[TextContent(type="text", text=runtime._delegate_message())]
                )
                return

            if is_handoff:
                token = _SUBAGENT_DEPTH.set(_SUBAGENT_DEPTH.get() + 1)
                try:
                    original = original_descriptor.__get__(None, executor_cls)
                    async for item in original(tool, run_context, **tool_args):
                        yield item
                finally:
                    _SUBAGENT_DEPTH.reset(token)
                return

            original = original_descriptor.__get__(None, executor_cls)
            async for item in original(tool, run_context, **tool_args):
                yield item

        async def guarded_handle(
            runner: ToolLoopAgentRunner, req: Any, llm_response: Any
        ) -> AsyncGenerator[Any, None]:
            runtime = GuardRuntime._active
            original = GuardRuntime._original_handle
            if runtime is None or original is None:
                async for item in original(runner, req, llm_response):
                    yield item
                return
            config = GuardConfig.from_mapping(runtime._config_getter())
            toolset = getattr(runner, "_skill_like_raw_tool_set", None) or getattr(req, "func_tool", None)
            available = bool(toolset and any(runtime._is_handoff(t, config) for t in toolset))
            token = _HANDOFF_AVAILABLE.set(available)
            try:
                async for item in original(runner, req, llm_response):
                    yield item
            finally:
                _HANDOFF_AVAILABLE.reset(token)

        FunctionToolExecutor.execute = classmethod(guarded_execute)
        ToolLoopAgentRunner._handle_function_tools = guarded_handle
        cls._active = self
        self._installed = True
        logger.info("[SubAgentRouterGuard] execution wrapper installed")

    def uninstall(self) -> None:
        if not self._installed or type(self)._active is not self:
            return
        FunctionToolExecutor.execute = type(self)._original_execute
        ToolLoopAgentRunner._handle_function_tools = type(self)._original_handle
        type(self)._active = None
        type(self)._original_execute = None
        type(self)._original_handle = None
        self._installed = False
        logger.info("[SubAgentRouterGuard] execution wrapper restored")

    @staticmethod
    def _is_handoff(tool: Any, config: GuardConfig) -> bool:
        if isinstance(tool, HandoffTool):
            return True
        prefix = config.handoff_prefix
        return bool(config.auto_detect_handoff and prefix and str(getattr(tool, "name", "")).startswith(prefix))

    def _delegate_message(self) -> str:
        message = self._config_getter().get("delegate_required_message", "")
        if isinstance(message, str) and message.strip():
            return message
        return "DELEGATE_REQUIRED\n\nDelegate the remaining task using an available handoff tool."

    def _debug(self, run_context: Any, agent_type: str, tool_name: str, decision: Any) -> None:
        if not bool(self._config_getter().get("debug_log", False)):
            return
        event = getattr(getattr(run_context, "context", None), "event", None)
        session = getattr(event, "unified_msg_origin", "unknown")
        logger.info(
            "[SubAgentRouterGuard] session=%s agent=%s tool=%s used=%s/%s action=%s reason=%s",
            session,
            agent_type,
            tool_name,
            decision.used,
            decision.budget,
            decision.action,
            decision.reason,
        )
