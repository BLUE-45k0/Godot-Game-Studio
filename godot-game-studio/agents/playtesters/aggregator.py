"""Aggregator: merges the playtest reports and picks the changes the Programmer should apply."""
from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, Field

from agents.base_agent import AgentError, BaseAgent
from agents.playtesters.base_playtester import BASIS, qa_summary
from core.schemas import ProjectContext

logger = logging.getLogger(__name__)


class AggregatorOutput(BaseModel):
    summary: str = Field(min_length=20)
    consensus_problems: list[str] = Field(default_factory=list)
    disagreements: list[str] = Field(default_factory=list)
    priority_changes: list[str] = Field(min_length=1, max_length=8)
    deferred: list[str] = Field(default_factory=list)


_ROLE = """You are the Review Aggregator. You receive reports from several simulated playtesters
(none of them actually played the game) plus the QA status, and you decide what the Programmer
should change next.
- Merge duplicates. Rank by how many personas raised an issue and by severity (crashes and
  softlocks first, then clarity/onboarding, then balance, then polish).
- priority_changes: at most 8 concrete, ordered instructions. Each one names the file(s) from the
  FILE LIST, says exactly what to change (values, logic, UI text) and can be done by editing the
  existing scripts/scenes without new binary assets or new files.
- When personas disagree, record it in disagreements and choose the option that best satisfies the
  five design rules and keeps the game small. Never add contradictory changes.
- Anything that needs new assets, new systems or large scope goes into deferred.
- Write in the language of the game idea."""


class AggregatorAgent(BaseAgent):
    name = "aggregator"
    role = _ROLE
    inject_design_rules = True

    def run(self, ctx: ProjectContext) -> dict[str, Any]:
        reports: dict[str, Any] = {}
        missing: list[str] = []
        for key in ctx.config.playtesters.enabled:
            data = ctx.get_artifact(f"playtest_{key}")
            if data is None:
                missing.append(key)
            else:
                reports[key] = data
        if not reports:
            raise AgentError("no playtester report is available to aggregate")

        scores = {key: int(data["score"]) for key, data in reports.items()}
        average = round(sum(scores.values()) / len(scores), 2)

        files: list[str] = []
        for top in ("scripts", "scenes"):
            base = ctx.project_dir / top
            if base.is_dir():
                files += [p.relative_to(ctx.project_dir).as_posix() for p in sorted(base.rglob("*"))
                          if p.is_file() and p.suffix in (".gd", ".tscn", ".tres")]

        user = (
            f"GAME IDEA:\n{ctx.idea}\n\n"
            f"FILE LIST:\n{json.dumps(files)}\n\n"
            f"QA STATUS:\n{qa_summary(ctx)}\n\n"
            f"PLAYTEST REPORTS (json):\n{json.dumps(reports, ensure_ascii=False)}\n\n"
            "Produce the aggregated result."
        )
        output = self.ask_json(ctx, user, AggregatorOutput, max_tokens=4000)

        data = output.model_dump()
        data.update({"average_score": average, "scores": scores, "missing_personas": missing, "basis": BASIS})
        ctx.reports_dir.mkdir(parents=True, exist_ok=True)
        (ctx.reports_dir / "playtest_summary.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return data
