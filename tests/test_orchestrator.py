import json

import pytest

from core.config import load_config
from core.orchestrator import Orchestrator, PipelineError, StageSpec, make_project_slug


def test_slug_ascii_and_fallback():
    assert make_project_slug("Space Cat Platformer!") == "space_cat_platformer"
    assert make_project_slug("لعبة منصات").startswith("game_")


def test_missing_stage_fails_and_writes_summary(tmp_path):
    cfg_file = tmp_path / "c.toml"
    out_dir = (tmp_path / "out").as_posix()
    cfg_file.write_text(f'[output]\nbase_dir = "{out_dir}"\n', encoding="utf-8")
    config = load_config(cfg_file, use_dotenv=False, require_llm=False)

    pipeline = ((StageSpec("ghost", "agents.does_not_exist", "Ghost", 3),),)
    orchestrator = Orchestrator(config, llm=object(), pipeline=pipeline)  # llm unused here

    with pytest.raises(PipelineError):
        orchestrator.run("test idea")

    summaries = list((tmp_path / "out").glob("*/reports/run_summary.json"))
    assert len(summaries) == 1
    data = json.loads(summaries[0].read_text(encoding="utf-8"))
    assert data["stages"][0]["ok"] is False
