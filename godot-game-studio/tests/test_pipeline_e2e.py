"""Full pipeline with a scripted fake LLM: every stage runs for real, only the model is fake."""
import json
import re

from core.config import load_config
from core.orchestrator import Orchestrator

MAIN_GD = """extends Node2D

signal scored

var score: int = 0


func _ready() -> void:
\tscored.connect(_on_scored)


func _process(_delta: float) -> void:
\tif Input.is_action_just_pressed("jump"):
\t\tscore += 1
\t\tscored.emit()


func _on_scored() -> void:
\tqueue_redraw()
"""

PLAYER_GD = """extends CharacterBody2D

const SPEED: float = 200.0


func _physics_process(_delta: float) -> void:
\tvar direction: float = Input.get_axis("move_left", "move_right")
\tvelocity.x = direction * SPEED
\tmove_and_slide()
"""

MAIN_TSCN = """[gd_scene load_steps=9 format=3]

[ext_resource type="Script" path="res://scripts/main.gd" id="1"]
[ext_resource type="Script" path="res://scripts/player.gd" id="2"]

[node name="Main" type="Node2D"]
script = ExtResource("1")

[node name="Player" type="CharacterBody2D" parent="."]
script = ExtResource("2")
"""

FILES = {"scripts/main.gd": MAIN_GD, "scripts/player.gd": PLAYER_GD, "scenes/main.tscn": MAIN_TSCN}

RULES = ["clear_goal_and_loss", "game_juice", "risk_reward", "difficulty_progression", "instant_retry"]


def _sfx(name: str, wave: str, f0: float, f1: float) -> dict:
    return {"name": name, "event": f"{name} event", "waveform": wave, "start_freq_hz": f0,
            "end_freq_hz": f1, "duration_sec": 0.15, "volume": 0.5}


def _sprite(name, category, shape, base, accent, frames=1, anim="none", w=32, h=32, style="flat"):
    return {"name": name, "category": category, "width": w, "height": h, "style": style, "shape": shape,
            "base_color": base, "accent_color": accent, "frames": frames, "animation": anim,
            "description": f"{name} sprite"}


PAYLOADS = {
    "GameDesign": {
        "title": "Space Cat", "genre": "platformer", "camera_view": "side_view_2d",
        "elevator_pitch": "A cat floats through space collecting stars.",
        "core_loop": ["move", "jump", "collect star", "avoid hazard"],
        "mechanics": [
            {"name": "jump", "description": "Jump over gaps and hazards", "player_input": "Space"},
            {"name": "collect", "description": "Touch stars to score points", "player_input": "move"},
        ],
        "controls": [
            {"action": "move_left", "keys": ["A", "Left"], "description": "left"},
            {"action": "move_right", "keys": ["D", "Right"], "description": "right"},
            {"action": "jump", "keys": ["Space"], "description": "jump"},
        ],
        "win_condition": "Collect ten stars to win.", "lose_condition": "Touch a hazard and lose a life.",
        "risk_reward_dilemma": "Golden stars sit next to hazards and pay triple.",
        "juice_plan": ["shake on hit", "particles on pickup", "sound on jump", "squash on land"],
        "instant_retry": "Press R to reset state in place without loading.",
        "rules_check": [{"rule": r, "how_satisfied": f"{r} is satisfied by the design above"} for r in RULES],
    },
    "LevelPlan": {
        "first_challenge_within_sec": 3, "respawn_delay_sec": 1.0, "no_loading_screens": True,
        "balance_params": [
            {"name": "enemy_speed", "unit": "px/s", "higher_is_harder": True, "easy": 80, "normal": 120,
             "hard": 170, "rationale": "faster is harder"},
            {"name": "player_health", "unit": "hp", "higher_is_harder": False, "easy": 5, "normal": 3,
             "hard": 1, "rationale": "less health is harder"},
            {"name": "spawn_interval", "unit": "s", "higher_is_harder": False, "easy": 3.0, "normal": 2.0,
             "hard": 1.0, "rationale": "shorter is harder"},
        ],
        "levels": [
            {"id": 1, "name": "Dock", "goal": "reach the exit", "duration_sec": 60, "intensity": 2, "new_element": "jump"},
            {"id": 2, "name": "Belt", "goal": "reach the exit", "duration_sec": 60, "intensity": 4, "new_element": "enemy"},
            {"id": 3, "name": "Core", "goal": "reach the exit", "duration_sec": 60, "intensity": 7, "new_element": "spikes"},
        ],
    },
    "ArtDesign": {
        "style_summary": "Bold flat shapes on a dark space background.",
        "clear_color": "#101820",
        "palette": [
            {"name": "space", "hex": "#101820", "role": "background"},
            {"name": "cat", "hex": "#ffd166", "role": "player"},
            {"name": "alien", "hex": "#ef476f", "role": "enemy"},
            {"name": "rock", "hex": "#118ab2", "role": "platform"},
            {"name": "snow", "hex": "#ffffff", "role": "ui_text"},
            {"name": "mint", "hex": "#06d6a0", "role": "accent"},
        ],
        "sprites": [
            _sprite("hero", "player", "humanoid_blocky", "#ffd166", "#06d6a0", 4, "walk"),
            _sprite("blob_enemy", "enemy", "blob", "#ef476f", "#ffffff", 2, "idle_bob"),
            _sprite("ground", "platform", "rounded_rect", "#118ab2", "#06d6a0", w=64, h=16),
            _sprite("sky_bg", "background", "circle", "#101820", "#ffffff", w=128, h=72),
        ],
    },
    "AudioDesign": {
        "sonic_identity": "Bright chiptune bleeps over a calm triangle bass.",
        "sfx": [_sfx("jump", "square", 300, 700), _sfx("hit", "noise", 400, 100), _sfx("pickup", "sine", 600, 1200),
                _sfx("death", "saw", 400, 80), _sfx("win", "triangle", 400, 900)],
        "music": {"mood": "bright", "bpm": 120, "scale": "major", "root_note": "C", "loop_bars": 4,
                  "lead_waveform": "square", "bass_waveform": "triangle", "volume": 0.5},
    },
    "BuildPlan": {
        "files": [
            {"path": "scripts/main.gd", "purpose": "main game controller"},
            {"path": "scripts/player.gd", "purpose": "player movement"},
            {"path": "scenes/main.tscn", "purpose": "main scene", "depends_on": ["scripts/main.gd", "scripts/player.gd"]},
        ],
        "main_scene": "scenes/main.tscn",
        "input_actions": [{"name": "jump", "keys": ["Space"]}],
    },
    "RefinePlan": {"edits": [{"path": "scripts/main.gd", "instructions": "spawn the first challenge after 2 seconds"}]},
    "PlaytestReport": {"persona": "x", "score": 6, "top_3_problems": ["slow start"],
                       "top_3_strengths": ["clear goal"], "fix_suggestions": ["scripts/main.gd: shorten the intro"]},
    "AggregatorOutput": {"summary": "All reviewers want a faster first challenge.",
                         "priority_changes": ["scripts/main.gd: spawn the first challenge after 2 seconds"]},
}
PAYLOADS["ChaosReport"] = {**PAYLOADS["PlaytestReport"], "edge_cases": [
    {"scenario": f"scenario number {i} breaks", "input_or_value": "0", "expected_failure": "crash",
     "target_file": "scripts/main.gd", "severity": "high"} for i in range(5)]}


class ScriptedLLM:
    last_stop_reason = "end_turn"

    def complete_json(self, system, user, schema, **kwargs):
        return schema.model_validate(PAYLOADS[schema.__name__])

    def complete(self, system, user, max_tokens=None):
        target = re.search(r"TARGET FILE: (\S+)", user).group(1)
        return f"```gdscript\n{FILES[target]}```"


def test_full_pipeline_produces_a_consistent_project(tmp_path):
    cfg_file = tmp_path / "c.toml"
    cfg_file.write_text(f'[output]\nbase_dir = "{(tmp_path / "out").as_posix()}"\n'
                        '[godot]\ntarget_version = "4.3"\n', encoding="utf-8")
    config = load_config(cfg_file, use_dotenv=False, require_llm=False)

    ctx = Orchestrator(config, llm=ScriptedLLM()).run("space cat platformer")
    root = ctx.project_dir

    summary = json.loads((root / "reports/run_summary.json").read_text(encoding="utf-8"))
    assert [s["name"] for s in summary["stages"] if not s["ok"]] == []
    assert not any(s["skipped"] for s in summary["stages"])

    project = (root / "project.godot").read_text(encoding="utf-8")
    assert 'run/main_scene="res://scenes/main.tscn"' in project
    for action in ("jump", "move_left", "move_right"):
        assert f"\n{action}={{" in project

    assert (root / "scripts/main.gd").is_file() and (root / "icon.svg").is_file()
    for sprite in ("hero", "blob_enemy", "ground", "sky_bg"):
        assert (root / f"assets/sprites/{sprite}.png").is_file()
    for sfx in ("jump", "hit", "pickup", "death", "win", "music_loop"):
        assert (root / f"assets/audio/{sfx}.wav").is_file()

    for name in ("qa_report.json", "qa_report_final.json", "playtest_summary.json",
                 *[f"playtest_{k}.json" for k in ("brainrot", "hardcore", "casual", "critic", "chaos_qa")]):
        assert (root / "reports" / name).is_file(), name

    qa = ctx.get_artifact("qa_final")
    assert qa["verdict"] == "passed_static_checks_only" and qa["unresolved"] == []
    assert ctx.get_artifact("programmer_refine")["applied"] == ["scripts/main.gd"]
    assert ctx.get_artifact("aggregator")["average_score"] == 6.0
