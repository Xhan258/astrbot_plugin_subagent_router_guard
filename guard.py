"""Pure, deterministic policy for SubAgent Router Guard.

This module deliberately has no AstrBot dependency so its accounting and matching
behaviour can be unit-tested without a running bot.
"""

from __future__ import annotations

import asyncio
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Literal

DecisionAction = Literal["allow", "exempt", "handoff", "delegate"]


@dataclass(frozen=True)
class MatchRule:
    mode: str
    pattern: str

    def matches(self, value: str) -> bool:
        if not self.pattern:
            return False
        if self.mode == "exact":
            return value == self.pattern
        if self.mode == "prefix":
            return value.startswith(self.pattern)
        if self.mode == "contains":
            return self.pattern in value
        if self.mode == "regex":
            try:
                return re.search(self.pattern, value) is not None
            except re.error:
                return False
        return False


@dataclass(frozen=True)
class GuardConfig:
    enabled: bool = True
    budget: int = 2
    auto_detect_handoff: bool = True
    handoff_prefix: str = "transfer_to_"
    exempt_rules: tuple[MatchRule, ...] = ()
    immediate_rules: tuple[MatchRule, ...] = ()
    no_handoff_behavior: Literal["allow", "reject"] = "allow"

    @classmethod
    def from_mapping(cls, config: dict[str, Any]) -> "GuardConfig":
        def rules(key: str) -> tuple[MatchRule, ...]:
            result: list[MatchRule] = []
            raw = config.get(key, [])
            if not isinstance(raw, list):
                return ()
            for item in raw:
                if not isinstance(item, dict):
                    continue
                mode = str(item.get("mode", "exact"))
                pattern = str(item.get("pattern", "")).strip()
                if mode in {"exact", "prefix", "contains", "regex"} and pattern:
                    result.append(MatchRule(mode, pattern))
            return tuple(result)

        budget = config.get("main_tool_budget", 2)
        try:
            budget = max(0, int(budget))
        except (TypeError, ValueError):
            budget = 2
        no_handoff = str(config.get("no_handoff_behavior", "allow"))
        return cls(
            enabled=bool(config.get("enabled", True)),
            budget=budget,
            auto_detect_handoff=bool(config.get("auto_detect_handoff", True)),
            handoff_prefix=str(config.get("handoff_prefix", "transfer_to_")),
            exempt_rules=rules("budget_exempt_rules"),
            immediate_rules=rules("immediate_delegate_rules"),
            no_handoff_behavior="reject" if no_handoff == "reject" else "allow",
        )


@dataclass(frozen=True)
class Decision:
    action: DecisionAction
    reason: str
    used: int
    budget: int


@dataclass
class _RunState:
    owner: object
    used: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class BudgetLedger:
    """Atomically reserves budget per *run object*, never per clock or session."""

    def __init__(self, max_retained_runs: int = 4096) -> None:
        self._states: OrderedDict[int, _RunState] = OrderedDict()
        self._max_retained_runs = max_retained_runs

    def _state_for(self, run: object) -> _RunState:
        key = id(run)
        state = self._states.get(key)
        if state is None or state.owner is not run:
            state = _RunState(owner=run)
            self._states[key] = state
        self._states.move_to_end(key)
        while len(self._states) > self._max_retained_runs:
            self._states.popitem(last=False)
        return state

    async def decide(
        self,
        *,
        run: object,
        tool_name: str,
        is_subagent: bool,
        is_handoff: bool,
        handoff_available: bool,
        config: GuardConfig,
    ) -> Decision:
        state = self._state_for(run)
        if not config.enabled:
            return Decision("allow", "disabled", state.used, config.budget)
        if is_subagent:
            return Decision("exempt", "subagent", state.used, config.budget)
        if is_handoff:
            return Decision("handoff", "handoff", state.used, config.budget)
        if not handoff_available and config.no_handoff_behavior == "allow":
            return Decision("allow", "no_handoff_allow", state.used, config.budget)
        if any(rule.matches(tool_name) for rule in config.immediate_rules):
            return Decision("delegate", "immediate_rule", state.used, config.budget)
        if any(rule.matches(tool_name) for rule in config.exempt_rules):
            return Decision("exempt", "exempt_rule", state.used, config.budget)
        async with state.lock:
            if state.used < config.budget:
                state.used += 1
                return Decision("allow", "budget_available", state.used, config.budget)
            return Decision("delegate", "budget_exhausted", state.used, config.budget)
