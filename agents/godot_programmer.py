"""Godot Lead Programmer: plans the project, then writes each .gd/.tscn file."""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from agents.asset_manifest import build_manifest
from agents.base_agent import SNAKE_CASE, AgentError, BaseAgent, extract_code_block
from agents.game_designer import ALLOWED_KEYS
from core.safe_files import UnsafePathError, clean_rel_path, safe_write, validate_project_path
from core.schemas import ProjectContext
from tools.godot_writer import write_project_files

logger = logging.getLogger(__name__)

ALLOWED_EXTS: tuple[str, ...] = (".gd", ".tscn", ".tres")
ALLOWED_ROOTS: tuple[str, ...] = ("scripts", "scenes")
MAX_REPAIRS = 2
_IDENT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


# --------------------------------------------------------------------------- schemas
class PlannedFile(BaseModel):
    path: str
    purpose: str = Field(min_length=5)
    depends_on: list[str] = Field(default_factory=list)

    @field_validator("path")
    @classmethod
    def _path_ok(cls, value: str) -> str:
        return validate_project_path(value, ALLOWED_ROOTS, ALLOWED_EXTS)

    @field_validator("depends_on")
    @classmethod
    def _deps_ok(cls, value: list[str]) -> list[str]:
        return [clean_rel_path(v) for v in value]


class InputAction(BaseModel):
    name: str
    keys: list[str] = Field(min_length=1)

    @field_validator("name")
    @classmethod
    def _name_ok(cls, value: str) -> str:
        if not SNAKE_CASE.match(value):
            raise ValueError(f"input action '{value}' must be snake_case")
        return value

    @field_validator("keys")
    @classmethod
    def _keys_ok(cls, value: list[str]) -> list[str]:
        bad = [k for k in value if k not in ALLOWED_KEYS]
        if bad:
            raise ValueError(f"unsupported keys {bad}; allowed: {sorted(ALLOWED_KEYS)}")
        return value


class BuildPlan(BaseModel):
    files: list[PlannedFile] = Field(min_length=3, max_length=16)
    main_scene: str
    autoloads: dict[str, str] = Field(default_factory=dict)
    input_actions: list[InputAction] = Field(min_length=1)
    window_width: int = Field(default=1152, ge=320, le=3840)
    window_height: int = Field(default=648, ge=240, le=2160)
    notes: str = ""

    @field_validator("main_scene")
    @classmethod
    def _main_ok(cls, value: str) -> str:
        return validate_project_path(value, ("scenes",), (".tscn",))

    @field_validator("autoloads")
    @classmethod
    def _autoloads_ok(cls, value: dict[str, str]) -> dict[str, str]:
        cleaned: dict[str, str] = {}
        for name, path in value.items():
            if not _IDENT_RE.match(name):
                raise ValueError(f"autoload name '{name}' is not a valid identifier")
            cleaned[name] = validate_project_path(path, ("scripts",), (".gd",))
        return cleaned

    @model_validator(mode="after")
    def _consistent(self) -> "BuildPlan":
        paths = [f.path for f in self.files]
        if len(set(paths)) != len(paths):
            raise ValueError("duplicate file paths in plan")
        known = set(paths)
        if self.main_scene not in known:
            raise ValueError("main_scene must be one of the planned files")
        for name, path in self.autoloads.items():
            if path not in known:
                raise ValueError(f"autoload '{name}' points to an unplanned file")
        for f in self.files:
            missing = [d for d in f.depends_on if d not in known]
            if missing:
                raise ValueError(f"{f.path} depends on unplanned files {missing}")
        if sum(1 for p in paths if p.endswith(".gd")) < 2:
            raise ValueError("plan needs at least 2 scripts")
        return self


class RefineEdit(BaseModel):
    path: str
    instructions: str = Field(min_length=10)

    @field_validator("path")
    @classmethod
    def _path_ok(cls, value: str) -> str:
        return validate_project_path(value, ALLOWED_ROOTS, ALLOWED_EXTS)


class RefinePlan(BaseModel):
    edits: list[RefineEdit] = Field(default_factory=list, max_length=10)


# --------------------------------------------------------------------------- validation
_GD3_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^\s*onready\s+var\b", re.M), "use '@onready var' (Godot 4)"),
    (re.compile(r"^\s*export\s*(\(|var\b)", re.M), "use '@export var' (Godot 4)"),
    (re.compile(r"\byield\s*\("), "use 'await' instead of yield"),
    (re.compile(r"\bKinematicBody(2D)?\b"), "use CharacterBody2D"),
    (re.compile(r"\bmove_and_slide\(\s*[^)\s]"),
     "move_and_slide() takes no arguments in Godot 4; set 'velocity' first"),
    (re.compile(r"\.connect\(\s*\"[^\"]*\"\s*,\s*[A-Za-z_][A-Za-z0-9_.]*\s*,\s*\""),
     "use signal.connect(callable) (Godot 4)"),
    (re.compile(r"\bsetget\b"), "setget is Godot 3; use property setter/getter syntax"),
    (re.compile(r"^\s*tool\s*$", re.M), "use '@tool' (Godot 4)"),
    (re.compile(r"\.instance\(\)"), "use .instantiate()"),
    (re.compile(r"\brand_range\("), "use randf_range() / randi_range()"),
    (re.compile(r"\bchange_scene\("), "use change_scene_to_file()"),
    (re.compile(r"\bPool[A-Za-z]+Array\b"), "use Packed*Array types"),
    (re.compile(r"\bOS\.get_ticks_msec\b"), "use Time.get_ticks_msec()"),
)
_FUNC_RE = re.compile(r"^\s*(?:static\s+)?func\s+(\w+)\s*\([^)]*\)\s*:", re.M)
_RES_REF_RE = re.compile(r"res://((?:scripts|scenes)/[^\"'\s)\]]+)")
_HEADER_RE = re.compile(r"^\[gd_scene([^\]]*)\]", re.M)
_IFACE_RE = re.compile(
    r"^(?:class_name|extends|signal|const|enum|@export|@onready|var|static var|func|static func)\b.*$",
    re.M,
)


def normalize_indent(code: str) -> str:
    """If tabs and spaces are mixed, convert leading groups of 4 spaces to tabs."""
    lines = code.splitlines()
    has_tab = any(line.startswith("\t") for line in lines)
    has_space = any(re.match(r" +\S", line) for line in lines)
    if not (has_tab and has_space):
        return code
    fixed: list[str] = []
    for line in lines:
        body = line.lstrip(" \t")
        lead = line[: len(line) - len(body)].replace("    ", "\t")
        fixed.append(lead + body)
    return "\n".join(fixed) + "\n"


def validate_gd(code: str) -> list[str]:
    """Return problems found in a GDScript file (empty list = looks fine)."""
    problems: list[str] = []
    if not re.search(r"^\s*(extends|class_name)\b", code, re.M):
        problems.append("script must declare 'extends' (or class_name)")
    for pattern, hint in _GD3_PATTERNS:
        if pattern.search(code):
            problems.append(hint)
    for match in _FUNC_RE.finditer(code):
        problems.append(f"function '{match.group(1)}' needs an explicit return type (-> Type or -> void)")
    return problems


def validate_tscn(code: str) -> tuple[str, list[str]]:
    """Check a .tscn, repair its header (load_steps) and return (fixed_code, problems)."""
    problems: list[str] = []
    header = _HEADER_RE.search(code)
    if header is None or not code.lstrip().startswith("[gd_scene"):
        return code, ["scene must start with a [gd_scene ...] header"]
    attrs = header.group(1)
    if "format=3" not in attrs:
        problems.append("scene header must contain format=3 (Godot 4)")
    ext_ids = re.findall(r'^\[ext_resource[^\]]*\bid="([^"]+)"', code, re.M)
    sub_ids = re.findall(r'^\[sub_resource[^\]]*\bid="([^"]+)"', code, re.M)
    steps = len(ext_ids) + len(sub_ids)
    parts = ["[gd_scene"]
    if steps:
        parts.append(f"load_steps={steps + 1}")
    parts.append("format=3")
    uid = re.search(r'uid="[^"]+"', attrs)
    if uid:
        parts.append(uid.group(0))
    code = code[: header.start()] + " ".join(parts) + "]" + code[header.end():]
    for ref in sorted(set(re.findall(r'ExtResource\(\s*"([^"]+)"\s*\)', code))):
        if ref not in ext_ids:
            problems.append(f'ExtResource("{ref}") is used but never declared')
    for ref in sorted(set(re.findall(r'SubResource\(\s*"([^"]+)"\s*\)', code))):
        if ref not in sub_ids:
            problems.append(f'SubResource("{ref}") is used but never declared')
    if not re.search(r"^\[node name=", code, re.M):
        problems.append("scene has no [node ...] entry")
    return code, problems


def check_file(path: str, code: str, known_paths: set[str]) -> tuple[str, list[str]]:
    """Run all deterministic checks for one file; may auto-fix indentation/header."""
    problems: list[str] = []
    if path.endswith(".gd"):
        code = normalize_indent(code)
        problems += validate_gd(code)
    elif path.endswith(".tscn"):
        code, tscn_problems = validate_tscn(code)
        problems += tscn_problems
    for ref in sorted(set(_RES_REF_RE.findall(code))):
        if ref not in known_paths:
            problems.append(f"references res://{ref}, which is not a file of this project")
    return code, problems


def interface_summary(path: str, content: str) -> str:
    """Short outline of a file (signatures / nodes) so later files stay consistent."""
    if path.endswith(".gd"):
        text = "\n".join(m.group(0).rstrip() for m in _IFACE_RE.finditer(content))
    else:
        text = "\n".join(
            line for line in content.splitlines() if line.startswith(("[node ", "[ext_resource"))
        )
    return text[:2500]


def _ordered(files: list[PlannedFile]) -> list[PlannedFile]:
    """Topological order by depends_on; falls back to plan order on cycles."""
    remaining = list(files)
    done: set[str] = set()
    order: list[PlannedFile] = []
    while remaining:
        ready = [f for f in remaining if all(d in done for d in f.depends_on)]
        if not ready:
            logger.warning("Cyclic depends_on in plan; using plan order for the rest")
            order.extend(remaining)
            break
        for f in ready:
            order.append(f)
            done.add(f.path)
            remaining.remove(f)
    return order


def _existing_files(project_dir: Path) -> dict[str, str]:
    """Read scripts/ and scenes/ from disk: relative path -> content."""
    files: dict[str, str] = {}
    for root in ALLOWED_ROOTS:
        base = project_dir / root
        if not base.is_dir():
            continue
        for item in sorted(base.rglob("*")):
            if item.is_file() and item.suffix in ALLOWED_EXTS:
                files[item.relative_to(project_dir).as_posix()] = item.read_text(encoding="utf-8")
    return files


# --------------------------------------------------------------------------- agent
_ROLE = """You are the Godot Lead Programmer. You write clean, runnable Godot 4 GDScript 2.0 with static typing.

HARD RULES
- Godot 4 syntax only: @export, @onready, await, signal.connect(callable), signal.emit(),
  CharacterBody2D with the `velocity` property and move_and_slide() (no arguments),
  PackedScene.instantiate(), create_tween(), randf_range()/randi_range(),
  get_tree().change_scene_to_file(), get_tree().reload_current_scene().
- Never use Godot 3 API: onready/export/yield/setget/tool keywords, KinematicBody*, instance(),
  rand_range, change_scene, Pool*Array, connect("sig", obj, "method").
- Static typing everywhere: typed variables and parameters, and an explicit return type on EVERY
  function (-> void included). When the value is a Variant (Dictionary/Array element access,
  get_node results, load() results) write the type explicitly instead of using ':='.
- Indent with TABS only.
- Use only classes, methods and properties you are certain exist in Godot 4; otherwise choose a
  simpler documented alternative. Avoid TileMap/TileSet, shaders, GDExtension, C#, @tool scripts.
- Prefer building child nodes in code inside _ready() (CollisionShape2D + RectangleShape2D,
  Sprite2D, Timer, AudioStreamPlayer, CPUParticles2D) so .tscn files stay tiny: a root node plus a
  script is enough.
- .tscn rules: first line `[gd_scene load_steps=N format=3]` with N = number of [ext_resource] +
  [sub_resource] entries + 1 (omit load_steps when there are none); declare every id you use;
  scripts/scenes referenced must be files of the plan.
- Assets: load textures and sounds only from the ASSET MANIFEST paths. An animated sprite is a
  horizontal strip: Sprite2D with hframes = frames. A missing asset must never crash the game:
  check ResourceLoader.exists(path) and fall back to drawing a coloured rectangle in _draw().
- Music looping: connect the player's `finished` signal to its play method.
- Implement ALL the design rules: visible goal and loss condition on screen, game juice (camera
  shake through Camera2D offset, CPUParticles2D bursts, sound effects, tweens), the risk/reward
  decision, the difficulty presets and level progression from the level plan, and instant retry
  (reset state in place, no scene change, no loading screen).
- Input actions are created in project.godot by the tool; use the exact action names provided.
- Keep every script under about 250 lines. Small, working and complete beats ambitious."""


class GodotProgrammerAgent(BaseAgent):
    """Builds the project (mode='build') or applies reviewer changes (mode='refine').

    Also exposes apply_fixes() which the QA agent uses for its fix loop.
    """

    role = _ROLE
    inject_design_rules = True

    def __init__(self, mode: Literal["build", "refine"] = "build") -> None:
        if mode not in ("build", "refine"):
            raise ValueError(f"unknown mode: {mode!r}")
        self.mode = mode
        self.name = "programmer_refine" if mode == "refine" else "programmer"

    # ---- public API ------------------------------------------------------
    def run(self, ctx: ProjectContext) -> dict[str, Any]:
        if self.mode == "refine":
            return self._run_refine(ctx)
        return self._run_build(ctx)

    def apply_fixes(self, ctx: ProjectContext, issues: list[dict[str, Any]]) -> dict[str, Any]:
        """Rewrite files named in `issues` (dicts with 'file' and 'message') to fix them."""
        grouped: dict[str, list[str]] = {}
        skipped: list[str] = []
        for issue in issues:
            rel = self._issue_path(ctx, str(issue.get("file", "")))
            message = str(issue.get("message", "")).strip()
            if rel is None or not message:
                skipped.append(f"{issue.get('file')}: {message}")
                continue
            line = issue.get("line")
            where = f" (line {line})" if line else ""
            grouped.setdefault(rel, []).append(f"{message}{where}")
        result = self._rewrite_files(ctx, grouped)
        result["skipped_issues"] = skipped
        return result

    # ---- build -----------------------------------------------------------
    def _digest(self, ctx: ProjectContext) -> str:
        art = ctx.get_artifact("art", {}) or {}
        audio = ctx.get_artifact("audio", {}) or {}
        digest = {
            "design": ctx.get_artifact("game_designer", {}),
            "levels": ctx.get_artifact("level_designer", {}),
            "palette": art.get("palette", []),
            "clear_color": art.get("clear_color"),
            "asset_manifest": build_manifest(art, audio),
        }
        return json.dumps(digest, ensure_ascii=False)

    def _merge_controls(self, plan: BuildPlan, design: dict[str, Any]) -> BuildPlan:
        """Make sure every control action from the design exists as an input action."""
        existing = {a.name for a in plan.input_actions}
        extra: list[InputAction] = []
        for control in design.get("controls", []):
            action, keys = control.get("action"), control.get("keys")
            if action and keys and action not in existing:
                try:
                    extra.append(InputAction(name=action, keys=keys))
                except ValueError as exc:
                    logger.warning("Skipping invalid control %r: %s", action, exc)
        if not extra:
            return plan
        return plan.model_copy(update={"input_actions": [*plan.input_actions, *extra]})

    def _run_build(self, ctx: ProjectContext) -> dict[str, Any]:
        digest = self._digest(ctx)
        plan_prompt = (
            f"Game idea:\n{ctx.idea}\n\nPROJECT SPEC (json):\n{digest}\n\n"
            "Plan the Godot project: at most 14 files under scripts/ and scenes/ "
            "(.gd, .tscn, .tres). Give each a path and purpose, order dependencies with depends_on, "
            "choose main_scene (a .tscn), optional autoload singletons (scripts), and ALL input "
            "actions the game uses (use the control action names from the design). Keep it small."
        )
        plan = self.ask_json(ctx, plan_prompt, BuildPlan)
        plan = self._merge_controls(plan, ctx.get_artifact("game_designer", {}) or {})

        known = {f.path for f in plan.files}
        system = self.system_prompt(ctx)
        plan_lines = "\n".join(f"- {f.path}: {f.purpose}" for f in plan.files)
        actions = ", ".join(a.name for a in plan.input_actions)
        written: dict[str, str] = {}
        warnings: list[str] = []

        for planned in _ordered(plan.files):
            outlines = "\n\n".join(
                f"### {p}\n{interface_summary(p, c)}" for p, c in written.items()
            ) or "(none yet)"
            user = (
                f"TARGET FILE: {planned.path}\nPURPOSE: {planned.purpose}\n"
                f"DEPENDS ON: {', '.join(planned.depends_on) or 'nothing'}\n\n"
                f"PROJECT PLAN (all files):\n{plan_lines}\n"
                f"MAIN SCENE: {plan.main_scene}\n"
                f"AUTOLOADS: {json.dumps(plan.autoloads)}\n"
                f"INPUT ACTIONS (exact names): {actions}\n\n"
                f"PROJECT SPEC (json):\n{digest}\n\n"
                f"ALREADY WRITTEN FILES (outlines):\n{outlines}\n\n"
                f"Write the COMPLETE content of {planned.path}. "
                "Reply with ONE fenced code block and nothing else."
            )
            code, problems = self._generate(ctx, system, user, planned.path, known)
            if problems:
                warnings.extend(f"{planned.path}: {p}" for p in problems)
                logger.warning("%s kept with %d unresolved problem(s)", planned.path, len(problems))
            safe_write(ctx.project_dir, planned.path, code, ALLOWED_EXTS)
            written[planned.path] = code

        settings = {
            "main_scene": plan.main_scene,
            "autoloads": plan.autoloads,
            "input_actions": [a.model_dump() for a in plan.input_actions],
            "window_width": plan.window_width,
            "window_height": plan.window_height,
        }
        project_info = write_project_files(ctx, settings)
        warnings.extend(project_info["warnings"])
        return {
            "files": sorted(written),
            "plan": [{"path": f.path, "purpose": f.purpose} for f in plan.files],
            "project_settings": settings,
            "project_files": project_info["files"],
            "godot_version": project_info["godot_version"],
            "warnings": warnings,
            "notes": plan.notes,
        }

    # ---- refine / fixes --------------------------------------------------
    def _run_refine(self, ctx: ProjectContext) -> dict[str, Any]:
        aggregator = ctx.get_artifact("aggregator") or {}
        changes = aggregator.get("priority_changes") if isinstance(aggregator, dict) else None
        if not isinstance(changes, list) or not changes:
            return {"applied": [], "not_applied": [], "note": "no priority_changes from aggregator"}

        existing = _existing_files(ctx.project_dir)
        outlines = "\n\n".join(f"### {p}\n{interface_summary(p, c)}" for p, c in existing.items())
        user = (
            f"REVIEWER CHANGES (highest priority first):\n{json.dumps(changes, ensure_ascii=False)}\n\n"
            f"EXISTING FILES (outlines):\n{outlines}\n\n"
            "Decide which existing files must change (at most 10) and give precise instructions per "
            "file. Do not add new files. Skip changes that are impossible without new assets."
        )
        refine = self.ask_json(ctx, user, RefinePlan)
        grouped: dict[str, list[str]] = {}
        for edit in refine.edits:
            grouped.setdefault(edit.path, []).append(edit.instructions)
        result = self._rewrite_files(ctx, grouped)
        result["requested_changes"] = [str(c) for c in changes]
        return result

    def _rewrite_files(
        self, ctx: ProjectContext, instructions: dict[str, list[str]]
    ) -> dict[str, Any]:
        """Rewrite existing files per instructions; keep the original if the result is invalid."""
        existing = _existing_files(ctx.project_dir)
        known = set(existing)
        system = self.system_prompt(ctx)
        applied: list[str] = []
        not_applied: list[dict[str, Any]] = []
        for path, items in instructions.items():
            if path not in existing:
                not_applied.append({"path": path, "reason": "file does not exist in the project"})
                continue
            outlines = "\n\n".join(
                f"### {p}\n{interface_summary(p, c)}" for p, c in existing.items() if p != path
            )
            user = (
                f"TARGET FILE: {path}\n\nCURRENT CONTENT:\n```\n{existing[path]}```\n\n"
                "REQUIRED CHANGES:\n- " + "\n- ".join(items) + "\n\n"
                f"OTHER FILES (outlines):\n{outlines or '(none)'}\n\n"
                f"Return the COMPLETE updated {path} in ONE fenced code block and nothing else. "
                "Keep everything that already works; change only what is required."
            )
            try:
                code, problems = self._generate(ctx, system, user, path, known)
            except AgentError as exc:
                not_applied.append({"path": path, "reason": str(exc)})
                continue
            if problems:
                not_applied.append({"path": path, "reason": "; ".join(problems)})
                continue
            safe_write(ctx.project_dir, path, code, ALLOWED_EXTS)
            existing[path] = code
            applied.append(path)
        return {"applied": applied, "not_applied": not_applied}

    # ---- helpers ---------------------------------------------------------
    def _issue_path(self, ctx: ProjectContext, raw: str) -> str | None:
        """Turn 'res://x', 'x' or an absolute path inside the project into a clean relative path."""
        text = raw.strip()
        if not text:
            return None
        candidate = Path(text)
        try:
            if candidate.is_absolute():
                text = candidate.resolve().relative_to(ctx.project_dir.resolve()).as_posix()
            return validate_project_path(text, ALLOWED_ROOTS, ALLOWED_EXTS)
        except (ValueError, UnsafePathError):
            return None

    def _generate(
        self, ctx: ProjectContext, system: str, user: str, path: str, known: set[str]
    ) -> tuple[str, list[str]]:
        """Request a file, run checks, and ask for repairs up to MAX_REPAIRS times."""
        prompt = user
        code, problems = "", []
        for attempt in range(MAX_REPAIRS + 1):
            code = self._request_code(ctx, system, prompt)
            code, problems = check_file(path, code, known)
            if not problems:
                return code, []
            logger.warning("%s attempt %d/%d has problems: %s",
                           path, attempt + 1, MAX_REPAIRS + 1, "; ".join(problems))
            prompt = (
                f"{user}\n\nYOUR PREVIOUS VERSION:\n```\n{code}```\n"
                "It has these problems:\n- " + "\n- ".join(problems) + "\n"
                f"Return the COMPLETE corrected {path} in ONE fenced code block and nothing else."
            )
        return code, problems

    def _request_code(self, ctx: ProjectContext, system: str, user: str) -> str:
        """One code request; retries once when the reply was truncated or malformed."""
        prompt = user
        for _ in range(2):
            reply = ctx.llm.complete(system, prompt)
            if getattr(ctx.llm, "last_stop_reason", None) != "max_tokens":
                try:
                    return extract_code_block(reply)
                except ValueError as exc:
                    logger.warning("Unusable code reply: %s", exc)
            prompt = (
                f"{user}\n\nYour previous reply was cut off or malformed. Return the COMPLETE file "
                "in ONE closed fenced code block, shorter and simpler than before."
            )
        raise AgentError("could not obtain a complete code block from the model")
