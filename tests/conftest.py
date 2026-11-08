from pathlib import Path

import pytest

PROJECT_GODOT = """config_version=5

[application]

config/name="T"
run/main_scene="res://scenes/main.tscn"

[autoload]

Game="*res://scripts/game.gd"

[input]

jump={
"deadzone": 0.5,
"events": []
}
"""

MAIN_GD = """extends Node2D

signal scored


func _ready() -> void:
\tscored.emit()
\tif Input.is_action_just_pressed("jump"):
\t\tset_process(true)


func _on_scored() -> void:
\tset_process(false)
"""

GAME_GD = "extends Node\n\nvar score: int = 0\n"

MAIN_TSCN = """[gd_scene load_steps=2 format=3]

[ext_resource type="Script" path="res://scripts/main.gd" id="1"]

[node name="Main" type="Node2D"]
script = ExtResource("1")

[node name="Label" type="Label" parent="."]

[connection signal="scored" from="." to="." method="_on_scored"]
"""


@pytest.fixture
def make_project(tmp_path):
    """Create a small valid project and return its path."""

    def _make() -> Path:
        root = tmp_path / "proj"
        (root / "scripts").mkdir(parents=True)
        (root / "scenes").mkdir()
        (root / "project.godot").write_text(PROJECT_GODOT, encoding="utf-8")
        (root / "scripts/main.gd").write_text(MAIN_GD, encoding="utf-8")
        (root / "scripts/game.gd").write_text(GAME_GD, encoding="utf-8")
        (root / "scenes/main.tscn").write_text(MAIN_TSCN, encoding="utf-8")
        return root

    return _make
