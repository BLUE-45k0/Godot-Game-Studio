"""Runs the agent pipeline wave by wave and records a run summary."""
from __future__ import annotations

import dataclasses
import hashlib
import importlib
import json
import logging
import re
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from core.config import Config
from core.llm_client import LLMClient
from core.schemas import ProjectContext

logger = logging.getLogger(__name__)


class StageAgent(Protocol):
    """Contract every agent must satisfy: return a JSON-serialisable dict."""

    name: str

    def run(self, ctx: ProjectContext) -> dict[str, Any]:
        """Execute the stage and return its result."""
        raise NotImplementedError


class PipelineError(Exception):
    """Raised when a critical stage fails; the run summary is still written."""


class StageLoadError(Exception):
    """Raised when a stage's module or class cannot be loaded."""


@dataclass(frozen=True)
class StageSpec:
    """Declarative description of one pipeline stage."""

    name: str                         # also the artifact key
    module: str
    class_name: str
    phase: int                        # build phase in which this module was delivered
    critical: bool = True
    playtester: str | None = None     # key checked against config.playtesters.enabled
    skip_unless_any: tuple[str, ...] = ()  # run only if one of these artifacts exists
    options: dict[str, Any] = field(default_factory=dict)  # constructor kwargs


@dataclass
class StageResult:
    name: str
    ok: bool
    seconds: float
    critical: bool
    skipped: bool = False
    error: str | None = None


def _playtest_stage(key: str, class_name: str, module: str) -> StageSpec:
    return StageSpec(
        name=f"playtest_{key}",
        module=f"agents.playtesters.{module}",
        class_name=class_name,
        phase=5,
        critical=False,
        playtester=key,
    )


_PLAYTEST_NAMES = tuple(f"playtest_{k}" for k in ("brainrot", "hardcore", "casual", "critic", "chaos_qa"))

# Each inner tuple is a "wave": stages in a wave run in parallel.
PIPELINE: tuple[tuple[StageSpec, ...], ...] = (
    (StageSpec("game_designer", "agents.game_designer", "GameDesignerAgent", 3),),
    (StageSpec("level_designer", "agents.level_designer", "LevelDesignerAgent", 3),),
    (
        StageSpec("art", "agents.art_agent", "ArtAgent", 3),
        StageSpec("audio", "agents.audio_director", "AudioDirectorAgent", 3),
    ),
    (StageSpec("programmer", "agents.godot_programmer", "GodotProgrammerAgent", 3),),
    (StageSpec("qa", "agents.qa_agent", "QAAgent", 5),),
    (
        _playtest_stage("brainrot", "BrainrotPlaytester", "brainrot"),
        _playtest_stage("hardcore", "HardcorePlaytester", "hardcore"),
        _playtest_stage("casual", "CasualPlaytester", "casual"),
        _playtest_stage("critic", "CriticPlaytester", "critic"),
        _playtest_stage("chaos_qa", "ChaosQAPlaytester", "chaos_qa"),
    ),
    (
        StageSpec(
            "aggregator", "agents.playtesters.aggregator", "AggregatorAgent", 5,
            critical=False, skip_unless_any=_PLAYTEST_NAMES,
        ),
    ),
    (
        StageSpec(
            "programmer_refine", "agents.godot_programmer", "GodotProgrammerAgent", 3,
            critical=False, skip_unless_any=("aggregator",), options={"mode": "refine"},
        ),
    ),
    (
        StageSpec(
            "qa_final", "agents.qa_agent", "QAAgent", 5,
            critical=False, skip_unless_any=("programmer_refine",), options={"final": True},
        ),
    ),
)


def make_project_slug(idea: str) -> str:
    """ASCII folder name from the idea; hash-based fallback for non-Latin ideas."""
    ascii_part = idea.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", ascii_part).strip("_").lower()[:40]
    if len(slug) < 3:
        slug = "game_" + hashlib.sha1(idea.encode("utf-8")).hexdigest()[:8]
    return slug


class Orchestrator:
    """Coordinates the full generation run."""

    def __init__(
        self,
        config: Config,
        llm: LLMClient | None = None,
        pipeline: tuple[tuple[StageSpec, ...], ...] = PIPELINE,
    ) -> None:
        self.config = config
        self.llm = llm if llm is not None else LLMClient(config.llm)
        self.pipeline = pipeline

    # ------------------------------------------------------------------
    def run(self, idea: str) -> ProjectContext:
        """Generate a project for `idea`. Raises PipelineError on critical failure."""
        idea = idea.strip()
        if not idea:
            raise ValueError("Game idea must not be empty")

        project_dir = self._prepare_project(idea)
        ctx = ProjectContext(idea=idea, project_dir=project_dir, config=self.config, llm=self.llm)
        logger.info("Project folder: %s", project_dir)

        started = datetime.now(timezone.utc)
        results: list[StageResult] = []
        try:
            for wave in self.pipeline:
                wave_results = self._run_wave(wave, ctx)
                results.extend(wave_results)
                failed = [r for r in wave_results if r.critical and not r.ok and not r.skipped]
                if failed:
                    detail = "; ".join(f"{r.name}: {r.error}" for r in failed)
                    raise PipelineError(f"Critical stage failed -> {detail}")
        finally:
            self._write_run_summary(ctx, results, started)
        return ctx

    # ------------------------------------------------------------------
    def _prepare_project(self, idea: str) -> Path:
        base = self.config.output.base_dir
        base.mkdir(parents=True, exist_ok=True)
        slug = make_project_slug(idea)
        target = base / slug

        if target.exists():
            if self.config.output.overwrite_existing:
                if target.resolve().parent != base.resolve():
                    raise PipelineError(f"Refusing to delete outside output dir: {target}")
                shutil.rmtree(target)
            else:
                counter = 2
                while (base / f"{slug}_{counter}").exists():
                    counter += 1
                target = base / f"{slug}_{counter}"

        for sub in ("scenes", "scripts", "assets", "studio_docs", "reports"):
            (target / sub).mkdir(parents=True, exist_ok=True)
        # Stop Godot from importing our JSON docs/reports as resources.
        for ignored in ("studio_docs", "reports"):
            (target / ignored / ".gdignore").write_text("", encoding="utf-8")
        (target / "studio_docs" / "idea.txt").write_text(idea, encoding="utf-8")
        return target

    def _load_stage(self, spec: StageSpec) -> StageAgent:
        try:
            module = importlib.import_module(spec.module)
        except ModuleNotFoundError as exc:
            missing = exc.name or ""
            if spec.module == missing or spec.module.startswith(missing + "."):
                raise StageLoadError(f"module '{spec.module}' does not exist") from exc
            raise
        cls = getattr(module, spec.class_name, None)
        if cls is None:
            raise StageLoadError(f"class '{spec.class_name}' not found in {spec.module}")
        return cls(**spec.options)

    def _should_skip(self, spec: StageSpec, ctx: ProjectContext) -> str | None:
        if spec.playtester and spec.playtester not in self.config.playtesters.enabled:
            return "playtester disabled in config"
        if spec.skip_unless_any and not any(ctx.has_artifact(n) for n in spec.skip_unless_any):
            return "prerequisite artifacts missing"
        return None

    def _run_stage(self, spec: StageSpec, ctx: ProjectContext) -> StageResult:
        reason = self._should_skip(spec, ctx)
        if reason:
            logger.info("Skipping %s (%s)", spec.name, reason)
            return StageResult(spec.name, True, 0.0, spec.critical, skipped=True, error=reason)

        logger.info("Stage start: %s", spec.name)
        started = time.perf_counter()
        try:
            agent = self._load_stage(spec)
            data = agent.run(ctx)
            if not isinstance(data, dict):
                raise TypeError(f"{spec.name} returned {type(data).__name__}, expected dict")
            ctx.set_artifact(spec.name, data)
        except Exception as exc:  # noqa: BLE001 - boundary: any agent failure is reported, not fatal here
            elapsed = time.perf_counter() - started
            logger.error("Stage failed: %s -> %s: %s", spec.name, type(exc).__name__, exc)
            logger.debug("Traceback for %s", spec.name, exc_info=True)
            return StageResult(spec.name, False, elapsed, spec.critical, error=f"{type(exc).__name__}: {exc}")
        elapsed = time.perf_counter() - started
        logger.info("Stage done: %s (%.1fs)", spec.name, elapsed)
        return StageResult(spec.name, True, elapsed, spec.critical)

    def _run_wave(self, wave: tuple[StageSpec, ...], ctx: ProjectContext) -> list[StageResult]:
        if len(wave) == 1:
            return [self._run_stage(wave[0], ctx)]
        with ThreadPoolExecutor(max_workers=len(wave)) as pool:
            futures = [pool.submit(self._run_stage, spec, ctx) for spec in wave]
            return [future.result() for future in futures]

    def _write_run_summary(
        self, ctx: ProjectContext, results: list[StageResult], started: datetime
    ) -> None:
        summary = {
            "idea": ctx.idea,
            "project_dir": str(ctx.project_dir),
            "started_utc": started.isoformat(),
            "finished_utc": datetime.now(timezone.utc).isoformat(),
            "config_file": str(self.config.source_path) if self.config.source_path else None,
            "stages": [dataclasses.asdict(r) for r in results],
        }
        ctx.reports_dir.mkdir(parents=True, exist_ok=True)
        (ctx.reports_dir / "run_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
