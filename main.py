from __future__ import annotations

from astrbot.api import AstrBotConfig
from astrbot.api.star import Context, Star

from .runtime import GuardRuntime


class SubAgentRouterGuard(Star):
    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        self.config = config
        self._runtime = GuardRuntime(lambda: dict(self.config))
        self._runtime.install()

    async def terminate(self) -> None:
        self._runtime.uninstall()
