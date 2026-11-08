"""Runs the real Godot binary headless: --import, then --check-only per script."""
from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from core.config import GodotConfig
from core.schemas import QAIssue

logger = logging.getLogger(__name__)

MAX_SCRIPTS = 60
_START_RE = re.compile(r"^\s*(SCRIPT ERROR|USER ERROR|USER WARNING|ERROR|WARNING)\s*:\s*(.*)$")
_AT_RE = re.compile(r"^\s*at:\s*.*?res://([^:\s)]+):(\d+)")
_PATH_RE = re.compile(r"res://([^\s\"')]+)")
# Shutdown chatter that headless tool runs commonly print; not project problems.
_NOISE: tuple[str, ...] = (
    "leaked at exit", "still in use at exit", "ObjectDB instances",
    "resources still in use", "RID allocations of type",
)


@dataclass
class GodotRunResult:
    ran: bool                      # Godot could be launched
    complete: bool                 # every step finished without timeout/launch failure
    issues: list[QAIssue] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    log_tail: str = ""


def parse_godot_output(output: str, fallback_file: str = "project") -> list[QAIssue]:
    """Turn Godot's console output into QAIssue objects."""
    raw: list[dict[str, object]] = []
    current: dict[str, object] | None = None
    for line in output.splitlines():
        start = _START_RE.match(line)
        if start:
            if current is not None:
                raw.append(current)
            current = {"kind": start.group(1), "message": start.group(2).strip(), "file": None, "line": None}
            continue
        if current is not None and current["file"] is None:
            at = _AT_RE.match(line)
            if at:
                current["file"], current["line"] = at.group(1), int(at.group(2))
    if current is not None:
        raw.append(current)

    issues: list[QAIssue] = []
    for item in raw:
        message = str(item["message"])
        if any(noise in message for noise in _NOISE):
            continue
        file, line_no = item["file"], item["line"]
        if file is None:
            found = _PATH_RE.search(message)
            if found:
                parts = re.match(r"^(.*?)(?::(\d+))?$", found.group(1).rstrip(".,;"))
                file = parts.group(1) if parts else found.group(1)
                line_no = int(parts.group(2)) if parts and parts.group(2) else None
            else:
                file = fallback_file
        severity = "warning" if item["kind"] in ("WARNING", "USER WARNING") else "error"
        issues.append(QAIssue(severity=severity, source="godot", file=str(file),  # type: ignore[arg-type]
                              line=line_no if isinstance(line_no, int) else None, message=message))

    # The generic "Failed to load script" line is redundant when a precise error exists.
    precise = {i.file for i in issues if not i.message.startswith("Failed to load script")}
    issues = [i for i in issues if not (i.message.startswith("Failed to load script") and i.file in precise)]

    seen: set[tuple[str, str, int | None, str]] = set()
    unique: list[QAIssue] = []
    for issue in issues:
        key = (issue.severity, issue.file, issue.line, issue.message)
        if key not in seen:
            seen.add(key)
            unique.append(issue)
    return unique


def _run(cmd: list[str], timeout: float) -> tuple[int | None, str]:
    """Run a command, merging stderr into stdout. Returns (exit code or None on timeout, output)."""
    try:
        proc = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace", timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        partial = exc.stdout
        if isinstance(partial, bytes):
            partial = partial.decode("utf-8", errors="replace")
        return None, partial or ""
    return proc.returncode, proc.stdout or ""


def _exit_issue(code: int, output: str, file: str) -> QAIssue:
    tail = " ".join(output.strip().splitlines()[-3:])[:300]
    return QAIssue(
        severity="warning", source="godot", file=file,
        message=f"Godot exited with code {code} without a recognizable error message. Output tail: {tail}",
    )


def run_godot_checks(cfg: GodotConfig, project_dir: Path) -> GodotRunResult:
    """Import the project, then parse-check every script. Never raises for engine problems."""
    exe = cfg.executable_path
    if exe is None:
        return GodotRunResult(False, False, notes=["Godot executable is not configured (godot.executable_path)."])
    root = project_dir.resolve()
    base = [str(exe), "--headless", "--path", str(root)]
    issues: list[QAIssue] = []
    notes: list[str] = []
    tails: list[str] = []
    complete = True

    try:
        code, out = _run(base + ["--import"], cfg.headless_timeout_sec)
    except OSError as exc:
        return GodotRunResult(False, False, notes=[f"Could not launch Godot: {exc}"])
    tails.append(out[-1500:])
    if code is None:
        complete = False
        notes.append(f"--import timed out after {cfg.headless_timeout_sec:.0f}s.")
    found = parse_godot_output(out, "project")
    issues += found
    if code not in (0, None) and not any(i.severity == "error" for i in found):
        issues.append(_exit_issue(code, out, "project"))

    scripts: list[str] = []
    for top in ("scripts", "scenes"):
        scripts += [p.relative_to(root).as_posix() for p in sorted((root / top).rglob("*.gd"))]
    if len(scripts) > MAX_SCRIPTS:
        notes.append(f"Only the first {MAX_SCRIPTS} of {len(scripts)} scripts were checked.")
        complete = False
        scripts = scripts[:MAX_SCRIPTS]

    for rel in scripts:
        try:
            code, out = _run(base + ["--check-only", "--script", f"res://{rel}"], cfg.headless_timeout_sec)
        except OSError as exc:
            notes.append(f"Could not launch Godot for {rel}: {exc}")
            complete = False
            break
        tails.append(out[-800:])
        if code is None:
            complete = False
            notes.append(f"--check-only timed out for {rel}.")
            continue
        found = parse_godot_output(out, rel)
        issues += found
        if code != 0 and not any(i.severity == "error" for i in found):
            issues.append(_exit_issue(code, out, rel))

    seen: set[tuple[str, str, int | None, str]] = set()
    unique: list[QAIssue] = []
    for issue in issues:
        key = (issue.severity, issue.file, issue.line, issue.message)
        if key not in seen:
            seen.add(key)
            unique.append(issue)
    return GodotRunResult(True, complete, unique, notes, "\n".join(tails)[-4000:])
