import argparse
from pathlib import Path

import pytest

from core.config import ConfigError, load_config
from main import apply_overrides, build_parser


def _cfg(tmp_path):
    cfg_file = tmp_path / "c.toml"
    cfg_file.write_text("", encoding="utf-8")
    return load_config(cfg_file, use_dotenv=False, require_llm=False)


def test_parser_accepts_idea_and_flags():
    args = build_parser().parse_args(["لعبة منصات", "--overwrite", "-v", "--output-dir", "x"])
    assert args.idea == "لعبة منصات" and args.overwrite and args.verbose and args.output_dir == "x"


def test_overrides_applied(tmp_path):
    cfg = _cfg(tmp_path)
    args = argparse.Namespace(output_dir=str(tmp_path / "o"), overwrite=True, godot_path=None)
    new = apply_overrides(cfg, args)
    assert new.output.base_dir == (tmp_path / "o").resolve() and new.output.overwrite_existing is True
    assert new.godot.executable_path is None


def test_bad_godot_path_rejected(tmp_path):
    args = argparse.Namespace(output_dir=None, overwrite=False, godot_path=str(tmp_path / "missing"))
    with pytest.raises(ConfigError):
        apply_overrides(_cfg(tmp_path), args)
    exe = tmp_path / "godot"
    exe.write_text("", encoding="utf-8")
    args.godot_path = str(exe)
    assert apply_overrides(_cfg(tmp_path), args).godot.executable_path == Path(exe)
