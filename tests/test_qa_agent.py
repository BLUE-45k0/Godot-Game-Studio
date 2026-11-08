import json

from agents.qa_agent import QAAgent
from core.config import load_config
from core.schemas import ProjectContext

GOOD_MAIN = (
    "extends Node2D\n\nsignal scored\n\n\nfunc _ready() -> void:\n\tscored.emit()\n\n\n"
    "func _on_scored() -> void:\n\tset_process(false)\n"
)
BROKEN_MAIN = "extends Node2D\n\nsignal scored\n\nonready var label = 1\n"


class FakeProgrammer:
    def __init__(self, fixes: bool = True) -> None:
        self.calls: list[list[dict]] = []
        self.fixes = fixes

    def apply_fixes(self, ctx, issues):
        self.calls.append(issues)
        if self.fixes:
            (ctx.project_dir / "scripts/main.gd").write_text(GOOD_MAIN, encoding="utf-8")
            return {"applied": ["scripts/main.gd"], "not_applied": []}
        return {"applied": [], "not_applied": [{"path": "scripts/main.gd", "reason": "nope"}]}


def _ctx(tmp_path, project):
    cfg_file = tmp_path / "c.toml"
    cfg_file.write_text("[qa]\nmax_fix_attempts = 3\n", encoding="utf-8")
    config = load_config(cfg_file, use_dotenv=False, require_llm=False)
    return ProjectContext("idea", project, config, llm=None)  # type: ignore[arg-type]


def test_fix_loop_resolves_errors(tmp_path, make_project):
    project = make_project()
    (project / "scripts/main.gd").write_text(BROKEN_MAIN, encoding="utf-8")
    ctx = _ctx(tmp_path, project)
    programmer = FakeProgrammer()

    data = QAAgent(programmer=programmer).run(ctx)

    assert data["attempts_used"] == 1 and data["unresolved"] == []
    assert data["verdict"] == "passed_static_checks_only" and data["godot_checked"] is False
    assert "ONLY static checks" in data["notes"]
    assert any(i["severity"] == "error" for i in data["issues"])
    assert json.loads((ctx.reports_dir / "qa_report.json").read_text(encoding="utf-8"))["verdict"] == data["verdict"]
    assert (ctx.reports_dir / "qa_report.md").is_file()


def test_unresolved_errors_are_reported(tmp_path, make_project):
    project = make_project()
    (project / "scripts/main.gd").write_text(BROKEN_MAIN, encoding="utf-8")
    ctx = _ctx(tmp_path, project)

    data = QAAgent(programmer=FakeProgrammer(fixes=False)).run(ctx)

    assert data["verdict"] == "errors_remain" and data["unresolved"]
    assert data["attempts_used"] == 1  # stops when nothing was applied


def test_clean_project_needs_no_fix(tmp_path, make_project):
    ctx = _ctx(tmp_path, make_project())
    programmer = FakeProgrammer()
    data = QAAgent(programmer=programmer).run(ctx)
    assert programmer.calls == [] and data["attempts_used"] == 0
