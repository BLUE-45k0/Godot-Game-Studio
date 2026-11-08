import pytest

from core.config import ConfigError, load_config


def test_missing_model_env_fails(monkeypatch, tmp_path):
    cfg_file = tmp_path / "c.toml"
    cfg_file.write_text("", encoding="utf-8")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy")
    monkeypatch.delenv("GGS_MODEL", raising=False)
    with pytest.raises(ConfigError):
        load_config(cfg_file, use_dotenv=False)


def test_defaults_without_llm(tmp_path):
    cfg_file = tmp_path / "c.toml"
    cfg_file.write_text("[qa]\nmax_fix_attempts = 2\n", encoding="utf-8")
    cfg = load_config(cfg_file, use_dotenv=False, require_llm=False)
    assert cfg.qa.max_fix_attempts == 2
    assert cfg.godot.executable_path is None


def test_invalid_playtester_rejected(tmp_path):
    cfg_file = tmp_path / "c.toml"
    cfg_file.write_text('[playtesters]\nenabled = ["nobody"]\n', encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(cfg_file, use_dotenv=False, require_llm=False)


def test_example_config_is_valid():
    from pathlib import Path

    example = Path(__file__).resolve().parents[1] / "config.example.toml"
    cfg = load_config(example, use_dotenv=False, require_llm=False)
    assert cfg.llm.max_retries == 3 and cfg.assets.kenney_pack_urls == ()
