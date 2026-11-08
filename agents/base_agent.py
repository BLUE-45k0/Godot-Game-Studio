"""Shared base class and helpers for all agents."""
from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from core.schemas import ProjectContext

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"
SNAKE_CASE = re.compile(r"^[a-z][a-z0-9_]{1,30}$")
HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
_CODE_RE = re.compile(r"```[A-Za-z0-9_+-]*[ \t]*\n(.*?)```", re.DOTALL)


class AgentError(RuntimeError):
    """Raised when an agent cannot produce a valid result."""


def load_design_rules() -> str:
    """Read the five design rules that are injected into designer prompts."""
    path = PROMPTS_DIR / "design_rules.md"
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise FileNotFoundError(f"Design rules file is missing: {path}") from exc


def artifacts_block(ctx: ProjectContext, names: list[str]) -> str:
    """Compact JSON dump of earlier stage results for use inside a prompt."""
    parts: list[str] = []
    for name in names:
        data = ctx.get_artifact(name)
        if data is not None:
            parts.append(f"### {name}\n{json.dumps(data, ensure_ascii=False)}")
    return "\n\n".join(parts)


def extract_code_block(text: str) -> str:
    """Return the first fenced code block (or the whole reply if it has no fence)."""
    match = _CODE_RE.search(text)
    if match:
        body = match.group(1).rstrip()
        if body:
            return body + "\n"
        raise ValueError("empty code block")
    stripped = text.strip()
    if stripped.startswith("```"):
        raise ValueError("unterminated code fence (reply probably truncated)")
    if not stripped:
        raise ValueError("empty reply")
    return stripped + "\n"


def _common_system(ctx: ProjectContext) -> str:
    target = ctx.config.godot.target_version
    godot_line = (
        f"Godot {target}"
        if target
        else "the latest stable Godot 4.x (use only APIs available across Godot 4.x)"
    )
    return (
        'You are one agent in "Godot Game Studio", a pipeline that builds a complete, runnable '
        "2D Godot 4 project from a short game idea.\n"
        f"- Target engine: {godot_line}.\n"
        "- Write free-text fields (descriptions, rationale) in the same language as the user's "
        "idea. Identifiers, file names, node names and code (including comments) stay in English.\n"
        "- In-game player-facing text: English unless the idea explicitly asks otherwise "
        "(Arabic text needs a font that covers Arabic glyphs; do not assume the default font does).\n"
        "- Scope: 2D only, one small game that is playable and finishable within a few minutes. "
        "Cut features rather than risk broken ones.\n"
        "- Never invent Godot APIs. If you are not sure a class, method or property exists in "
        "Godot 4, use a simpler documented alternative."
    )


class BaseAgent(ABC):
    """Base class: builds the system prompt and wraps validated JSON calls."""

    name: str = "agent"
    role: str = ""
    inject_design_rules: bool = False

    def system_prompt(self, ctx: ProjectContext) -> str:
        """Common rules + this agent's role (+ the five design rules for designers)."""
        parts = [_common_system(ctx), f"YOUR ROLE:\n{self.role}"]
        if self.inject_design_rules:
            parts.append("DESIGN RULES (mandatory, the result will be reviewed against them):\n"
                         + load_design_rules())
        return "\n\n".join(parts)

    def ask_json(
        self, ctx: ProjectContext, user: str, schema: type[T], max_tokens: int | None = None
    ) -> T:
        """Ask the model for a JSON object validated against `schema`."""
        logger.info("[%s] requesting %s", self.name, schema.__name__)
        return ctx.llm.complete_json(
            self.system_prompt(ctx), user, schema, max_tokens=max_tokens
        )

    @abstractmethod
    def run(self, ctx: ProjectContext) -> dict[str, Any]:
        """Execute the agent and return a JSON-serialisable result."""
