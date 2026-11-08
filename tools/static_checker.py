"""Static checks for a generated Godot 4 project (works without the engine)."""
from __future__ import annotations

import logging
import re
from pathlib import Path

from agents.godot_programmer import validate_gd
from core.schemas import QAIssue

logger = logging.getLogger(__name__)

SCAN_ROOTS: tuple[str, ...] = ("scripts", "scenes")

_RES_RE = re.compile(r"res://([^\"'\n]+)")
_SECTION_RE = re.compile(r"^\[(\w+)\s*(.*)\]\s*$")
_ATTR_RE = re.compile(r'(\w+)=(?:"((?:[^"\\]|\\.)*)"|(\S+))')
_STRING_RE = re.compile(r'"(?:[^"\\\n]|\\.)*"|\'(?:[^\'\\\n]|\\.)*\'')
_EXT_REF_RE = re.compile(r'ExtResource\(\s*"([^"]+)"\s*\)')
_SUB_REF_RE = re.compile(r'SubResource\(\s*"([^"]+)"\s*\)')
_SCRIPT_PROP_RE = re.compile(r'^script\s*=\s*ExtResource\(\s*"([^"]+)"\s*\)')
_FUNC_DEF_RE = re.compile(r"^\s*(?:static\s+)?func\s+(\w+)\s*\(", re.M)
_VAR_DEF_RE = re.compile(r"^\s*(?:@\w+(?:\([^)]*\))?\s+)*(?:static\s+)?(?:var|const)\s+(\w+)", re.M)
_CLASS_NAME_RE = re.compile(r"^\s*class_name\s+(\w+)", re.M)
_SIGNAL_DECL_RE = re.compile(r"^\s*signal\s+(\w+)", re.M)
_EMIT_RE = re.compile(r"(\w+)\.emit\(")
_EMIT_SIGNAL_RE = re.compile(r'emit_signal\(\s*"(\w+)"')
_CONNECT_RE = re.compile(r"(\w+)\.connect\(")
_HANDLER_RE = re.compile(r"(\w+)\.connect\(\s*([A-Za-z_]\w*)\s*[,)]")
_ACTION_RE = re.compile(
    r'\b(?:is_action_pressed|is_action_just_pressed|is_action_just_released|'
    r'is_action_released|get_action_strength|is_action)\(\s*"([^"]+)"'
)
_AXIS_RE = re.compile(r"\bget_(?:axis|vector)\(([^)]*)\)")


def _attrs(rest: str) -> dict[str, str]:
    """Parse key=value attributes of a .tscn section header."""
    out: dict[str, str] = {}
    for match in _ATTR_RE.finditer(rest):
        out[match.group(1)] = match.group(2) if match.group(2) is not None else match.group(3)
    return out


def _under(path: str, instanced: set[str]) -> bool:
    """True if `path` is an instanced scene or lives inside one (its children are not in this file)."""
    return any(path == p or path.startswith(p + "/") for p in instanced)


class _Checker:
    """Collects issues over the whole project (project-wide facts are gathered first)."""

    def __init__(self, project_dir: Path) -> None:
        self.root = project_dir.resolve()
        self.issues: list[QAIssue] = []
        self.actions: set[str] | None = None
        self.autoloads: dict[str, str] = {}
        self.script_funcs: dict[str, set[str]] = {}
        self.declared_signals: list[tuple[str, str, int]] = []
        self.emitted: set[str] = set()
        self.connected: set[str] = set()
        self.class_names: dict[str, str] = {}

    # ---- helpers ---------------------------------------------------------
    def add(self, severity: str, file: str, message: str, line: int | None = None) -> None:
        self.issues.append(
            QAIssue(severity=severity, source="static", file=file, line=line, message=message)  # type: ignore[arg-type]
        )

    def _exists(self, rel: str) -> bool:
        try:
            target = (self.root / rel).resolve()
            target.relative_to(self.root)
        except (ValueError, OSError):
            return False
        return target.exists()

    @staticmethod
    def _read(path: Path) -> str:
        return path.read_text(encoding="utf-8", errors="replace")

    # ---- entry point -----------------------------------------------------
    def run(self) -> list[QAIssue]:
        self._check_project_file()
        scripts: dict[str, str] = {}
        scenes: dict[str, str] = {}
        for top in SCAN_ROOTS:
            base = self.root / top
            if not base.is_dir():
                continue
            for item in sorted(base.rglob("*")):
                if not item.is_file():
                    continue
                rel = item.relative_to(self.root).as_posix()
                if item.suffix == ".gd":
                    scripts[rel] = self._read(item)
                elif item.suffix in (".tscn", ".tres"):
                    scenes[rel] = self._read(item)
        for rel, text in scripts.items():
            self.script_funcs[rel] = set(_FUNC_DEF_RE.findall(text))
        for rel, text in scripts.items():
            self._check_script(rel, text)
        for rel, text in scenes.items():
            self._check_scene(rel, text)
        self._check_signals()
        self._check_class_names()
        return sorted(
            self.issues,
            key=lambda i: (0 if i.severity == "error" else 1, i.file, i.line or 0, i.message),
        )

    # ---- project.godot ---------------------------------------------------
    def _check_project_file(self) -> None:
        path = self.root / "project.godot"
        if not path.is_file():
            self.add("error", "project.godot", "project.godot is missing")
            return
        section = ""
        version: str | None = None
        main_scene: tuple[int, str] | None = None
        actions: set[str] = set()
        autoload_lines: dict[str, int] = {}
        for no, raw in enumerate(self._read(path).splitlines(), 1):
            line = raw.strip()
            if not line or line.startswith(";"):
                continue
            header = re.match(r"^\[([^\]]+)\]$", line)
            if header:
                section = header.group(1)
                continue
            if section == "" and line.startswith("config_version="):
                version = line.split("=", 1)[1].strip()
            elif section == "application" and line.startswith("run/main_scene="):
                main_scene = (no, line.split("=", 1)[1].strip().strip('"'))
            elif section == "autoload":
                match = re.match(r'^(\w+)\s*=\s*"\*?([^"]*)"', line)
                if match:
                    target = match.group(2)
                    self.autoloads[match.group(1)] = target[6:] if target.startswith("res://") else target
                    autoload_lines[match.group(1)] = no
            elif section == "input":
                match = re.match(r"^(\w+)\s*=\s*\{", line)
                if match:
                    actions.add(match.group(1))
        self.actions = actions
        if version != "5":
            self.add("error", "project.godot", f"config_version must be 5 for Godot 4 (found {version})")
        if main_scene is None or not main_scene[1]:
            self.add("error", "project.godot", "run/main_scene is not set")
        else:
            no, value = main_scene
            rel = value[6:] if value.startswith("res://") else value
            if not self._exists(rel):
                self.add("error", "project.godot", f"main scene {value} does not exist", no)
        for name, rel in self.autoloads.items():
            if not self._exists(rel):
                self.add("error", "project.godot", f"autoload '{name}' points to missing file {rel}",
                         autoload_lines.get(name))

    # ---- GDScript --------------------------------------------------------
    def _check_script(self, rel: str, text: str) -> None:
        for message in validate_gd(text):
            soft = "return type" in message or "must declare 'extends'" in message
            self.add("warning" if soft else "error", rel, message)

        lines = text.splitlines()
        self._check_indentation(rel, lines)
        funcs = self.script_funcs.get(rel, set())
        variables = set(_VAR_DEF_RE.findall(text))

        for no, line in enumerate(lines, 1):
            if line.lstrip().startswith("#"):
                continue
            self._check_res_refs(rel, no, line)
            for match in _ACTION_RE.finditer(line):
                self._check_action(rel, no, match.group(1))
            for match in _AXIS_RE.finditer(line):
                for name in re.findall(r'"([^"]+)"', match.group(1)):
                    self._check_action(rel, no, name)

        for match in _SIGNAL_DECL_RE.finditer(text):
            self.declared_signals.append((match.group(1), rel, text.count("\n", 0, match.start(1)) + 1))
        self.emitted.update(_EMIT_RE.findall(text))
        self.emitted.update(_EMIT_SIGNAL_RE.findall(text))
        self.connected.update(_CONNECT_RE.findall(text))

        for match in _HANDLER_RE.finditer(text):
            handler = match.group(2)
            if handler not in funcs and handler not in variables and handler != "self":
                self.add(
                    "warning", rel,
                    f"connect() handler '{handler}' is not defined in this script",
                    text.count("\n", 0, match.start()) + 1,
                )

        class_match = _CLASS_NAME_RE.search(text)
        if class_match:
            name = class_match.group(1)
            if name in self.class_names:
                self.add("error", rel, f"class_name '{name}' is already used by {self.class_names[name]}")
            else:
                self.class_names[name] = rel

    def _check_action(self, rel: str, no: int, name: str) -> None:
        if self.actions is None or name.startswith("ui_"):
            return
        if name not in self.actions:
            self.add("error", rel, f"input action '{name}' is used but not defined in project.godot", no)

    def _check_res_refs(self, rel: str, no: int, line: str) -> None:
        guarded = "exists(" in line
        for match in _RES_RE.finditer(line):
            target = match.group(1).strip()
            if not target or target.endswith("/") or any(c in target for c in "%{*"):
                continue
            if self._exists(target) or guarded:
                continue
            if target.startswith(("scripts/", "scenes/")) or target.endswith((".gd", ".tscn", ".tres")):
                self.add("error", rel, f"references missing file res://{target}", no)
            else:
                self.add(
                    "warning", rel,
                    f"references res://{target}, which does not exist (guard it with ResourceLoader.exists)",
                    no,
                )

    def _check_indentation(self, rel: str, lines: list[str]) -> None:
        """Flag files whose block indentation mixes tabs and spaces."""
        depth = 0
        continued = False
        first: dict[str, int] = {}
        for no, raw in enumerate(lines, 1):
            code = _STRING_RE.sub('""', raw).split("#", 1)[0]
            if code.strip() and not continued and depth == 0 and raw[:1] in (" ", "\t"):
                first.setdefault("tab" if raw[0] == "\t" else "space", no)
            opened = sum(code.count(c) for c in "([{")
            closed = sum(code.count(c) for c in ")]}")
            depth = max(0, depth + opened - closed)
            continued = code.rstrip().endswith("\\")
        if len(first) == 2:
            self.add("error", rel, "mixed tab and space indentation (use tabs only)", max(first.values()))

    # ---- .tscn / .tres ---------------------------------------------------
    def _check_scene(self, rel: str, text: str) -> None:
        lines = text.splitlines()
        first_no, first = next(((n, s.strip()) for n, s in enumerate(lines, 1) if s.strip()), (1, ""))
        header = _SECTION_RE.match(first)
        if header is None or header.group(1) not in ("gd_scene", "gd_resource"):
            self.add("error", rel, "file must start with a [gd_scene ...] or [gd_resource ...] header", first_no)
            return
        is_scene = header.group(1) == "gd_scene"
        head = _attrs(header.group(2))
        if head.get("format") != "3":
            self.add("error", rel, "header must contain format=3 (Godot 4)", first_no)

        ext_ids: set[str] = set()
        ext_paths: dict[str, str] = {}
        sub_ids: set[str] = set()
        n_ext = n_sub = roots = 0
        nodes: set[str] = set()
        instanced: set[str] = set()
        node_script: dict[str, str] = {}
        connections: list[tuple[int, dict[str, str]]] = []
        current: str | None = None

        for no, raw in enumerate(lines, 1):
            line = raw.strip()
            section = _SECTION_RE.match(line)
            if section is None:
                if current is not None:
                    prop = _SCRIPT_PROP_RE.match(line)
                    if prop and prop.group(1) in ext_paths:
                        node_script[current] = ext_paths[prop.group(1)]
                continue
            kind, attrs = section.group(1), _attrs(section.group(2))
            if kind == "node":
                name, parent = attrs.get("name", ""), attrs.get("parent")
                if parent is None:
                    roots += 1
                    if roots > 1:
                        self.add("error", rel, "scene has more than one root node", no)
                    path = "."
                else:
                    if "." not in nodes:
                        self.add("error", rel, f"node '{name}' appears before the root node", no)
                    elif parent != "." and parent not in nodes and not _under(parent, instanced):
                        self.add("error", rel, f"node '{name}' has parent '{parent}' which is not declared before it", no)
                    path = name if parent == "." else f"{parent}/{name}"
                    if path in nodes:
                        self.add("error", rel, f"duplicate node path '{path}'", no)
                nodes.add(path)
                if "instance" in attrs:
                    instanced.add(path)
                current = path
                continue
            current = None
            if kind == "ext_resource":
                n_ext += 1
                rid, rpath = attrs.get("id", ""), attrs.get("path", "")
                ext_ids.add(rid)
                if rpath.startswith("res://"):
                    target = rpath[6:]
                    ext_paths[rid] = target
                    if not self._exists(target):
                        self.add("error", rel, f"ext_resource points to missing file {rpath}", no)
            elif kind == "sub_resource":
                n_sub += 1
                sub_ids.add(attrs.get("id", ""))
            elif kind == "connection":
                connections.append((no, attrs))

        declared = head.get("load_steps")
        expected = n_ext + n_sub + 1
        if declared is not None:
            try:
                value = int(declared)
            except ValueError:
                value = -1
            if value != expected:
                self.add("warning", rel,
                         f"load_steps={declared} does not match the file's resources (expected {expected})",
                         first_no)

        for no, raw in enumerate(lines, 1):
            for match in _EXT_REF_RE.finditer(raw):
                if match.group(1) not in ext_ids:
                    self.add("error", rel, f'ExtResource("{match.group(1)}") is used but never declared', no)
            for match in _SUB_REF_RE.finditer(raw):
                if match.group(1) not in sub_ids:
                    self.add("error", rel, f'SubResource("{match.group(1)}") is used but never declared', no)

        if is_scene and roots == 0:
            self.add("error", rel, "scene has no root node", first_no)

        for no, attrs in connections:
            signal, src, dst, method = (attrs.get(k, "") for k in ("signal", "from", "to", "method"))
            for label, end in (("from", src), ("to", dst)):
                if end not in nodes and not _under(end, instanced):
                    self.add("error", rel, f"connection {label}='{end}' is not a node of this scene", no)
            if signal:
                self.connected.add(signal)
            script = node_script.get(dst)
            if script and method:
                funcs = self.script_funcs.get(script)
                if funcs is not None and method not in funcs:
                    self.add("error", rel, f"connection method '{method}' is not defined in {script}", no)

    # ---- project-wide ----------------------------------------------------
    def _check_signals(self) -> None:
        for name, rel, line in self.declared_signals:
            if name not in self.emitted:
                self.add("warning", rel, f"signal '{name}' is declared but never emitted", line)
            elif name not in self.connected:
                self.add("warning", rel, f"signal '{name}' is emitted but never connected", line)

    def _check_class_names(self) -> None:
        for name, rel in self.class_names.items():
            if name in self.autoloads:
                self.add("error", rel, f"class_name '{name}' conflicts with the autoload singleton '{name}'")


def run_static_checks(project_dir: Path) -> list[QAIssue]:
    """Run every static check and return the issues (errors first)."""
    return _Checker(project_dir).run()


def check_expected_assets(project_dir: Path, res_paths: list[str]) -> list[QAIssue]:
    """Warn about planned asset files that do not exist on disk."""
    root = project_dir.resolve()
    issues: list[QAIssue] = []
    for res in res_paths:
        rel = res[len("res://"):] if res.startswith("res://") else res
        if not (root / rel).is_file():
            issues.append(QAIssue(
                severity="warning", source="static", file=rel,
                message="planned asset file is missing (the code must fall back gracefully)",
            ))
    return issues
