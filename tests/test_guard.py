from __future__ import annotations

import asyncio
import unittest

from guard import BudgetLedger, GuardConfig, MatchRule


class BudgetLedgerTests(unittest.IsolatedAsyncioTestCase):
    async def test_budget_then_delegate_and_handoff_is_free(self) -> None:
        ledger = BudgetLedger()
        run = object()
        config = GuardConfig(budget=2)
        first = await ledger.decide(run=run, tool_name="a", is_subagent=False, is_handoff=False, handoff_available=True, config=config)
        second = await ledger.decide(run=run, tool_name="b", is_subagent=False, is_handoff=False, handoff_available=True, config=config)
        blocked = await ledger.decide(run=run, tool_name="c", is_subagent=False, is_handoff=False, handoff_available=True, config=config)
        handoff = await ledger.decide(run=run, tool_name="transfer_to_any", is_subagent=False, is_handoff=True, handoff_available=True, config=config)
        self.assertEqual((first.action, second.action, blocked.action, handoff.action), ("allow", "allow", "delegate", "handoff"))

    async def test_subagent_is_unlimited_and_new_run_resets(self) -> None:
        ledger = BudgetLedger()
        config = GuardConfig(budget=1)
        run = object()
        for _ in range(10):
            decision = await ledger.decide(run=run, tool_name="tool", is_subagent=True, is_handoff=False, handoff_available=True, config=config)
            self.assertEqual(decision.action, "exempt")
        new_run = await ledger.decide(run=object(), tool_name="tool", is_subagent=False, is_handoff=False, handoff_available=True, config=config)
        self.assertEqual(new_run.action, "allow")

    async def test_parallel_reservation_is_atomic(self) -> None:
        ledger = BudgetLedger()
        run = object()
        config = GuardConfig(budget=1)
        results = await asyncio.gather(*[
            ledger.decide(run=run, tool_name=f"tool_{i}", is_subagent=False, is_handoff=False, handoff_available=True, config=config)
            for i in range(3)
        ])
        self.assertEqual([item.action for item in results].count("allow"), 1)
        self.assertEqual([item.action for item in results].count("delegate"), 2)

    async def test_rules_and_no_handoff_default(self) -> None:
        ledger = BudgetLedger()
        run = object()
        config = GuardConfig(budget=0, exempt_rules=(MatchRule("prefix", "light_"),), immediate_rules=(MatchRule("regex", r"^scan_"),))
        exempt = await ledger.decide(run=run, tool_name="light_ping", is_subagent=False, is_handoff=False, handoff_available=True, config=config)
        immediate = await ledger.decide(run=run, tool_name="scan_everything", is_subagent=False, is_handoff=False, handoff_available=True, config=config)
        no_handoff = await ledger.decide(run=object(), tool_name="scan_everything", is_subagent=False, is_handoff=False, handoff_available=False, config=config)
        self.assertEqual(exempt.action, "exempt")
        self.assertEqual(immediate.reason, "immediate_rule")
        self.assertEqual(no_handoff.action, "allow")
