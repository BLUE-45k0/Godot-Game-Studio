"""Single source of truth for asset file names shared by agents and tools."""
from __future__ import annotations

from typing import Any

SPRITES_DIR = "assets/sprites"
AUDIO_DIR = "assets/audio"
MUSIC_PATH = f"{AUDIO_DIR}/music_loop.wav"


def sprite_path(name: str) -> str:
    """Project-relative path of a sprite PNG. Animated sprites are horizontal strips."""
    return f"{SPRITES_DIR}/{name}.png"


def sfx_path(name: str) -> str:
    """Project-relative path of a sound-effect WAV."""
    return f"{AUDIO_DIR}/{name}.wav"


def build_manifest(art: dict[str, Any] | None, audio: dict[str, Any] | None) -> dict[str, Any]:
    """Describe the asset files the programmer may reference, with res:// paths."""
    art = art or {}
    audio = audio or {}
    sprites = [
        {
            "name": s["name"],
            "res_path": f"res://{sprite_path(s['name'])}",
            "frame_size": [s["width"], s["height"]],
            "hframes": s.get("frames", 1),
            "animation": s.get("animation", "none"),
            "category": s.get("category", ""),
        }
        for s in art.get("sprites", [])
    ]
    sfx = [
        {"name": s["name"], "res_path": f"res://{sfx_path(s['name'])}", "event": s.get("event", "")}
        for s in audio.get("sfx", [])
    ]
    music = {"res_path": f"res://{MUSIC_PATH}"} if audio.get("music") else None
    return {"sprites": sprites, "sfx": sfx, "music": music}
