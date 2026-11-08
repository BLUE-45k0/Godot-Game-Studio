from pathlib import Path

from core.config import GodotConfig
from tools.godot_runner import parse_godot_output, run_godot_checks

SAMPLE = """Godot Engine vX
SCRIPT ERROR: Parse Error: Identifier "foo" not declared in the current scope.
          at: GDScript::reload (res://scripts/player.gd:12)
ERROR: Failed to load script "res://scripts/player.gd" with error "Parse error".
WARNING: ObjectDB instances leaked at exit (run with --verbose for details).
"""


def test_parse_keeps_precise_error_and_drops_noise():
    issues = parse_godot_output(SAMPLE)
    assert len(issues) == 1
    assert (issues[0].file, issues[0].line, issues[0].severity) == ("scripts/player.gd", 12, "error")


def test_parse_path_inside_message():
    issues = parse_godot_output('ERROR: Cannot open file "res://scenes/x.tscn".')
    assert issues[0].file == "scenes/x.tscn"


def test_no_executable_means_not_run(tmp_path: Path):
    result = run_godot_checks(GodotConfig(None, "", 10.0), tmp_path)
    assert result.ran is False and result.complete is False and result.issues == []
