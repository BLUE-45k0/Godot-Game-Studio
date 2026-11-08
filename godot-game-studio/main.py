"""CLI entry point:  python main.py "game idea" """
from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import sys
from pathlib import Path

from core.config import Config, ConfigError, load_config
from core.llm_client import LLMError
from core.logging_setup import setup_logging
from core.orchestrator import Orchestrator, PipelineError
from core.schemas import ProjectContext

logger = logging.getLogger("ggs")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Godot Game Studio: turn a game idea into a Godot 4 project with a team of AI agents.",
    )
    parser.add_argument("idea", help='the game idea in one or two sentences, e.g. "لعبة منصات عن قطة فضاء"')
    parser.add_argument("--config", help="path to config.toml (default: ./config.toml or GGS_CONFIG)")
    parser.add_argument("--output-dir", help="override output.base_dir")
    parser.add_argument("--godot-path", help="override godot.executable_path")
    parser.add_argument("--overwrite", action="store_true", help="replace an existing project of the same name")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    return parser


def apply_overrides(cfg: Config, args: argparse.Namespace) -> Config:
    """Return a copy of the config with CLI overrides applied."""
    output = cfg.output
    if args.output_dir:
        output = dataclasses.replace(output, base_dir=Path(args.output_dir).expanduser().resolve())
    if args.overwrite:
        output = dataclasses.replace(output, overwrite_existing=True)
    godot = cfg.godot
    if args.godot_path:
        exe = Path(args.godot_path).expanduser()
        if not exe.is_file():
            raise ConfigError(f"--godot-path does not point to a file: {exe}")
        godot = dataclasses.replace(godot, executable_path=exe)
    return dataclasses.replace(cfg, output=output, godot=godot)


def _latest_project(base: Path) -> Path | None:
    if not base.is_dir():
        return None
    dirs = [d for d in base.iterdir() if d.is_dir()]
    return max(dirs, key=lambda d: d.stat().st_mtime) if dirs else None


def _failed_stages(ctx: ProjectContext) -> list[str]:
    path = ctx.reports_dir / "run_summary.json"
    try:
        stages = json.loads(path.read_text(encoding="utf-8")).get("stages", [])
    except (OSError, ValueError):
        return []
    return [f"{s['name']}: {s.get('error')}" for s in stages if not s.get("ok")]


def print_summary(ctx: ProjectContext) -> int:
    """Print the end-of-run summary. Returns the exit code (0 clean, 2 problems remain)."""
    qa = ctx.get_artifact("qa_final") or ctx.get_artifact("qa") or {}
    aggregator = ctx.get_artifact("aggregator") or {}
    failed = _failed_stages(ctx)
    unresolved = qa.get("unresolved", [])

    print()
    print(f"Project folder : {ctx.project_dir}")
    print(f"QA verdict     : {qa.get('verdict', 'unknown')} (checked by Godot: {qa.get('godot_checked')})")
    print(f"Unresolved errs: {len(unresolved)}")
    for issue in unresolved[:10]:
        where = f"{issue['file']}:{issue['line']}" if issue.get("line") else issue["file"]
        print(f"  - {where}  {issue['message']}")
    if aggregator:
        print(f"Playtest score : {aggregator.get('average_score')} / 10 "
              f"({len(aggregator.get('scores', {}))} simulated reviewers, nobody actually played)")
    for item in failed:
        print(f"Stage problem  : {item}")
    print(f"Reports        : {ctx.reports_dir}")
    print(f'Open in Godot  : godot --path "{ctx.project_dir}" --editor')
    return 2 if unresolved or failed else 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass

    args = build_parser().parse_args(argv)
    setup_logging(logging.DEBUG if args.verbose else logging.INFO)
    try:
        cfg = apply_overrides(load_config(args.config), args)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    try:
        ctx = Orchestrator(cfg).run(args.idea)
    except (PipelineError, LLMError, ValueError) as exc:
        logger.error("Run failed: %s", exc)
        latest = _latest_project(cfg.output.base_dir)
        if latest is not None:
            print(f"Partial output and reports/run_summary.json (if any): {latest}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    return print_summary(ctx)


if __name__ == "__main__":
    sys.exit(main())
