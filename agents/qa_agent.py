"""QA Agent: real static checks, optional headless Godot checks, bounded auto-fix loop."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from agents.asset_manifest import build_manifest
from agents.base_agent import BaseAgent
from agents.godot_programmer import GodotProgrammerAgent
from core.schemas import ProjectContext, QAIssue, QAReport
from tools.godot_runner import run_godot_checks
from tools.static_checker import check_expected_assets, run_static_checks

logger = logging.getLogger(__name__)

FIXABLE_ROOTS = ("scripts/", "scenes/")
FIXABLE_EXTS = (".gd", ".tscn", ".tres")


@dataclass
class _Scan:
    issues: list[QAIssue]
    godot_ran: bool
    godot_complete: bool
    godot_notes: list[str] = field(default_factory=list)
    log_tail: str = ""


def _dedupe(issues: list[QAIssue]) -> list[QAIssue]:
    seen: set[tuple[str, str, str, int | None, str]] = set()
    out: list[QAIssue] = []
    for issue in issues:
        key = (issue.severity, issue.source, issue.file, issue.line, issue.message)
        if key not in seen:
            seen.add(key)
            out.append(issue)
    return out


def _render_markdown(data: dict[str, Any], final: bool) -> str:
    def rows(items: list[dict[str, Any]]) -> list[str]:
        if not items:
            return ["- (none)"]
        return [
            f"- [{i['severity']}/{i['source']}] `{i['file']}"
            + (f":{i['line']}" if i.get("line") else "") + f"` {i['message']}"
            for i in items
        ]

    lines = [
        f"# QA report{' (final)' if final else ''}", "",
        f"- Verdict: **{data['verdict']}**",
        f"- Checked by the real Godot binary: **{data['godot_checked']}**",
        f"- Auto-fix rounds used: {data['attempts_used']}", "",
        "## Notes", data["notes"], "",
        "## Unresolved errors", *rows(data["unresolved"]), "",
        "## Issues found in the first scan", *rows(data["issues"]), "",
        "## Fix history",
    ]
    for entry in data["history"]:
        lines.append(f"- round {entry['round']}: {entry['errors']} error(s), {entry['warnings']} warning(s)"
                     + (f", applied: {entry['applied']}" if entry.get("applied") else "")
                     + (f", not applied: {entry['not_applied']}" if entry.get("not_applied") else "")
                     + (f", fix error: {entry['fix_error']}" if entry.get("fix_error") else ""))
    return "\n".join(lines) + "\n"


class QAAgent(BaseAgent):
    """Quality gate. `final=True` is the re-check after the playtest-driven refinement."""

    role = "Quality gate: deterministic checks, optional real Godot run, bounded auto-fix loop."

    def __init__(self, final: bool = False, programmer: Any | None = None) -> None:
        self.final = final
        self.name = "qa_final" if final else "qa"
        self._programmer = programmer

    # ---- helpers ---------------------------------------------------------
    def _get_programmer(self) -> Any:
        if self._programmer is None:
            self._programmer = GodotProgrammerAgent()
        return self._programmer

    @staticmethod
    def _expected_assets(ctx: ProjectContext) -> list[str]:
        manifest = build_manifest(ctx.get_artifact("art"), ctx.get_artifact("audio"))
        paths = [s["res_path"] for s in manifest["sprites"]] + [s["res_path"] for s in manifest["sfx"]]
        if manifest["music"]:
            paths.append(manifest["music"]["res_path"])
        return paths

    @staticmethod
    def _fixable(issues: list[QAIssue]) -> list[QAIssue]:
        return [i for i in issues if i.severity == "error"
                and i.file.startswith(FIXABLE_ROOTS) and i.file.endswith(FIXABLE_EXTS)]

    @staticmethod
    def _entry(round_no: int, scan: _Scan) -> dict[str, Any]:
        return {
            "round": round_no,
            "errors": sum(1 for i in scan.issues if i.severity == "error"),
            "warnings": sum(1 for i in scan.issues if i.severity == "warning"),
        }

    def _scan(self, ctx: ProjectContext, expected: list[str]) -> _Scan:
        issues: list[QAIssue] = []
        try:
            issues += run_static_checks(ctx.project_dir)
        except Exception as exc:  # noqa: BLE001 - a checker bug must not kill the whole run
            logger.exception("Static checker crashed")
            issues.append(QAIssue(severity="warning", source="static", file="project",
                                  message=f"static checker crashed: {type(exc).__name__}: {exc}"))
        issues += check_expected_assets(ctx.project_dir, expected)
        godot = run_godot_checks(ctx.config.godot, ctx.project_dir)
        issues += godot.issues
        return _Scan(_dedupe(issues), godot.ran, godot.complete, godot.notes, godot.log_tail)

    # ---- main ------------------------------------------------------------
    def run(self, ctx: ProjectContext) -> dict[str, Any]:
        limit = ctx.config.qa.max_fix_attempts
        expected = self._expected_assets(ctx)
        scan = self._scan(ctx, expected)
        initial = list(scan.issues)
        history: list[dict[str, Any]] = [self._entry(0, scan)]
        attempts = 0

        while attempts < limit:
            fixable = self._fixable(scan.issues)
            if not fixable:
                break
            attempts += 1
            logger.info("[%s] fix round %d/%d for %d error(s)", self.name, attempts, limit, len(fixable))
            try:
                result = self._get_programmer().apply_fixes(ctx, [i.model_dump() for i in fixable])
            except Exception as exc:  # noqa: BLE001 - LLM/network failure must not abort the report
                logger.exception("Fix round %d failed", attempts)
                entry = self._entry(attempts, scan)
                entry["fix_error"] = f"{type(exc).__name__}: {exc}"
                history.append(entry)
                break
            scan = self._scan(ctx, expected)
            entry = self._entry(attempts, scan)
            entry["applied"] = result.get("applied", [])
            entry["not_applied"] = result.get("not_applied", [])
            history.append(entry)
            if not result.get("applied"):
                logger.warning("[%s] the programmer changed nothing; stopping the fix loop", self.name)
                break

        unresolved = [i for i in scan.issues if i.severity == "error"]
        godot_checked = scan.godot_ran and scan.godot_complete

        notes: list[str] = []
        if not scan.godot_ran:
            notes.append(
                "Godot executable was not available: ONLY static checks ran. The project was NOT opened, "
                "imported or parsed by Godot and may still contain errors."
            )
        elif not scan.godot_complete:
            notes.append("Godot ran but at least one step did not complete; the engine results are partial.")
        else:
            notes.append(
                "Godot headless --import and --check-only ran on every script. This is a parse/import check; "
                "the game was never run, so runtime errors and gameplay problems may remain."
            )
        notes.extend(scan.godot_notes)
        if unresolved:
            notes.append(f"{len(unresolved)} error(s) remain unresolved after {attempts} fix round(s).")

        if unresolved:
            verdict = "errors_remain"
        elif godot_checked:
            verdict = "passed_static_and_godot_checks"
        else:
            verdict = "passed_static_checks_only"

        report = QAReport(godot_checked=godot_checked, attempts_used=attempts, issues=initial,
                          unresolved=unresolved, notes=" ".join(notes))
        data = report.model_dump()
        data.update({
            "verdict": verdict,
            "final_warnings": [i.model_dump() for i in scan.issues if i.severity == "warning"],
            "history": history,
            "godot_log_tail": scan.log_tail,
        })
        self._write(ctx, data)
        return data

    def _write(self, ctx: ProjectContext, data: dict[str, Any]) -> None:
        ctx.reports_dir.mkdir(parents=True, exist_ok=True)
        suffix = "_final" if self.final else ""
        (ctx.reports_dir / f"qa_report{suffix}.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        (ctx.reports_dir / f"qa_report{suffix}.md").write_text(
            _render_markdown(data, self.final), encoding="utf-8")
