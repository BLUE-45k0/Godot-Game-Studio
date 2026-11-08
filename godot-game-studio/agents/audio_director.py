"""Audio Director: sonic identity, procedural SFX and a looping music spec."""
from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from agents.base_agent import SNAKE_CASE, BaseAgent, artifacts_block
from core.schemas import ProjectContext
from tools.asset_pipeline import build_audio_assets

Waveform = Literal["sine", "square", "saw", "triangle", "noise"]
_NOTE_RE = re.compile(r"^[A-G](#|b)?$")


class SfxSpec(BaseModel):
    name: str
    event: str = Field(min_length=3)
    waveform: Waveform
    start_freq_hz: float = Field(ge=20, le=12000)
    end_freq_hz: float = Field(ge=20, le=12000)
    duration_sec: float = Field(ge=0.03, le=3.0)
    volume: float = Field(ge=0.05, le=1.0)
    attack_sec: float = Field(default=0.005, ge=0, le=0.5)
    release_sec: float = Field(default=0.05, ge=0, le=1.0)
    vibrato_hz: float = Field(default=0.0, ge=0, le=30)

    @field_validator("name")
    @classmethod
    def _name_ok(cls, value: str) -> str:
        if not SNAKE_CASE.match(value):
            raise ValueError(f"sfx name '{value}' must be snake_case")
        return value

    @model_validator(mode="after")
    def _envelope_fits(self) -> "SfxSpec":
        if self.attack_sec + self.release_sec > self.duration_sec:
            raise ValueError(f"'{self.name}': attack + release must not exceed duration")
        return self


class MusicSpec(BaseModel):
    mood: str = Field(min_length=3)
    bpm: int = Field(ge=60, le=200)
    scale: Literal["major", "minor", "pentatonic", "dorian"]
    root_note: str
    loop_bars: int = Field(ge=2, le=16)
    lead_waveform: Literal["sine", "square", "saw", "triangle"]
    bass_waveform: Literal["sine", "square", "saw", "triangle"]
    volume: float = Field(ge=0.05, le=1.0)

    @field_validator("root_note")
    @classmethod
    def _note_ok(cls, value: str) -> str:
        if not _NOTE_RE.match(value):
            raise ValueError("root_note must be a note name like C, F#, Bb")
        return value


class AudioDesign(BaseModel):
    sonic_identity: str = Field(min_length=20)
    sfx: list[SfxSpec] = Field(min_length=5, max_length=16)
    music: MusicSpec

    @model_validator(mode="after")
    def _unique(self) -> "AudioDesign":
        names = [s.name for s in self.sfx]
        if len(set(names)) != len(names):
            raise ValueError("duplicate sfx names")
        return self


_ROLE = """You are the Audio Director. Define a coherent sonic identity and describe sounds that can
be synthesised procedurally (sine/square/saw/triangle/noise with a simple envelope and a frequency
sweep from start to end).
- sfx: 5-16 effects (snake_case names), each tied to a game event from the design's juice plan
  (jump, hit, pickup, death, win, UI click, ...). Short durations (<= 3s), attack + release must
  fit inside the duration.
- music: one looping track described by bpm, scale, root note, bars, lead/bass waveforms."""


class AudioDirectorAgent(BaseAgent):
    name = "audio"
    role = _ROLE

    def run(self, ctx: ProjectContext) -> dict[str, Any]:
        user = (
            f"Game idea:\n{ctx.idea}\n\n"
            f"{artifacts_block(ctx, ['game_designer'])}\n\n"
            "Produce the audio direction, sound effect specs and the music loop spec."
        )
        audio = self.ask_json(ctx, user, AudioDesign)
        result = audio.model_dump()
        result["assets"] = build_audio_assets(ctx, result)
        return result
