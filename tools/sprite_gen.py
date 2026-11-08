"""Procedural sprite generation with Pillow + numpy (deterministic per name + seed)."""
from __future__ import annotations

import logging
import math
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from agents.asset_manifest import sprite_path
from core.safe_files import resolve_inside

logger = logging.getLogger(__name__)

RESAMPLING = getattr(Image, "Resampling", Image)  # Pillow >= 9.1 or older constants
SUPERSAMPLE = 4
RGB = tuple[int, int, int]
Point = tuple[float, float]


def hex_to_rgb(value: str) -> RGB:
    """'#rrggbb' -> (r, g, b)."""
    return int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16)


def darken(color: RGB, amount: float) -> RGB:
    return (int(color[0] * (1 - amount)), int(color[1] * (1 - amount)), int(color[2] * (1 - amount)))


def lighten(color: RGB, amount: float) -> RGB:
    return tuple(int(c + (255 - c) * amount) for c in color)  # type: ignore[return-value]


@dataclass(frozen=True)
class FrameParams:
    """Per-frame transform: offsets/scales are fractions of the canvas, rot in degrees."""

    dx: float = 0.0
    dy: float = 0.0
    sx: float = 1.0
    sy: float = 1.0
    rot: float = 0.0
    leg: float = 0.0


def _frame_params(animation: str, index: int, count: int) -> FrameParams:
    t = index / count
    s = math.sin(2 * math.pi * t)
    if animation == "idle_bob":
        return FrameParams(dy=0.03 * s, sy=1.0 - 0.03 * s)
    if animation == "walk":
        return FrameParams(dx=0.02 * s, dy=-0.03 * abs(s), rot=4.0 * s, leg=s)
    if animation == "spin":
        return FrameParams(rot=360.0 * t)
    if animation == "pulse":
        k = 0.88 + 0.12 * (0.5 + 0.5 * s)
        return FrameParams(sx=k, sy=k)
    return FrameParams()


def _rotate(points: list[Point], cx: float, cy: float, deg: float) -> list[Point]:
    a = math.radians(deg)
    ca, sa = math.cos(a), math.sin(a)
    return [(cx + (x - cx) * ca - (y - cy) * sa, cy + (x - cx) * sa + (y - cy) * ca) for x, y in points]


def _polygon_points(shape: str, cx: float, cy: float, rx: float, ry: float, rot: float, seed: int) -> list[Point]:
    if shape == "triangle":
        pts: list[Point] = [(cx, cy - ry), (cx + rx, cy + ry), (cx - rx, cy + ry)]
    elif shape == "diamond":
        pts = [(cx, cy - ry), (cx + rx, cy), (cx, cy + ry), (cx - rx, cy)]
    elif shape == "star":
        pts = []
        for k in range(10):
            ang = -math.pi / 2 + k * math.pi / 5
            r = 1.0 if k % 2 == 0 else 0.45
            pts.append((cx + rx * r * math.cos(ang), cy + ry * r * math.sin(ang)))
    else:  # blob
        rng = np.random.default_rng(seed)
        radii = rng.uniform(0.8, 1.0, 14)
        angles = np.linspace(0, 2 * math.pi, 14, endpoint=False)
        pts = [(cx + rx * float(r) * math.cos(float(a)), cy + ry * float(r) * math.sin(float(a)))
               for r, a in zip(radii, angles)]
    return _rotate(pts, cx, cy, rot) if rot else pts


def _rect(d: ImageDraw.ImageDraw, x0: float, y0: float, x1: float, y1: float, **kw: Any) -> None:
    d.rectangle([min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)], **kw)


def _eyes(d: ImageDraw.ImageDraw, cx: float, cy: float, rx: float, ry: float, pupil: RGB) -> None:
    r = max(1.0, min(rx, ry) * 0.16)
    for sign in (-1, 1):
        ex, ey = cx + sign * rx * 0.35, cy - ry * 0.15
        d.ellipse([ex - r, ey - r, ex + r, ey + r], fill=(255, 255, 255))
        pr = max(0.5, r * 0.5)
        d.ellipse([ex - pr, ey - pr, ex + pr, ey + pr], fill=pupil)


def _draw_body(d: ImageDraw.ImageDraw, W: int, H: int, spec: dict[str, Any], p: FrameParams, seed: int) -> None:
    base, accent = hex_to_rgb(spec["base_color"]), hex_to_rgb(spec["accent_color"])
    style, shape = spec["style"], spec["shape"]
    lw = max(1, round(min(W, H) * 0.06))
    edge: RGB | None = base if style == "outline" else (darken(base, 0.45) if style == "pixel" else None)
    fill: RGB | None = None if style == "outline" else base
    cx, cy = W / 2 + p.dx * W, H / 2 + p.dy * H
    rx, ry = W * 0.42 * p.sx, H * 0.42 * p.sy
    eyes = spec["category"] in ("player", "enemy") and spec["animation"] != "spin"
    pupil = darken(base, 0.7)

    if shape == "circle":
        d.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=fill, outline=edge, width=lw)
        ang = math.radians(p.rot - 45)
        mx, my = cx + math.cos(ang) * rx * 0.55, cy + math.sin(ang) * ry * 0.55
        mr = max(1.0, min(rx, ry) * 0.18)
        d.ellipse([mx - mr, my - mr, mx + mr, my + mr], fill=accent)
    elif shape == "rounded_rect":
        d.rounded_rectangle([cx - rx, cy - ry, cx + rx, cy + ry], radius=max(1.0, min(rx, ry) * 0.3),
                            fill=fill, outline=edge, width=lw)
        _rect(d, cx - rx * 0.7, cy + ry * 0.45, cx + rx * 0.7, cy + ry * 0.45 + max(1.0, ry * 0.18), fill=accent)
    elif shape == "humanoid_blocky":
        top, bottom = cy - ry, cy + ry
        h = bottom - top
        head_b, torso_b, leg_top = top + 0.34 * h, top + 0.70 * h, top + 0.70 * h
        left_bottom = bottom - max(0.0, p.leg) * 0.10 * h
        right_bottom = bottom - max(0.0, -p.leg) * 0.10 * h
        _rect(d, cx - rx * 0.45, leg_top, cx - rx * 0.05, left_bottom, fill=fill or base, outline=edge, width=lw)
        _rect(d, cx + rx * 0.05, leg_top, cx + rx * 0.45, right_bottom, fill=fill or base, outline=edge, width=lw)
        _rect(d, cx - rx * 0.75, head_b, cx + rx * 0.75, torso_b, fill=fill, outline=edge, width=lw)
        _rect(d, cx - rx * 0.75, head_b + (torso_b - head_b) * 0.55, cx + rx * 0.75,
              head_b + (torso_b - head_b) * 0.80, fill=accent)
        _rect(d, cx - rx * 0.55, top, cx + rx * 0.55, head_b, fill=fill, outline=edge, width=lw)
        if eyes:
            _eyes(d, cx, top + (head_b - top) * 0.55, rx * 0.75, (head_b - top) * 0.9, pupil)
        return
    else:  # triangle, diamond, star, blob
        pts = _polygon_points(shape, cx, cy, rx, ry, p.rot, seed)
        if fill is not None:
            d.polygon(pts, fill=fill)
        if edge is not None:
            d.line(pts + [pts[0]], fill=edge, width=lw, joint="curve")
        dot = max(1.0, min(rx, ry) * 0.2)
        d.ellipse([cx - dot, cy - dot, cx + dot, cy + dot], fill=accent)
    if eyes:
        _eyes(d, cx, cy, rx, ry, pupil)


def _draw_platform(d: ImageDraw.ImageDraw, W: int, H: int, spec: dict[str, Any]) -> None:
    base, accent = hex_to_rgb(spec["base_color"]), hex_to_rgb(spec["accent_color"])
    style = spec["style"]
    lw = max(1, round(min(W, H) * 0.06))
    edge: RGB | None = base if style == "outline" else (darken(base, 0.45) if style == "pixel" else None)
    fill: RGB | None = None if style == "outline" else base
    d.rounded_rectangle([0, 0, W - 1, H - 1], radius=max(1.0, min(W, H) * 0.12), fill=fill, outline=edge, width=lw)
    strip = max(1.0, H * 0.22)
    _rect(d, lw, lw, W - 1 - lw, lw + strip, fill=accent)
    seam = darken(base, 0.35)
    for k in range(1, 4):
        x = W * k / 4
        d.line([(x, lw + strip), (x, H - 1 - lw)], fill=seam, width=max(1, lw // 2))


def _background(w: int, h: int, base: RGB, accent: RGB, seed: int) -> Image.Image:
    top = np.array(lighten(base, 0.25), dtype=float)
    bottom = np.array(darken(base, 0.6), dtype=float)
    t = np.linspace(0.0, 1.0, h)[:, None, None]
    rgb = np.broadcast_to(top * (1 - t) + bottom * t, (h, w, 3)).copy()
    rng = np.random.default_rng(seed)
    n = max(8, (w * h) // 500)
    xs, ys = rng.integers(0, w, n), rng.integers(0, h, n)
    alpha = rng.uniform(0.3, 1.0, n)[:, None]
    rgb[ys, xs] = rgb[ys, xs] * (1 - alpha) + np.array(accent, dtype=float) * alpha
    arr = np.dstack([rgb.clip(0, 255).astype(np.uint8), np.full((h, w), 255, dtype=np.uint8)])
    return Image.fromarray(arr)


def _particle(w: int, h: int, base: RGB) -> Image.Image:
    yy, xx = np.mgrid[0:h, 0:w]
    dist = np.sqrt(((xx + 0.5) / w * 2 - 1) ** 2 + ((yy + 0.5) / h * 2 - 1) ** 2)
    alpha = (np.clip(1.0 - dist, 0.0, 1.0) ** 1.5 * 255).astype(np.uint8)
    arr = np.zeros((h, w, 4), dtype=np.uint8)
    arr[..., 0], arr[..., 1], arr[..., 2], arr[..., 3] = base[0], base[1], base[2], alpha
    return Image.fromarray(arr)


def _render_frame(spec: dict[str, Any], index: int, count: int, seed: int) -> Image.Image:
    w, h, style, category = spec["width"], spec["height"], spec["style"], spec["category"]
    base, accent = hex_to_rgb(spec["base_color"]), hex_to_rgb(spec["accent_color"])
    if category == "background":
        return _background(w, h, base, accent, seed)
    if category == "particle":
        return _particle(w, h, base)
    if style == "pixel":
        cell = max(1, min(w, h) // 16)
        W, H = max(4, w // cell), max(4, h // cell)
        resample = RESAMPLING.NEAREST
    else:
        W, H = w * SUPERSAMPLE, h * SUPERSAMPLE
        resample = RESAMPLING.LANCZOS
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if category == "platform":
        _draw_platform(d, W, H, spec)
    else:
        _draw_body(d, W, H, spec, _frame_params(spec["animation"], index, count), seed)
    return img.resize((w, h), resample)


def render_sprite(spec: dict[str, Any], seed: int = 0) -> Image.Image:
    """Render a sprite spec to an RGBA image (a horizontal strip when frames > 1)."""
    w, h, frames = spec["width"], spec["height"], spec["frames"]
    local_seed = zlib.crc32(spec["name"].encode("utf-8")) ^ seed
    strip = Image.new("RGBA", (w * frames, h), (0, 0, 0, 0))
    for i in range(frames):
        strip.paste(_render_frame(spec, i, frames, local_seed), (i * w, 0))
    return strip


def generate_sprites(
    specs: list[dict[str, Any]], project_dir: Path, seed: int = 0
) -> tuple[list[str], list[str]]:
    """Write one PNG per spec. Returns (written names, failed names)."""
    written: list[str] = []
    failed: list[str] = []
    for spec in specs:
        name = spec.get("name", "?")
        try:
            image = render_sprite(spec, seed)
            target = resolve_inside(project_dir, sprite_path(name))
            target.parent.mkdir(parents=True, exist_ok=True)
            image.save(target, format="PNG")
            written.append(name)
        except (KeyError, ValueError, OSError) as exc:
            logger.error("Sprite '%s' failed: %s: %s", name, type(exc).__name__, exc)
            failed.append(name)
    return written, failed
