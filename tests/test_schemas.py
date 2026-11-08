import pytest
from pydantic import ValidationError

from core.safe_files import UnsafePathError, clean_rel_path, safe_write, validate_project_path
from core.schemas import PlaytestReport, QAIssue


def _report(**over):
    data = {"persona": "casual", "score": 7, "top_3_problems": ["a"], "top_3_strengths": ["b"],
            "fix_suggestions": ["c"]}
    data.update(over)
    return data


def test_playtest_report_valid_and_bounds():
    assert PlaytestReport.model_validate(_report()).score == 7
    for bad in ({"score": 0}, {"score": 11}, {"top_3_problems": []}, {"top_3_problems": list("abcd")}):
        with pytest.raises(ValidationError):
            PlaytestReport.model_validate(_report(**bad))


def test_qa_issue_literals():
    with pytest.raises(ValidationError):
        QAIssue(severity="fatal", source="static", file="x", message="m")


def test_safe_paths(tmp_path):
    assert clean_rel_path("res://scripts/a.gd") == "scripts/a.gd"
    for bad in ("../x.gd", "/etc/passwd", "a\\b.gd", ""):
        with pytest.raises(UnsafePathError):
            clean_rel_path(bad)
    with pytest.raises(UnsafePathError):
        validate_project_path("assets/a.gd", ("scripts",), (".gd",))
    with pytest.raises(UnsafePathError):
        validate_project_path("scripts/a.exe", ("scripts",), (".gd",))
    target = safe_write(tmp_path, "scripts/a.gd", "extends Node\r\n", (".gd",))
    assert target.read_text(encoding="utf-8") == "extends Node\n"
    with pytest.raises(UnsafePathError):
        safe_write(tmp_path, "scripts/a.txt", "x", (".gd",))
