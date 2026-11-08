import json

from agents.playtesters.aggregator import AggregatorAgent
from agents.playtesters.brainrot import BrainrotPlaytester
from agents.playtesters.chaos_qa import ChaosQAPlaytester
from core.config import load_config
from core.schemas import ProjectContext


def _payload(schema) -> dict:
    fields = schema.model_fields
    if "priority_changes" in fields:
        return {"summary": "A merged summary of all reviews.",
                "priority_changes": ["scripts/main.gd: spawn the first enemy after 2s"]}
    data = {"persona": "wrong", "score": 6, "top_3_problems": ["slow start"],
            "top_3_strengths": ["clear goal"], "fix_suggestions": ["scripts/main.gd: shorten the intro"]}
    if "edge_cases" in fields:
        data["edge_cases"] = [
            {"scenario": f"scenario number {i} breaks", "input_or_value": "0",
             "expected_failure": "crash", "target_file": "scripts/main.gd", "severity": "high"}
            for i in range(5)
        ]
    return data


class FakeLLM:
    last_stop_reason = "end_turn"

    def complete_json(self, system, user, schema, **kwargs):
        return schema.model_validate(_payload(schema))


def _ctx(tmp_path):
    cfg_file = tmp_path / "c.toml"
    cfg_file.write_text("", encoding="utf-8")
    config = load_config(cfg_file, use_dotenv=False, require_llm=False)
    project = tmp_path / "proj"
    (project / "scripts").mkdir(parents=True)
    (project / "scripts/main.gd").write_text("extends Node\n", encoding="utf-8")
    return ProjectContext("idea", project, config, FakeLLM())  # type: ignore[arg-type]


def test_persona_report_written_with_forced_persona(tmp_path):
    ctx = _ctx(tmp_path)
    data = ChaosQAPlaytester().run(ctx)
    assert data["persona"] == "chaos_qa" and len(data["edge_cases"]) == 5
    assert "Nobody actually played" in data["basis"]
    saved = json.loads((ctx.reports_dir / "playtest_chaos_qa.json").read_text(encoding="utf-8"))
    assert saved["persona"] == "chaos_qa"

    data = BrainrotPlaytester().run(ctx)
    assert data["persona"] == "brainrot" and data["score"] == 6


def test_aggregator_averages_and_reports_missing(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.set_artifact("playtest_brainrot", {"score": 4})
    ctx.set_artifact("playtest_hardcore", {"score": 7})
    data = AggregatorAgent().run(ctx)
    assert data["average_score"] == 5.5
    assert data["scores"] == {"brainrot": 4, "hardcore": 7}
    assert set(data["missing_personas"]) == {"casual", "critic", "chaos_qa"}
    assert data["priority_changes"]
    assert (ctx.reports_dir / "playtest_summary.json").is_file()
