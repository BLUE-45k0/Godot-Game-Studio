"""Optional download of CC0 asset packs (URLs come from config) with a safe fallback."""
from __future__ import annotations

import contextlib
import io
import logging
import re
import shutil
import threading
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

import requests
from PIL import Image

from agents.asset_manifest import sfx_path, sprite_path
from core.config import AssetsConfig
from core.safe_files import resolve_inside
from core.schemas import ProjectContext

logger = logging.getLogger(__name__)

RESAMPLING = getattr(Image, "Resampling", Image)
MAX_DOWNLOAD_BYTES = 150 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 400 * 1024 * 1024
MAX_FILES = 5000
EXTRACT_EXTS = {".png", ".wav"}  # OGG is skipped: we cannot convert it to the WAV paths we promise
_FETCH_LOCK = threading.Lock()


class AssetFetchError(RuntimeError):
    """Raised for unsafe or oversized downloads."""


@dataclass
class FetchResult:
    files: list[str] = field(default_factory=list)  # project-relative POSIX paths
    failed: list[str] = field(default_factory=list)
    used_urls: list[str] = field(default_factory=list)


def _download(url: str, timeout: float) -> bytes:
    deadline = time.monotonic() + max(timeout * 6, 60.0)
    with contextlib.closing(requests.get(url, stream=True, timeout=timeout)) as resp:
        resp.raise_for_status()
        declared = resp.headers.get("Content-Length")
        if declared and declared.isdigit() and int(declared) > MAX_DOWNLOAD_BYTES:
            raise AssetFetchError(f"download too large ({declared} bytes)")
        buffer = io.BytesIO()
        for chunk in resp.iter_content(chunk_size=65536):
            buffer.write(chunk)
            if buffer.tell() > MAX_DOWNLOAD_BYTES:
                raise AssetFetchError("download exceeded the size limit")
            if time.monotonic() > deadline:
                raise AssetFetchError("download took too long")
        return buffer.getvalue()


def _extract(data: bytes, pack_dir: Path, project_dir: Path) -> list[str]:
    """Extract png/wav files safely (no path escape, bounded size/count)."""
    pack_dir.mkdir(parents=True, exist_ok=True)
    pack_root = pack_dir.resolve()
    project_root = project_dir.resolve()
    extracted: list[str] = []
    total = 0
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            rel = PurePosixPath(info.filename.replace("\\", "/"))
            if rel.is_absolute() or ".." in rel.parts:
                logger.warning("Skipping unsafe zip entry: %s", info.filename)
                continue
            if rel.suffix.lower() not in EXTRACT_EXTS:
                continue
            total += info.file_size
            if total > MAX_UNCOMPRESSED_BYTES or len(extracted) >= MAX_FILES:
                raise AssetFetchError("archive exceeds extraction limits")
            target = (pack_root / rel.as_posix()).resolve()
            try:
                target.relative_to(pack_root)
            except ValueError:
                logger.warning("Skipping zip entry that escapes the pack folder: %s", info.filename)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            extracted.append(target.relative_to(project_root).as_posix())
    return extracted


def fetch_packs(cfg: AssetsConfig, project_dir: Path) -> FetchResult:
    """Download and extract every configured pack; failures are recorded, never raised."""
    result = FetchResult()
    dest_root = project_dir / "assets" / "kenney"
    for index, url in enumerate(cfg.kenney_pack_urls, start=1):
        pack_dir = dest_root / f"pack_{index}"
        try:
            files = _extract(_download(url, cfg.download_timeout_sec), pack_dir, project_dir)
        except (requests.RequestException, zipfile.BadZipFile, AssetFetchError, OSError) as exc:
            logger.warning("Pack download failed (%s): %s: %s", url, type(exc).__name__, exc)
            result.failed.append(f"{url}: {type(exc).__name__}: {exc}")
            shutil.rmtree(pack_dir, ignore_errors=True)
            continue
        result.files.extend(files)
        result.used_urls.append(url)
    if result.used_urls:
        credits = (
            "Asset packs downloaded from URLs set in config.toml (expected: Kenney, CC0).\n"
            "Verify the license of every pack you add.\n\n" + "\n".join(result.used_urls) + "\n"
        )
        dest_root.mkdir(parents=True, exist_ok=True)
        (dest_root / "CREDITS.txt").write_text(credits, encoding="utf-8")
    return result


def ensure_kenney(ctx: ProjectContext) -> dict[str, Any]:
    """Fetch packs once per run (art and audio agents run in parallel). Cached as an artifact."""
    with _FETCH_LOCK:
        cached = ctx.get_artifact("kenney_fetch")
        if cached is not None:
            return cached
        cfg = ctx.config.assets
        if cfg.prefer_kenney and cfg.kenney_pack_urls:
            res = fetch_packs(cfg, ctx.project_dir)
            data = {"files": res.files, "failed": res.failed, "urls": res.used_urls}
        else:
            logger.info("No Kenney packs configured; using procedural assets only.")
            data = {"files": [], "failed": [], "urls": []}
        ctx.set_artifact("kenney_fetch", data)
        return data


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", text.lower()) if t]


def _best_match(name: str, files: list[str]) -> str | None:
    """Conservative match: every word of `name` must appear in the file's name."""
    wanted = set(_tokens(name))
    if not wanted:
        return None
    candidates = [(len(PurePosixPath(f).stem), f) for f in files
                  if wanted <= set(_tokens(PurePosixPath(f).stem))]
    return min(candidates)[1] if candidates else None


def _fit_into(img: Image.Image, w: int, h: int, pixel: bool) -> Image.Image:
    """Scale to fit inside w x h keeping aspect ratio, centered on a transparent canvas."""
    img = img.convert("RGBA")
    scale = min(w / img.width, h / img.height)
    nw, nh = max(1, round(img.width * scale)), max(1, round(img.height * scale))
    resized = img.resize((nw, nh), RESAMPLING.NEAREST if pixel else RESAMPLING.LANCZOS)
    canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    canvas.paste(resized, ((w - nw) // 2, (h - nh) // 2))
    return canvas


def apply_kenney_assets(
    sprite_specs: list[dict[str, Any]],
    sfx_specs: list[dict[str, Any]],
    kenney: dict[str, Any],
    project_dir: Path,
) -> dict[str, list[str]]:
    """Copy matching downloaded files to the standard asset paths. Returns matched names."""
    files: list[str] = kenney.get("files", [])
    pngs = [f for f in files if f.lower().endswith(".png")]
    wavs = [f for f in files if f.lower().endswith(".wav")]
    matched: dict[str, list[str]] = {"sprites": [], "sfx": []}

    for spec in sprite_specs:
        if spec["frames"] != 1 or spec["category"] in ("background", "particle"):
            continue
        best = _best_match(spec["name"], pngs)
        if best is None:
            continue
        try:
            with Image.open(project_dir / best) as source:
                fitted = _fit_into(source, spec["width"], spec["height"], spec["style"] == "pixel")
            target = resolve_inside(project_dir, sprite_path(spec["name"]))
            target.parent.mkdir(parents=True, exist_ok=True)
            fitted.save(target, format="PNG")
            matched["sprites"].append(spec["name"])
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            logger.warning("Could not use %s for sprite '%s': %s", best, spec["name"], exc)

    for spec in sfx_specs:
        best = _best_match(spec["name"], wavs)
        if best is None:
            continue
        try:
            target = resolve_inside(project_dir, sfx_path(spec["name"]))
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(project_dir / best, target)
            matched["sfx"].append(spec["name"])
        except OSError as exc:
            logger.warning("Could not use %s for sfx '%s': %s", best, spec["name"], exc)
    return matched
