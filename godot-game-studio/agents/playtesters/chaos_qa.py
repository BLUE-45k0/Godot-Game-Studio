"""Chaos QA Tester: tries to break the game and lists edge cases."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from agents.playtesters.base_playtester import PlaytesterAgent
from core.schemas import PlaytestReport


class EdgeCase(BaseModel):
    scenario: str = Field(min_length=10)
    input_or_value: str = Field(min_length=2)
    expected_failure: str = Field(min_length=5)
    target_file: str = ""
    severity: Literal["low", "medium", "high"]


class ChaosReport(PlaytestReport):
    """PlaytestReport plus a list of concrete edge cases."""

    edge_cases: list[EdgeCase] = Field(min_length=5, max_length=15)


_ROLE = """You are the CHAOS QA TESTER: your only goal is to break the game. You read the code looking
for crashes and softlocks, and you produce a list of edge cases. Think of: mashing or holding
inputs, opposite directions at once, pressing everything during a transition, restarting many times
in a row or during an animation, a timer/tween finishing after its node was freed, null or freed
nodes, divide by zero or empty arrays, extreme or negative balance values (0 health, huge spawn
counts, speed 0), spawning on top of the player, overlapping triggers firing twice, losing focus,
window resize, score overflow, and states the code never handles. For each edge case give the
scenario, the exact input or value, the failure you expect, the target file, and a severity."""


class ChaosQAPlaytester(PlaytesterAgent):
    persona_key = "chaos_qa"
    name = "playtest_chaos_qa"
    role = _ROLE
    schema = ChaosReport
    task = ("Find ways to break the game from the code and list 5-15 concrete edge cases. The score "
            "reflects robustness (10 = hard to break). Put the most severe issues in top_3_problems "
            "and the code changes that harden the game in fix_suggestions.")
