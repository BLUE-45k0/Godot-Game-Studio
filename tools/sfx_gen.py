"""Procedural sound effects and a music loop (numpy + wave, mono 16-bit)."""
from __future__ import annotations

import logging
import math
import wave
import zlib
from pathlib import Path
from typing import Any

import numpy as np

from agents.asset_manifest import MUSIC_PATH, sfx_path
from core.safe_files import resolve_inside

logger = logging.getLogger(__name__)

SAMPLE_RATE = 44100
MIN_FADE_SEC = 0.002
SCALES: dict[str, list[int]] = {
    "major": [0, 2, 4, 5, 7, 9, 11],
    "minor": [0, 2, 3, 5, 7, 8, 10],
    "pentatonic": [0, 2, 4, 7, 9],
    "dorian": [0, 2, 3, 5, 7, 9, 10],
}
_NOTE_BASE = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
_PROGRESSION = (0, 3, 4, 0)  # scale degrees of the chord root per bar


def _waveform(kind: str, phase: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    if kind == "sine":
        return np.sin(phase)
    frac = (phase / (2 * math.pi)) % 1.0
    if kind == "square":
        return np.where(frac < 0.5, 1.0, -1.0)
    if kind == "saw":
        return 2.0 * frac - 1.0
    if kind == "triangle":
        return 4.0 * np.abs(frac - 0.5) - 1.0
    if kind == "noise":
        return rng.uniform(-1.0, 1.0, phase.shape[0])
    raise ValueError(f"unknown waveform '{kind}'")


def synth_sfx(spec: dict[str, Any], rng: np.random.Generator) -> np.ndarray:
    """Synthesise one effect: waveform + linear frequency sweep + attack/release envelope."""
    n = max(1, int(round(SAMPLE_RATE * spec["duration_sec"])))
    t = np.arange(n) / SAMPLE_RATE
    freq = np.linspace(spec["start_freq_hz"], spec["end_freq_hz"], n)
    if spec.get("vibrato_hz", 0) > 0:
        freq = freq * (1.0 + 0.03 * np.sin(2 * math.pi * spec["vibrato_hz"] * t))
    phase = 2 * math.pi * np.cumsum(freq) / SAMPLE_RATE
    sig = _waveform(spec["waveform"], phase, rng)
    if spec["waveform"] == "noise":
        win = int(np.clip(SAMPLE_RATE / (4 * max(float(freq.mean()), 20.0)), 1, 200))
        if win > 1:
            sig = np.convolve(sig, np.ones(win) / win, mode="same")
        peak = float(np.abs(sig).max())
        if peak > 0:
            sig = sig / peak
    attack = min(n, max(int(spec["attack_sec"] * SAMPLE_RATE), int(MIN_FADE_SEC * SAMPLE_RATE)))
    release = min(n, max(int(spec["release_sec"] * SAMPLE_RATE), int(MIN_FADE_SEC * SAMPLE_RATE)))
    env = np.ones(n)
    env[:attack] *= np.linspace(0.0, 1.0, attack)
    env[n - release:] *= np.linspace(1.0, 0.0, release)
    return (sig * env * spec["volume"]).astype(np.float32)


def note_to_midi(name: str, octave: int) -> int:
    """'C', 'F#', 'Bb' + octave -> MIDI number."""
    base = _NOTE_BASE[name[0]]
    if len(name) > 1:
        base += 1 if name[1] == "#" else -1
    return 12 * (octave + 1) + base


def midi_to_freq(midi: int) -> float:
    return 440.0 * 2.0 ** ((midi - 69) / 12.0)


def _scale_note(root_midi: int, scale: list[int], degree: int) -> int:
    octave, idx = divmod(degree, len(scale))
    return root_midi + 12 * octave + scale[idx]


def _tone(kind: str, freq: float, length: int, release_frac: float, rng: np.random.Generator) -> np.ndarray:
    t = np.arange(length) / SAMPLE_RATE
    sig = _waveform(kind, 2 * math.pi * freq * t, rng)
    env = np.ones(length)
    attack = min(int(0.005 * SAMPLE_RATE), length // 2)
    if attack > 0:
        env[:attack] = np.linspace(0.0, 1.0, attack)
    release = max(1, int(length * release_frac))
    env[length - release:] *= np.linspace(1.0, 0.0, release)
    return sig * env


def synth_music(spec: dict[str, Any], rng: np.random.Generator) -> np.ndarray:
    """A seamless loop: bass on half-bars, a random-walk lead on eighth notes."""
    scale = SCALES[spec["scale"]]
    step = int(round(SAMPLE_RATE * 60 / spec["bpm"] / 2))  # one eighth note
    bars = spec["loop_bars"]
    total = bars * 8 * step
    lead_root = note_to_midi(spec["root_note"], 4)
    bass_root = note_to_midi(spec["root_note"], 2)
    out = np.zeros(total)

    for bar in range(bars):
        chord = _PROGRESSION[bar % len(_PROGRESSION)]
        for half in range(2):
            start = (bar * 8 + half * 4) * step
            freq = midi_to_freq(_scale_note(bass_root, scale, chord))
            out[start:start + 4 * step] += 0.6 * _tone(spec["bass_waveform"], freq, 4 * step, 0.25, rng)

    degree = len(scale)
    for i in range(bars * 8):
        bar, pos = divmod(i, 8)
        chord = _PROGRESSION[bar % len(_PROGRESSION)]
        if pos == 0:
            degree = chord + len(scale) + int(rng.choice([0, 2]))
        else:
            if rng.random() < 0.25:
                continue  # rest
            degree = int(np.clip(degree + int(rng.integers(-2, 3)), 0, 2 * len(scale)))
        freq = midi_to_freq(_scale_note(lead_root, scale, degree))
        out[i * step:(i + 1) * step] += 0.5 * _tone(spec["lead_waveform"], freq, step, 0.4, rng)

    peak = float(np.abs(out).max())
    if peak > 0:
        out *= spec["volume"] / peak
    return out.astype(np.float32)


def write_wav(path: Path, samples: np.ndarray) -> None:
    """Write mono 16-bit PCM."""
    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2")
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(pcm.tobytes())


def generate_audio(
    audio: dict[str, Any],
    project_dir: Path,
    seed: int = 0,
    skip_sfx: set[str] | None = None,
) -> tuple[list[str], list[str]]:
    """Write SFX (except `skip_sfx`) and the music loop. Returns (written, failed)."""
    skip_sfx = skip_sfx or set()
    written: list[str] = []
    failed: list[str] = []
    for spec in audio.get("sfx", []):
        name = spec["name"]
        if name in skip_sfx:
            continue
        try:
            rng = np.random.default_rng(zlib.crc32(name.encode("utf-8")) ^ seed)
            write_wav(resolve_inside(project_dir, sfx_path(name)), synth_sfx(spec, rng))
            written.append(name)
        except (KeyError, ValueError, OSError) as exc:
            logger.error("SFX '%s' failed: %s: %s", name, type(exc).__name__, exc)
            failed.append(name)
    music = audio.get("music")
    if music:
        try:
            rng = np.random.default_rng(zlib.crc32(music["root_note"].encode("utf-8")) ^ seed ^ music["bpm"])
            write_wav(resolve_inside(project_dir, MUSIC_PATH), synth_music(music, rng))
            written.append("music_loop")
        except (KeyError, ValueError, OSError) as exc:
            logger.error("Music failed: %s: %s", type(exc).__name__, exc)
            failed.append("music_loop")
    return written, failed
