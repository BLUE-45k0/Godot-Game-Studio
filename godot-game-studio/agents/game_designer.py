"""Lead Game Designer: core loop, mechanics, controls, win/lose, juice plan."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from agents.base_agent import SNAKE_CASE, BaseAgent
from core.schemas import ProjectContext

# Keys the file writer knows how to map to Godot physical keycodes.
ALLOWED_KEYS: frozenset[str] = frozenset(
    {*"ABCDEFGHIJKLMNOPQRSTUVWXYZ", "Left", "Right", "Up", "Down", "Space", "Enter",
     "Escape", "Shift", "Ctrl", "MouseLeft"}
)

RuleName = Literal[
    "clear_goal_and_loss", "game_juice", "risk_reward", "difficulty_progression", "instant_retry"
]


class Mechanic(BaseModel):
    name: str = Field(min_length=2)
    description: str = Field(min_length=10)
    player_input: str = Field(min_length=2)


class ControlBinding(BaseModel):
    action: str
    keys: list[str] = Field(min_length=1)
    description: str = ""

    @field_validator("action")
    @classmethod
    def _action_ok(cls, value: str) -> str:
        if not SNAKE_CASE.match(value):
            raise ValueError(f"action '{value}' must be snake_case")
        return value

    @field_validator("keys")
    @classmethod
    def _keys_ok(cls, value: list[str]) -> list[str]:
        bad = [k for k in value if k not in ALLOWED_KEYS]
        if bad:
            raise ValueError(f"unsupported keys {bad}; allowed: {sorted(ALLOWED_KEYS)}")
        return value


class RuleCheck(BaseModel):
    rule: RuleName
    how_satisfied: str = Field(min_length=15)


class GameDesign(BaseModel):
    title: str = Field(min_length=2, max_length=60)
    genre: str = Field(min_length=3)
    camera_view: Literal["side_view_2d", "top_down_2d"]
    elevator_pitch: str = Field(min_length=20)
    core_loop: list[str] = Field(min_length=3, max_length=7)
    mechanics: list[Mechanic] = Field(min_length=2, max_length=6)
    controls: list[ControlBinding] = Field(min_length=1, max_length=10)
    win_condition: str = Field(min_length=10)
    lose_condition: str = Field(min_length=10)
    risk_reward_dilemma: str = Field(min_length=20)
    juice_plan: list[str] = Field(min_length=4)
    instant_retry: str = Field(min_length=15)
    scope_cuts: list[str] = Field(default_factory=list)
    rules_check: list[RuleCheck]

    @field_validator("controls")
    @classmethod
    def _unique_actions(cls, value: list[ControlBinding]) -> list[ControlBinding]:
        actions = [c.action for c in value]
        if len(set(actions)) != len(actions):
            raise ValueError("duplicate control actions")
        return value

    @model_validator(mode="after")
    def _all_rules_covered(self) -> "GameDesign":
        covered = sorted(r.rule for r in self.rules_check)
        expected = sorted(RuleName.__args__)  # type: ignore[attr-defined]
        if covered != expected:
            raise ValueError(f"rules_check must contain each rule exactly once: {expected}")
        return self


_ROLE = """You are the Lead Game Designer. Design ONE small, fun, finishable 2D game from the idea.
- Define a tight core loop (3-7 steps), 2-6 mechanics and the controls (snake_case action names;
  keys only from: letters A-Z, Left, Right, Up, Down, Space, Enter, Escape, Shift, Ctrl, MouseLeft).
- State a visible goal and a concrete loss condition, a real risk/reward dilemma, and a juice plan
  naming a concrete effect (camera shake, particles, sound, tween) for each key action.
- Describe how instant retry works (no loading screen, reset in place).
- Fill rules_check with exactly one entry for each of the 5 rules (rule values: clear_goal_and_loss,
  game_juice, risk_reward, difficulty_progression, instant_retry), explaining HOW this design satisfies it.
- Prefer mechanics that are easy to implement reliably in GDScript with built-in nodes. List
  anything you deliberately cut in scope_cuts."""


class GameDesignerAgent(BaseAgent):
    name = "game_designer"
    role = _ROLE
    inject_design_rules = True

    def run(self, ctx: ProjectContext) -> dict[str, Any]:
        user = f"Game idea from the user:\n{ctx.idea}\n\nProduce the complete game design."
        design = self.ask_json(ctx, user, GameDesign)
        return design.model_dump()
