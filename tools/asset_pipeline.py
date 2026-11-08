"""Glue used by the art and audio agents: Kenney first, procedural for the rest."""
from __future__ import annotations

import logging
import zlib
from typing import Any

from core.schemas import ProjectContext
from tools.asset_fetcher import apply_kenney_assets, ensure_kenney
from tools.sfx_gen import generate_audio
from tools.sprite_gen import generate_sprites

logger = logging.getLogger(__name__)


class AssetPipelineError(RuntimeError):
    """Raised when no asset at all could be produced."""


def _seed(ctx: ProjectContext) -> int:
    return zlib.crc32(ctx.idea.encode("utf-8"))


def build_art_assets(ctx: ProjectContext, sprites: list[dict[str, Any]]) -> dict[str, Any]:
    """Place sprite files in assets/sprites/. Returns a JSON-serialisable summary."""
    kenney = ensure_kenney(ctx)
    matched = apply_kenney_assets(sprites, [], kenney, ctx.project_dir)["sprites"]
    remaining = [s for s in sprites if s["name"] not in set(matched)]
    written, failed = generate_sprites(remaining, ctx.project_dir, _seed(ctx))
    if not written and not matched:
        raise AssetPipelineError("no sprite could be produced")
    return {"kenney": matched, "procedural": written, "failed": failed,
            "download_failures": kenney.get("failed", [])}


def build_audio_assets(ctx: ProjectContext, audio: dict[str, Any]) -> dict[str, Any]:
    """Place SFX and the music loop in assets/audio/. Returns a JSON-serialisable summary."""
    kenney = ensure_kenney(ctx)
    matched = apply_kenney_assets([], audio.get("sfx", []), kenney, ctx.project_dir)["sfx"]
    written, failed = generate_audio(audio, ctx.project_dir, _seed(ctx), skip_sfx=set(matched))
    if not written and not matched:
        raise AssetPipelineError("no audio file could be produced")
    return {"kenney": matched, "procedural": written, "failed": failed,
            "download_failures": kenney.get("failed", [])}
