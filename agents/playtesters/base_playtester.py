"""Shared machinery for the five playtester personas."""
from __future__ import annotations

import json
import logging
from typing import Any

from agents.base_agent import BaseAgent, artifacts_block
from core.schemas import PlaytestReport, ProjectContext

logger = logging.getLogger(__name__)

BASIS = (
    "Simulated review of the game design and source code by an AI persona. Nobody actually played "
    "this build; treat scores as informed opinions, not measurements."
)

_HONESTY = """REVIEW RULES
- You cannot run or see the game. Base every statement on the design, level plan, QA status and
  source code you are given. Never claim you saw graphics, heard sounds or felt the controls.
- Cite file names, function names or numbers when you can. If something cannot be judged from the
  material, say that it is unverifiable instead of guessing.
- persona: use your persona key. score: integer 1-10 (1 = unplayable, 5 = playable but flawed,
  8 = good, 10 = exceptional). top_3_problems and top_3_strengths: 1-3 short concrete items, most
  important first. fix_suggestions: concrete changes that can be made by editing the existing
  scripts/scenes (name the file), most valuable first. No changes that need new binary assets."""


def project_digest(ctx: ProjectContext, max_total: int = 60_000, max_file: int = 7_000) -> str:
    """Concatenate the project's scripts/scenes (truncated) for the reviewer."""
    parts: list[str] = []
    used = 0
    for top in ("scripts", "scenes"):
        base = ctx.project_dir / top
        if not base.is_dir():
            continue
        for item in sorted(base.rglob("*")):
            if not item.is_file() or item.suffix not in (".gd", ".tscn", ".tres"):
                continue
            text = item.read_text(encoding="utf-8", errors="replace")
            if len(text) > max_file:
                text = text[:max_file] + "\n... [truncated]\n"
            block = f"### {item.relative_to(ctx.project_dir).as_posix()}\n{text}"
            if used + len(block) > max_total:
                parts.append("### (remaining files omitted: size limit)")
                return "\n\n".join(parts)
            parts.append(block)
            used += len(block)
    return "\n\n".join(parts) or "(no source files found)"


def assets_listing(ctx: ProjectContext, limit: int = 60) -> str:
    """Names of generated asset files (sprites and audio only)."""
    names: list[str] = []
    for sub in ("sprites", "audio"):
        base = ctx.project_dir / "assets" / sub
        if base.is_dir():
            names += [p.relative_to(ctx.project_dir).as_posix() for p in sorted(base.iterdir()) if p.is_file()]
    return "\n".join(names[:limit]) or "(no generated assets)"


def qa_summary(ctx: ProjectContext) -> str:
    """Compact QA status for reviewers."""
    qa = ctx.get_artifact("qa") or {}
    unresolved = qa.get("unresolved", []) if isinstance(qa, dict) else []
    return json.dumps({
        "verdict": qa.get("verdict", "unknown") if isinstance(qa, dict) else "unknown",
        "godot_checked": qa.get("godot_checked") if isinstance(qa, dict) else None,
        "unresolved_errors": [f"{i.get('file')}: {i.get('message')}" for i in unresolved[:10]],
    }, ensure_ascii=False)


class PlaytesterAgent(BaseAgent):
    """Base class: subclasses set `persona_key`, `name`, `role` and optionally `task`/`schema`."""

    persona_key: str = ""
    schema: type[PlaytestReport] = PlaytestReport
    task: str = "Review this game from your persona's point of view."

    def system_prompt(self, ctx: ProjectContext) -> str:
        return super().system_prompt(ctx) + "\n\n" + _HONESTY

    def _user_prompt(self, ctx: ProjectContext) -> str:
        return (
            f"GAME IDEA:\n{ctx.idea}\n\n"
            f"DESIGN AND LEVEL PLAN:\n{artifacts_block(ctx, ['game_designer', 'level_designer'])}\n\n"
            f"QA STATUS:\n{qa_summary(ctx)}\n\n"
            f"ASSET FILES:\n{assets_listing(ctx)}\n\n"
            f"SOURCE FILES:\n{project_digest(ctx)}\n\n"
            f"TASK:\n{self.task}\nReturn the report JSON."
        )

    def run(self, ctx: ProjectContext) -> dict[str, Any]:
        report = self.ask_json(ctx, self._user_prompt(ctx), self.schema, max_tokens=4000)
        data = report.model_dump()
        data["persona"] = self.persona_key
        data["basis"] = BASIS
        ctx.reports_dir.mkdir(parents=True, exist_ok=True)
        (ctx.reports_dir / f"playtest_{self.persona_key}.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return data
