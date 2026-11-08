from tools.static_checker import check_expected_assets, run_static_checks


def _errors(project):
    return [i for i in run_static_checks(project) if i.severity == "error"]


def test_valid_project_has_no_errors(make_project):
    assert _errors(make_project()) == []


def test_missing_script_reference(make_project):
    project = make_project()
    (project / "scripts/game.gd").write_text(
        'extends Node\n\nfunc _ready() -> void:\n\tvar s := preload("res://scripts/nope.gd")\n', encoding="utf-8")
    assert any("missing file" in i.message for i in _errors(project))


def test_undeclared_ext_resource(make_project):
    project = make_project()
    scene = project / "scenes/main.tscn"
    scene.write_text(scene.read_text(encoding="utf-8").replace('ExtResource("1")', 'ExtResource("9")'),
                     encoding="utf-8")
    assert any("never declared" in i.message for i in _errors(project))


def test_load_steps_mismatch_is_warning(make_project):
    project = make_project()
    scene = project / "scenes/main.tscn"
    scene.write_text(scene.read_text(encoding="utf-8").replace("load_steps=2", "load_steps=7"), encoding="utf-8")
    issues = run_static_checks(project)
    assert any("load_steps" in i.message and i.severity == "warning" for i in issues)
    assert _errors(project) == []


def test_undefined_input_action(make_project):
    project = make_project()
    script = project / "scripts/game.gd"
    script.write_text('extends Node\n\nfunc _process(_d: float) -> void:\n\tif Input.is_action_pressed("shoot"):\n\t\tpass\n',
                      encoding="utf-8")
    assert any("not defined in project.godot" in i.message for i in _errors(project))


def test_connection_method_missing(make_project):
    project = make_project()
    script = project / "scripts/main.gd"
    script.write_text(script.read_text(encoding="utf-8").replace("_on_scored", "_other"), encoding="utf-8")
    assert any("is not defined in" in i.message for i in _errors(project))


def test_mixed_indentation(make_project):
    project = make_project()
    (project / "scripts/game.gd").write_text(
        "extends Node\n\nfunc a() -> void:\n\tvar x := 1\n\nfunc b() -> void:\n    var y := 2\n", encoding="utf-8")
    assert any("mixed tab and space" in i.message for i in _errors(project))


def test_godot3_syntax_is_error(make_project):
    project = make_project()
    (project / "scripts/game.gd").write_text("extends Node\n\nonready var a = 1\n", encoding="utf-8")
    assert any("@onready" in i.message for i in _errors(project))


def test_signal_never_emitted_warning(make_project):
    project = make_project()
    (project / "scripts/game.gd").write_text("extends Node\n\nsignal lonely\n", encoding="utf-8")
    issues = run_static_checks(project)
    assert any("never emitted" in i.message and i.severity == "warning" for i in issues)


def test_missing_main_scene(make_project):
    project = make_project()
    (project / "scenes/main.tscn").unlink()
    assert any(i.file == "project.godot" for i in _errors(project))


def test_expected_assets(tmp_path):
    (tmp_path / "assets/sprites").mkdir(parents=True)
    (tmp_path / "assets/sprites/a.png").write_bytes(b"x")
    issues = check_expected_assets(tmp_path, ["res://assets/sprites/a.png", "res://assets/sprites/b.png"])
    assert [i.file for i in issues] == ["assets/sprites/b.png"]
