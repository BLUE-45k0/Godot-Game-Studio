import json
import re

import pytest
from pydantic import ValidationError

from agents.base_agent import extract_code_block
from agents.godot_programmer import (
    BuildPlan,
    GodotProgrammerAgent,
    normalize_indent,
    validate_gd,
    validate_tscn,
)
from agents.level_designer import LevelPlan
from core.config import load_config
from core.schemas import ProjectContext


def _level_plan(**overrides):
    data = {
        "first_challenge_within_sec": 3,
        "respawn_delay_sec": 1.0,
        "no_loading_screens": True,
        "balance_params": [
            {"name": "enemy_speed", "unit": "px/s", "higher_is_harder": True,
             "easy": 80, "normal": 120, "hard": 170, "rationale": "faster is harder"},
            {"name": "player_health", "unit": "hp", "higher_is_harder": False,
             "easy": 5, "normal": 3, "hard": 1, "rationale": "less health is harder"},
            {"name": "spawn_interval", "unit": "s", "higher_is_harder": False,
             "easy": 3.0, "normal": 2.0, "hard": 1.0, "rationale": "shorter is harder"},
        ],
        "levels": [
            {"id": 1, "name": "A", "goal": "reach the exit", "duration_sec": 60, "intensity": 2, "new_element": "jump"},
            {"id": 2, "name": "B", "goal": "reach the exit", "duration_sec": 60, "intensity": 4, "new_element": "enemy"},
            {"id": 3, "name": "C", "goal": "reach the exit", "duration_sec": 60, "intensity": 7, "new_element": "spikes"},
        ],
    }
    data.update(overrides)
    return data


def test_level_plan_valid():
    assert LevelPlan.model_validate(_level_plan()).levels[-1].intensity == 7


def test_level_plan_rejects_wrong_balance_order():
    data = _level_plan()
    data["balance_params"][0].update(easy=170, hard=80)
    with pytest.raises(ValidationError):
        LevelPlan.model_validate(data)


def test_level_plan_rejects_big_intensity_drop():
    data = _level_plan()
    data["levels"][1]["intensity"] = 9
    data["levels"][2]["intensity"] = 5
    with pytest.raises(ValidationError):
        LevelPlan.model_validate(data)


def test_extract_code_block():
    assert extract_code_block("x\n```gdscript\nextends Node\n```\ny") == "extends Node\n"
    with pytest.raises(ValueError):
        extract_code_block("```gdscript\nextends Node")


def test_validate_gd_flags_godot3_and_missing_types():
    code = 'extends Node\nonready var a = 1\nfunc _ready():\n\tyield(get_tree(), "idle_frame")\n'
    problems = validate_gd(code)
    assert any("@onready" in p for p in problems)
    assert any("await" in p for p in problems)
    assert any("return type" in p for p in problems)


def test_validate_gd_accepts_clean_code():
    assert validate_gd("extends Node\n\nfunc _ready() -> void:\n\tset_process(true)\n") == []


def test_normalize_indent_fixes_mixed():
    mixed = "extends Node\nfunc a() -> void:\n\tvar x := 1\n    var y := 2\n"
    assert "    " not in normalize_indent(mixed)


def test_validate_tscn_fixes_load_steps_and_finds_missing_ids():
    code = ('[gd_scene load_steps=9 format=3]\n\n'
            '[ext_resource type="Script" path="res://scripts/main.gd" id="1"]\n\n'
            '[node name="Main" type="Node2D"]\nscript = ExtResource("1")\n')
    fixed, problems = validate_tscn(code)
    assert problems == []
    assert fixed.startswith("[gd_scene load_steps=2 format=3]")
    _, problems = validate_tscn(code.replace('ExtResource("1")', 'ExtResource("9")'))
    assert any("never declared" in p for p in problems)


def test_build_plan_rejects_path_escape():
    plan = {
        "files": [
            {"path": "scripts/../evil.gd", "purpose": "bad file"},
            {"path": "scripts/a.gd", "purpose": "script a"},
            {"path": "scenes/main.tscn", "purpose": "main scene"},
        ],
        "main_scene": "scenes/main.tscn",
        "input_actions": [{"name": "jump", "keys": ["Space"]}],
    }
    with pytest.raises(ValidationError):
        BuildPlan.model_validate(plan)


_PLAN = {
    "files": [
        {"path": "scripts/main.gd", "purpose": "main game controller"},
        {"path": "scripts/player.gd", "purpose": "player movement"},
        {"path": "scenes/main.tscn", "purpose": "main scene", "depends_on": ["scripts/main.gd"]},
    ],
    "main_scene": "scenes/main.tscn",
    "input_actions": [{"name": "jump", "keys": ["Space"]}],
}

_FILES = {
    "scripts/main.gd": "extends Node2D\n\nfunc _ready() -> void:\n\tset_process(true)\n",
    "scripts/player.gd": "extends CharacterBody2D\n\nfunc _physics_process(_delta: float) -> void:\n\tmove_and_slide()\n",
    "scenes/main.tscn": ('[gd_scene load_steps=5 format=3]\n\n'
                         '[ext_resource type="Script" path="res://scripts/main.gd" id="1"]\n\n'
                         '[node name="Main" type="Node2D"]\nscript = ExtResource("1")\n'),
}


class FakeLLM:
    last_stop_reason = "end_turn"

    def complete_json(self, system, user, schema, **kwargs):
        return schema.model_validate(_PLAN)

    def complete(self, system, user, max_tokens=None):
        target = re.search(r"TARGET FILE: (\S+)", user).group(1)
        return f"```text\n{_FILES[target]}```"


def test_programmer_build_with_fake_llm(tmp_path):
    cfg_file = tmp_path / "c.toml"
    cfg_file.write_text('[godot]\ntarget_version = "4.3"\n', encoding="utf-8")
    config = load_config(cfg_file, use_dotenv=False, require_llm=False)
    ctx = ProjectContext("test idea", tmp_path / "proj", config, FakeLLM())
    ctx.set_artifact("game_designer", {
        "controls": [{"action": "move_left", "keys": ["A", "Left"], "description": "left"}]
    })

    result = GodotProgrammerAgent().run(ctx)

    assert result["files"] == sorted(_FILES)
    assert result["warnings"] == []
    assert result["godot_version"] == "4.3"
    assert "project.godot" in result["project_files"]
    names = {a["name"] for a in result["project_settings"]["input_actions"]}
    assert {"jump", "move_left"} <= names
    scene = (ctx.project_dir / "scenes/main.tscn").read_text(encoding="utf-8")
    assert scene.startswith("[gd_scene load_steps=2 format=3]")
    assert (ctx.project_dir / "project.godot").is_file()
    json.dumps(result)  # result must be JSON-serialisable
