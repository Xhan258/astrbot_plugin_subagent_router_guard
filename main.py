from __future__ import annotations

from astrbot.api import AstrBotConfig, Context, Star, register

from .runtime import GuardRuntime


@register(
    "astrbot_plugin_subagent_router_guard",
    "AstrBot Community",
    "Deterministic main-agent tool budget with native SubAgent handoff fallback.",
    "0.1.0",
)
class SubAgentRouterGuard(Star):
    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        self.config = config
        self._runtime = GuardRuntime(lambda: dict(self.config))
        self._runtime.install()

    async def terminate(self) -> None:
        self._runtime.uninstall()
