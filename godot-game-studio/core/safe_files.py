"""Safe file writing inside a project directory."""
from __future__ import annotations

import logging
from pathlib import Path, PurePosixPath

logger = logging.getLogger(__name__)

MAX_FILE_BYTES = 500_000


class UnsafePathError(ValueError):
    """Raised for paths that escape the project or are otherwise not allowed."""


def clean_rel_path(rel_path: str) -> str:
    """Normalise 'res://a/b.gd' or 'a/b.gd' to a safe relative POSIX path."""
    text = rel_path.strip()
    if text.startswith("res://"):
        text = text[len("res://"):]
    if not text or "\\" in text or "\x00" in text:
        raise UnsafePathError(f"invalid path: {rel_path!r}")
    posix = PurePosixPath(text)
    if posix.is_absolute() or ".." in posix.parts:
        raise UnsafePathError(f"path must be relative and inside the project: {rel_path!r}")
    return posix.as_posix()


def validate_project_path(
    rel_path: str, allowed_roots: tuple[str, ...], allowed_exts: tuple[str, ...]
) -> str:
    """Clean a path and require an allowed top folder and extension."""
    cleaned = clean_rel_path(rel_path)
    parts = PurePosixPath(cleaned).parts
    if len(parts) < 2 or parts[0] not in allowed_roots:
        raise UnsafePathError(f"{cleaned!r} must be inside one of {list(allowed_roots)}")
    if PurePosixPath(cleaned).suffix not in allowed_exts:
        raise UnsafePathError(f"{cleaned!r} must end with one of {list(allowed_exts)}")
    return cleaned


def resolve_inside(project_dir: Path, rel_path: str) -> Path:
    """Resolve rel_path under project_dir, rejecting symlink/.. escapes."""
    root = project_dir.resolve()
    target = (root / clean_rel_path(rel_path)).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise UnsafePathError(f"path escapes the project: {rel_path!r}") from exc
    return target


def safe_write(
    project_dir: Path, rel_path: str, content: str, allowed_exts: tuple[str, ...]
) -> Path:
    """Write text (LF newlines, trailing newline) to a validated path in the project."""
    target = resolve_inside(project_dir, rel_path)
    if target.suffix not in allowed_exts:
        raise UnsafePathError(f"extension {target.suffix!r} not allowed for {rel_path!r}")
    data = content.replace("\r\n", "\n").replace("\r", "\n")
    if not data.endswith("\n"):
        data += "\n"
    if len(data.encode("utf-8")) > MAX_FILE_BYTES:
        raise UnsafePathError(f"{rel_path!r} exceeds {MAX_FILE_BYTES} bytes")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(data, encoding="utf-8", newline="\n")
    logger.debug("Wrote %s", target)
    return target
