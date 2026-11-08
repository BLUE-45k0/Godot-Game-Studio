import io
import wave
import zipfile

import numpy as np
import pytest
import requests
from PIL import Image

from core.config import AssetsConfig, load_config
from core.schemas import ProjectContext
from tools.asset_fetcher import apply_kenney_assets, fetch_packs
from tools.godot_writer import ProjectWriteError, render_project_godot, write_project_files
from tools.sfx_gen import SAMPLE_RATE, generate_audio
from tools.sprite_gen import generate_sprites, render_sprite


def _sprite(name="hero", shape="circle", animation="none", frames=1, category="player", style="flat"):
    return {"name": name, "category": category, "width": 32, "height": 32, "style": style,
            "shape": shape, "base_color": "#33aaff", "accent_color": "#ffcc00",
            "frames": frames, "animation": animation, "description": "test sprite"}


@pytest.mark.parametrize("shape", ["circle", "rounded_rect", "triangle", "diamond", "star", "blob", "humanoid_blocky"])
@pytest.mark.parametrize("style", ["pixel", "flat", "outline"])
def test_every_shape_and_style_renders(shape, style):
    img = render_sprite(_sprite(shape=shape, style=style, animation="walk", frames=4))
    assert img.size == (32 * 4, 32)
    assert np.array(img)[..., 3].max() > 0


def test_sprite_deterministic_and_written(tmp_path):
    spec = _sprite(shape="blob", animation="spin", frames=6)
    assert render_sprite(spec, 5).tobytes() == render_sprite(spec, 5).tobytes()
    written, failed = generate_sprites([spec, _sprite("bg", category="background")], tmp_path, 5)
    assert written == ["hero", "bg"] and failed == []
    assert (tmp_path / "assets/sprites/hero.png").is_file()
    assert np.array(Image.open(tmp_path / "assets/sprites/bg.png"))[..., 3].min() == 255


def test_audio_generation(tmp_path):
    audio = {
        "sfx": [{"name": "jump", "event": "jump", "waveform": "square", "start_freq_hz": 300,
                 "end_freq_hz": 700, "duration_sec": 0.2, "volume": 0.5, "attack_sec": 0.005,
                 "release_sec": 0.05, "vibrato_hz": 0}],
        "music": {"mood": "calm", "bpm": 120, "scale": "minor", "root_note": "A", "loop_bars": 2,
                  "lead_waveform": "triangle", "bass_waveform": "sine", "volume": 0.4},
    }
    written, failed = generate_audio(audio, tmp_path, 1)
    assert written == ["jump", "music_loop"] and failed == []
    with wave.open(str(tmp_path / "assets/audio/jump.wav")) as wav:
        assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (1, 2, SAMPLE_RATE)
        pcm = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2")
        assert abs(wav.getnframes() - int(0.2 * SAMPLE_RATE)) <= 1
        assert 0 < np.abs(pcm).max() <= 0.5 * 32767 + 1
    with wave.open(str(tmp_path / "assets/audio/music_loop.wav")) as wav:
        assert wav.getnframes() == 2 * 8 * round(SAMPLE_RATE * 60 / 120 / 2)


def test_project_godot_text():
    text = render_project_godot(
        title='My "Game"', main_scene="scenes/main.tscn", autoloads={"Game": "scripts/game.gd"},
        input_actions=[{"name": "jump", "keys": ["Space", "W"]}, {"name": "shoot", "keys": ["MouseLeft"]}],
        width=1152, height=648, clear_color="#102030", version="4.3", pixel_art=True,
    )
    assert "config_version=5" in text
    assert 'config/name="My \\"Game\\""' in text
    assert 'run/main_scene="res://scenes/main.tscn"' in text
    assert 'Game="*res://scripts/game.gd"' in text
    assert '"physical_keycode":32' in text and '"physical_keycode":87' in text
    assert "InputEventMouseButton" in text
    assert 'PackedStringArray("4.3", "GL Compatibility")' in text


def test_write_project_files(tmp_path):
    cfg_file = tmp_path / "c.toml"
    cfg_file.write_text('[godot]\ntarget_version = "4.3"\n', encoding="utf-8")
    config = load_config(cfg_file, use_dotenv=False, require_llm=False)
    ctx = ProjectContext("idea", tmp_path / "proj", config, llm=None)  # type: ignore[arg-type]
    (ctx.project_dir / "scenes").mkdir(parents=True)
    (ctx.project_dir / "scenes/main.tscn").write_text("[gd_scene format=3]\n", encoding="utf-8")
    settings = {"main_scene": "scenes/main.tscn", "autoloads": {},
                "input_actions": [{"name": "jump", "keys": ["Space"]}]}
    info = write_project_files(ctx, settings)
    assert "project.godot" in info["files"] and info["godot_version"] == "4.3"
    assert (ctx.project_dir / "project.godot").read_text(encoding="utf-8").startswith("; Engine")
    with pytest.raises(ProjectWriteError):
        write_project_files(ctx, {**settings, "main_scene": "scenes/missing.tscn"})


class FakeResponse:
    def __init__(self, data: bytes, status: int = 200):
        self._data, self.status = data, status
        self.headers = {"Content-Length": str(len(data))}

    def raise_for_status(self):
        if self.status >= 400:
            raise requests.HTTPError(f"status {self.status}")

    def iter_content(self, chunk_size=1):
        for i in range(0, len(self._data), chunk_size):
            yield self._data[i:i + chunk_size]

    def close(self):
        pass


def _pack_zip() -> bytes:
    png = io.BytesIO()
    Image.new("RGBA", (64, 32), (255, 0, 0, 255)).save(png, format="PNG")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Players/player_idle.png", png.getvalue())
        zf.writestr("Sounds/jump.wav", b"RIFFfake")
        zf.writestr("../evil.png", png.getvalue())
        zf.writestr("readme.txt", b"ignored")
    return buf.getvalue()


def test_fetch_extract_and_match(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.asset_fetcher.requests.get", lambda url, **kw: FakeResponse(_pack_zip()))
    cfg = AssetsConfig(True, 5.0, ("https://example.test/pack.zip",))
    result = fetch_packs(cfg, tmp_path)
    assert sorted(f.rsplit("/", 1)[-1] for f in result.files) == ["jump.wav", "player_idle.png"]
    assert not (tmp_path / "evil.png").exists()

    matched = apply_kenney_assets(
        [_sprite("player", category="player")], [{"name": "jump"}], {"files": result.files}, tmp_path)
    assert matched == {"sprites": ["player"], "sfx": ["jump"]}
    assert Image.open(tmp_path / "assets/sprites/player.png").size == (32, 32)
    assert (tmp_path / "assets/audio/jump.wav").is_file()


def test_fetch_failure_is_safe(tmp_path, monkeypatch):
    def boom(url, **kw):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr("tools.asset_fetcher.requests.get", boom)
    result = fetch_packs(AssetsConfig(True, 1.0, ("https://example.test/pack.zip",)), tmp_path)
    assert result.files == [] and len(result.failed) == 1
