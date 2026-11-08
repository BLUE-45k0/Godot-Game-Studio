"""Visual & Art Agent: palette and procedural sprite specifications + real asset generation."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from agents.base_agent import HEX_COLOR, SNAKE_CASE, BaseAgent, artifacts_block
from core.schemas import ProjectContext
from tools.asset_pipeline import build_art_assets

ColorRole = Literal[
    "background", "player", "enemy", "hazard", "pickup", "platform", "ui_text", "accent"
]
SpriteCategory = Literal[
    "player", "enemy", "pickup", "hazard", "platform", "background", "ui", "particle"
]
Shape = Literal[
    "circle", "rounded_rect", "triangle", "diamond", "star", "blob", "humanoid_blocky"
]


def _channel(value: int) -> float:
    s = value / 255
    return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4


def relative_luminance(hex_color: str) -> float:
    """WCAG relative luminance of '#rrggbb'."""
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def contrast_ratio(a: str, b: str) -> float:
    """WCAG contrast ratio between two '#rrggbb' colors."""
    hi, lo = sorted((relative_luminance(a), relative_luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


class PaletteColor(BaseModel):
    name: str = Field(min_length=2)
    hex: str
    role: ColorRole

    @field_validator("hex")
    @classmethod
    def _hex_ok(cls, value: str) -> str:
        if not HEX_COLOR.match(value):
            raise ValueError(f"'{value}' must look like #RRGGBB")
        return value.lower()


class SpriteSpec(BaseModel):
    name: str
    category: SpriteCategory
    width: int = Field(ge=8, le=256)
    height: int = Field(ge=8, le=256)
    style: Literal["pixel", "flat", "outline"]
    shape: Shape
    base_color: str
    accent_color: str
    frames: int = Field(default=1, ge=1, le=8)
    animation: Literal["none", "idle_bob", "walk", "spin", "pulse"] = "none"
    description: str = Field(min_length=5)

    @field_validator("name")
    @classmethod
    def _name_ok(cls, value: str) -> str:
        if not SNAKE_CASE.match(value):
            raise ValueError(f"sprite name '{value}' must be snake_case")
        return value

    @field_validator("base_color", "accent_color")
    @classmethod
    def _color_ok(cls, value: str) -> str:
        if not HEX_COLOR.match(value):
            raise ValueError(f"'{value}' must look like #RRGGBB")
        return value.lower()


class ArtDesign(BaseModel):
    style_summary: str = Field(min_length=15)
    clear_color: str
    palette: list[PaletteColor] = Field(min_length=5, max_length=10)
    sprites: list[SpriteSpec] = Field(min_length=4, max_length=16)
    kenney_search_terms: list[str] = Field(default_factory=list, max_length=5)

    @field_validator("clear_color")
    @classmethod
    def _clear_ok(cls, value: str) -> str:
        if not HEX_COLOR.match(value):
            raise ValueError("clear_color must look like #RRGGBB")
        return value.lower()

    @model_validator(mode="after")
    def _consistent(self) -> "ArtDesign":
        roles = {c.role for c in self.palette}
        if not {"background", "player"} <= roles or not roles & {"enemy", "hazard"}:
            raise ValueError("palette needs roles: background, player, and enemy or hazard")
        backgrounds = [c.hex for c in self.palette if c.role == "background"]
        players = [c.hex for c in self.palette if c.role == "player"]
        for p in players:
            for b in backgrounds:
                if contrast_ratio(p, b) < 3.0:
                    raise ValueError(f"player color {p} has contrast < 3 against background {b}")
        names = [s.name for s in self.sprites]
        if len(set(names)) != len(names):
            raise ValueError("duplicate sprite names")
        cats = {s.category for s in self.sprites}
        if not {"player", "background"} <= cats or not cats & {"enemy", "hazard"}:
            raise ValueError("sprites need categories: player, background, and enemy or hazard")
        allowed = {c.hex for c in self.palette}
        for s in self.sprites:
            if s.base_color not in allowed or s.accent_color not in allowed:
                raise ValueError(f"sprite '{s.name}' uses colors outside the palette")
        return self


_ROLE = """You are the Visual & Art Agent. All art is generated procedurally from simple shapes, so
design for that: bold silhouettes, few colors, readable at small sizes.
- Palette: 5-10 colors, each with a role. The player color must contrast with the background
  (contrast ratio >= 3) and must be clearly different from enemy/hazard colors.
- clear_color: the game's background color (#RRGGBB).
- Sprites: 4-16 specs (snake_case names). Required categories: player, background, and at least one
  of enemy/hazard; add pickup and platform when the game needs them. base_color and accent_color
  MUST be hex values taken from the palette. An animated sprite is a horizontal strip of `frames`
  frames; keep frames <= 8. Sizes are per frame.
- kenney_search_terms: up to 5 short keywords for finding matching CC0 asset packs (optional)."""


class ArtAgent(BaseAgent):
    name = "art"
    role = _ROLE

    def run(self, ctx: ProjectContext) -> dict[str, Any]:
        user = (
            f"Game idea:\n{ctx.idea}\n\n"
            f"{artifacts_block(ctx, ['game_designer', 'level_designer'])}\n\n"
            "Produce the art direction, palette and sprite specifications."
        )
        art = self.ask_json(ctx, user, ArtDesign)
        result = art.model_dump()
        result["assets"] = build_art_assets(ctx, result["sprites"])
        return result
