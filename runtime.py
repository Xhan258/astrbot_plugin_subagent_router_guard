"""AstrBot execution-entry wrapper.

AstrBot's event hooks observe tool calls but do not provide a documented veto.
This wrapper sits at FunctionToolExecutor.execute(), before the executor reaches
the tool handler, MCP call, or Handoff implementation.
"""

from __future__ import annotations

import asyncio
import contextvars
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, field
from typing import Any

from astrbot.api import logger
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
_HANDOFF_PROGRESS: contextvars.ContextVar["HandoffProgress | None"] = (
    contextvars.ContextVar("subagent_router_guard_handoff_progress", default=None)
)


@dataclass
class ToolProgress:
    """One child-tool invocation, without arguments or tool-result content."""

    name: str
    state: str = "running"


@dataclass
class HandoffProgress:
    """Minimal, privacy-preserving progress that survives a cancelled Handoff."""

    tools: list[ToolProgress] = field(default_factory=list)
    reported: bool = False

    def begin(self, tool_name: str) -> ToolProgress:
        item = ToolProgress(name=tool_name)
        self.tools.append(item)
        return item

    def report_text(self) -> str:
        completed = [item.name for item in self.tools if item.state == "completed"]
        unfinished = [item.name for item in self.tools if item.state != "completed"]
        lines = ["子代理任务已停止。"]
        if completed:
            lines.extend(["", "已完成的工具："])
            lines.extend(f"- {name}" for name in completed)
        if unfinished:
            lines.extend(["", "未完成的工具："])
            lines.extend(f"- {name}（已停止）" for name in unfinished)
        if not self.tools:
            lines.extend(["", "子代理尚未开始调用工具。"])
        return "\n".join(lines)


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
            executor_cls: type[FunctionToolExecutor],
            tool: Any,
            run_context: Any,
            **tool_args: Any,
        ) -> AsyncGenerator[Any, None]:
            runtime = GuardRuntime._active
            # Read directly from the class dictionary: a stored classmethod is a
            # descriptor, and normal attribute access would bind it again.
            original_descriptor = GuardRuntime.__dict__.get("_original_execute")
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
            runtime._debug(
                run_context,
                "subagent" if _SUBAGENT_DEPTH.get() else "main",
                tool_name,
                decision,
            )
            if decision.action == "delegate":
                yield CallToolResult(
                    content=[TextContent(type="text", text=runtime._delegate_message())]
                )
                return

            if is_handoff:
                original = original_descriptor.__get__(None, executor_cls)
                # A background Handoff is detached from the parent Agent Run,
                # so AstrBot cannot stop it when the user stops the main Agent.
                # Keep every Handoff in the parent cancellation chain instead.
                handoff_args = dict(tool_args)
                handoff_args["background_task"] = False
                iterator = original(tool, run_context, **handoff_args)
                parent_progress = _HANDOFF_PROGRESS.get()
                progress = parent_progress or HandoffProgress()
                try:
                    while True:
                        # AstrBot may consume each anext(iterator) in a different
                        # asyncio Task. A ContextVar token must be reset in the
                        # exact Context where it was created, so never let it span
                        # this wrapper's yield boundary.
                        depth_token = _SUBAGENT_DEPTH.set(_SUBAGENT_DEPTH.get() + 1)
                        progress_token = _HANDOFF_PROGRESS.set(progress)
                        try:
                            item = await anext(iterator)
                        except StopAsyncIteration:
                            return
                        finally:
                            _HANDOFF_PROGRESS.reset(progress_token)
                            _SUBAGENT_DEPTH.reset(depth_token)
                        yield item
                except asyncio.CancelledError:
                    # Explicitly close the native Handoff generator before
                    # re-raising cancellation. This lets cancellation reach the
                    # nested Agent Run instead of leaving it running in the
                    # background.
                    await runtime._close_iterator(iterator)
                    if parent_progress is None:
                        await runtime._send_stop_report(run_context, progress)
                    raise
                return

            original = original_descriptor.__get__(None, executor_cls)
            activity = None
            progress = _HANDOFF_PROGRESS.get()
            if _SUBAGENT_DEPTH.get() > 0 and progress is not None:
                activity = progress.begin(tool_name)
            try:
                async for item in original(tool, run_context, **tool_args):
                    yield item
            except asyncio.CancelledError:
                if activity is not None:
                    activity.state = "cancelled"
                raise
            except Exception:
                if activity is not None:
                    activity.state = "failed"
                raise
            else:
                if activity is not None:
                    activity.state = "completed"

        async def guarded_handle(
            runner: ToolLoopAgentRunner, req: Any, llm_response: Any
        ) -> AsyncGenerator[Any, None]:
            runtime = GuardRuntime._active
            original = GuardRuntime.__dict__.get("_original_handle")
            if runtime is None or original is None:
                async for item in original(runner, req, llm_response):
                    yield item
                return
            config = GuardConfig.from_mapping(runtime._config_getter())
            toolset = getattr(runner, "_skill_like_raw_tool_set", None) or getattr(
                req, "func_tool", None
            )
            available = bool(
                toolset and any(runtime._is_handoff(t, config) for t in toolset)
            )
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
        original_execute = type(self).__dict__.get("_original_execute")
        original_handle = type(self).__dict__.get("_original_handle")
        if original_execute is not None:
            FunctionToolExecutor.execute = original_execute
        if original_handle is not None:
            ToolLoopAgentRunner._handle_function_tools = original_handle
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
        return bool(
            config.auto_detect_handoff
            and prefix
            and str(getattr(tool, "name", "")).startswith(prefix)
        )

    def _delegate_message(self) -> str:
        message = self._config_getter().get("delegate_required_message", "")
        if isinstance(message, str) and message.strip():
            return message
        return "DELEGATE_REQUIRED\n\nDelegate the remaining task using an available handoff tool."

    @staticmethod
    async def _close_iterator(iterator: AsyncGenerator[Any, None]) -> None:
        try:
            await iterator.aclose()
        except Exception:
            # Cancellation is already in progress. Cleanup must not turn a
            # requested stop into a new tool-execution failure.
            pass

    @staticmethod
    async def _send_stop_report(run_context: Any, progress: HandoffProgress) -> None:
        if progress.reported:
            return
        progress.reported = True
        event = getattr(getattr(run_context, "context", None), "event", None)
        plain_result = getattr(event, "plain_result", None)
        send = getattr(event, "send", None)
        if not callable(plain_result) or not callable(send):
            logger.warning("[SubAgentRouterGuard] cannot send subagent stop report")
            return
        try:
            await send(plain_result(progress.report_text()))
        except Exception:
            logger.exception(
                "[SubAgentRouterGuard] failed to send subagent stop report"
            )

    def _debug(
        self, run_context: Any, agent_type: str, tool_name: str, decision: Any
    ) -> None:
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
