"""Shared data models and the ProjectContext passed between agents."""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from core.config import Config
from core.llm_client import LLMClient


class PlaytestReport(BaseModel):
    """Output contract for every playtester persona."""

    persona: str
    score: int = Field(ge=1, le=10)
    top_3_problems: list[str] = Field(min_length=1, max_length=3)
    top_3_strengths: list[str] = Field(min_length=1, max_length=3)
    fix_suggestions: list[str] = Field(min_length=1)


class QAIssue(BaseModel):
    """A single problem found by static checks or by the Godot binary."""

    severity: Literal["error", "warning"]
    source: Literal["static", "godot"]
    file: str
    line: int | None = None
    message: str


class QAReport(BaseModel):
    """Final QA report. `godot_checked` is False when only static checks ran."""

    godot_checked: bool
    attempts_used: int = 0
    issues: list[QAIssue] = Field(default_factory=list)
    unresolved: list[QAIssue] = Field(default_factory=list)
    notes: str = ""


@dataclass
class ProjectContext:
    """Shared state for one generation run. Thread-safe for artifact access."""

    idea: str
    project_dir: Path
    config: Config
    llm: LLMClient
    artifacts: dict[str, Any] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def docs_dir(self) -> Path:
        """Design documents (ignored by Godot via .gdignore)."""
        return self.project_dir / "studio_docs"

    @property
    def reports_dir(self) -> Path:
        """QA and playtest reports (ignored by Godot via .gdignore)."""
        return self.project_dir / "reports"

    def set_artifact(self, name: str, data: Any) -> None:
        """Store a stage result in memory and persist it as JSON."""
        with self._lock:
            self.artifacts[name] = data
            self.docs_dir.mkdir(parents=True, exist_ok=True)
            target = self.docs_dir / f"{name}.json"
            target.write_text(
                json.dumps(data, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )

    def get_artifact(self, name: str, default: Any = None) -> Any:
        """Fetch a stored stage result."""
        with self._lock:
            return self.artifacts.get(name, default)

    def has_artifact(self, name: str) -> bool:
        with self._lock:
            return name in self.artifacts
