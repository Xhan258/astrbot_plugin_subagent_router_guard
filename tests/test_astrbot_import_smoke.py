from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path


class AstrBotImportSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved_modules = dict(sys.modules)

    def tearDown(self) -> None:
        sys.modules.clear()
        sys.modules.update(self._saved_modules)

    @staticmethod
    def _module(name: str, *, package: bool = False) -> types.ModuleType:
        module = types.ModuleType(name)
        if package:
            module.__path__ = []  # type: ignore[attr-defined]
        sys.modules[name] = module
        return module

    def test_plugin_imports_with_v4275_public_api_shape(self) -> None:
        # The user's v4.27.5 error proves that Context is not exported by
        # astrbot.api. Deliberately expose only the documented split imports.
        self._module("astrbot", package=True)
        api = self._module("astrbot.api", package=True)
        api.AstrBotConfig = dict
        api.logger = types.SimpleNamespace(info=lambda *args, **kwargs: None)
        api_star = self._module("astrbot.api.star")

        class Star:
            def __init__(self, context: object) -> None:
                self.context = context

        api_star.Context = object
        api_star.Star = Star

        self._module("astrbot.core", package=True)
        self._module("astrbot.core.agent", package=True)
        handoff = self._module("astrbot.core.agent.handoff")
        class HandoffTool:
            name = "transfer_to_worker"

        handoff.HandoffTool = HandoffTool
        self._module("astrbot.core.agent.runners", package=True)
        runner_module = self._module("astrbot.core.agent.runners.tool_loop_agent_runner")

        class ToolLoopAgentRunner:
            async def _handle_function_tools(self, req: object, response: object):
                if False:
                    yield None

        runner_module.ToolLoopAgentRunner = ToolLoopAgentRunner
        executor_module = self._module("astrbot.core.astr_agent_tool_exec")

        class FunctionToolExecutor:
            @classmethod
            async def execute(cls, tool: object, run_context: object, **kwargs: object):
                yield "first"
                yield "second"

        executor_module.FunctionToolExecutor = FunctionToolExecutor
        mcp = self._module("mcp", package=True)
        mcp_types = self._module("mcp.types")

        class CallToolResult:
            def __init__(self, content: object) -> None:
                self.content = content

        class TextContent:
            def __init__(self, **kwargs: object) -> None:
                self.kwargs = kwargs

        mcp.types = mcp_types
        mcp_types.CallToolResult = CallToolResult
        mcp_types.TextContent = TextContent

        data = self._module("data", package=True)
        plugins = self._module("data.plugins", package=True)
        plugin_package = self._module(
            "data.plugins.astrbot_plugin_subagent_router_guard", package=True
        )
        data.plugins = plugins
        plugins.astrbot_plugin_subagent_router_guard = plugin_package
        main_path = Path(__file__).resolve().parents[1] / "main.py"
        plugin_package.__path__ = [str(main_path.parent)]  # type: ignore[attr-defined]
        spec = importlib.util.spec_from_file_location(
            "data.plugins.astrbot_plugin_subagent_router_guard.main", main_path
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)

        original_execute = FunctionToolExecutor.__dict__["execute"]
        original_handle = ToolLoopAgentRunner.__dict__["_handle_function_tools"]
        plugin = module.SubAgentRouterGuard(object(), {})
        self.assertIsNot(FunctionToolExecutor.__dict__["execute"], original_execute)

        async def consume_one_item_per_task() -> list[str]:
            iterator = FunctionToolExecutor.execute(HandoffTool(), object())
            results = []
            for _ in range(2):
                results.append(await asyncio.create_task(anext(iterator)))
            with self.assertRaises(StopAsyncIteration):
                await asyncio.create_task(anext(iterator))
            return results

        self.assertEqual(asyncio.run(consume_one_item_per_task()), ["first", "second"])
        asyncio.run(plugin.terminate())
        self.assertIs(FunctionToolExecutor.__dict__["execute"], original_execute)
        self.assertIs(ToolLoopAgentRunner.__dict__["_handle_function_tools"], original_handle)
