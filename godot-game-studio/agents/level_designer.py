"""Difficulty & Level Designer: Easy/Normal/Hard balance values and level progression."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from agents.base_agent import SNAKE_CASE, BaseAgent, artifacts_block
from core.schemas import ProjectContext


class BalanceParam(BaseModel):
    name: str
    unit: str = Field(min_length=1)
    higher_is_harder: bool
    easy: float
    normal: float
    hard: float
    rationale: str = Field(min_length=5)

    @field_validator("name")
    @classmethod
    def _name_ok(cls, value: str) -> str:
        if not SNAKE_CASE.match(value):
            raise ValueError(f"param name '{value}' must be snake_case")
        return value

    @model_validator(mode="after")
    def _ordered(self) -> "BalanceParam":
        if self.higher_is_harder:
            ok = self.easy < self.normal < self.hard
        else:
            ok = self.easy > self.normal > self.hard
        if not ok:
            direction = "increase" if self.higher_is_harder else "decrease"
            raise ValueError(f"'{self.name}' must strictly {direction} from easy to normal to hard")
        return self


class LevelSpec(BaseModel):
    id: int = Field(ge=1)
    name: str = Field(min_length=2)
    goal: str = Field(min_length=5)
    duration_sec: int = Field(ge=10, le=600)
    intensity: int = Field(ge=1, le=10)
    new_element: str = Field(min_length=2)


class LevelPlan(BaseModel):
    starting_difficulty: Literal["easy", "normal", "hard"] = "normal"
    first_challenge_within_sec: float = Field(ge=0, le=6)
    respawn_delay_sec: float = Field(ge=0, le=2.5)
    no_loading_screens: bool
    balance_params: list[BalanceParam] = Field(min_length=3, max_length=12)
    levels: list[LevelSpec] = Field(min_length=3, max_length=12)
    notes: str = ""

    @model_validator(mode="after")
    def _progression(self) -> "LevelPlan":
        if not self.no_loading_screens:
            raise ValueError("no_loading_screens must be true (rule 5)")
        ids = [lvl.id for lvl in self.levels]
        if ids != list(range(1, len(ids) + 1)):
            raise ValueError("level ids must be 1..N in order")
        levels = self.levels
        if levels[-1].intensity <= levels[0].intensity:
            raise ValueError("last level must be more intense than the first (rule 4)")
        for prev, cur in zip(levels, levels[1:]):
            if prev.intensity - cur.intensity > 1:
                raise ValueError(
                    f"intensity drops by more than 1 between levels {prev.id} and {cur.id}"
                )
        names = [p.name for p in self.balance_params]
        if len(set(names)) != len(names):
            raise ValueError("duplicate balance param names")
        return self


_ROLE = """You are the Difficulty & Level Designer. From the game design, produce:
- balance_params: 3-12 numeric values the programmer can read directly (enemy speed, damage,
  spawn interval, player health, reward multiplier, ...). Each has easy/normal/hard values that
  strictly move in the harder direction; set higher_is_harder correctly and give units.
- levels: a short progression (3-12) with rising intensity (1-10). Intensity may dip by at most 1
  between neighbours (a breather) and the last level must beat the first. Each level introduces
  one new element.
- first_challenge_within_sec (<= 6): how fast the first real challenge appears.
- respawn_delay_sec (<= 2.5) and no_loading_screens = true (instant retry)."""


class LevelDesignerAgent(BaseAgent):
    name = "level_designer"
    role = _ROLE
    inject_design_rules = True

    def run(self, ctx: ProjectContext) -> dict[str, Any]:
        user = (
            f"Game idea:\n{ctx.idea}\n\nGame design:\n{artifacts_block(ctx, ['game_designer'])}\n\n"
            "Produce the difficulty presets and level plan."
        )
        plan = self.ask_json(ctx, user, LevelPlan)
        return plan.model_dump()
