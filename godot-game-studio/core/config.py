"""Configuration loading: TOML file + environment variables (.env supported)."""
from __future__ import annotations

import logging
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

KNOWN_PLAYTESTERS: tuple[str, ...] = ("brainrot", "hardcore", "casual", "critic", "chaos_qa")
_DEFAULT_CONFIG_FILES: tuple[str, ...] = ("config.toml", "config.example.toml")


class ConfigError(Exception):
    """Raised when configuration is missing or invalid."""


@dataclass(frozen=True)
class LLMConfig:
    model: str
    api_key: str = field(repr=False)
    max_tokens: int
    temperature: float | None
    max_retries: int


@dataclass(frozen=True)
class GodotConfig:
    executable_path: Path | None
    target_version: str
    headless_timeout_sec: float


@dataclass(frozen=True)
class QAConfig:
    max_fix_attempts: int


@dataclass(frozen=True)
class AssetsConfig:
    prefer_kenney: bool
    download_timeout_sec: float
    kenney_pack_urls: tuple[str, ...]


@dataclass(frozen=True)
class OutputConfig:
    base_dir: Path
    overwrite_existing: bool


@dataclass(frozen=True)
class PlaytestConfig:
    enabled: tuple[str, ...]


@dataclass(frozen=True)
class Config:
    llm: LLMConfig
    godot: GodotConfig
    qa: QAConfig
    assets: AssetsConfig
    output: OutputConfig
    playtesters: PlaytestConfig
    source_path: Path | None


def _section(raw: dict[str, Any], name: str) -> dict[str, Any]:
    value = raw.get(name, {})
    if not isinstance(value, dict):
        raise ConfigError(f"[{name}] must be a TOML table")
    return value


def _read(section: dict[str, Any], key: str, default: Any, expected: type) -> Any:
    """Read a typed value from a section, with bool/int/float strictness."""
    if key not in section:
        return default
    value = section[key]
    if expected is float and isinstance(value, int) and not isinstance(value, bool):
        value = float(value)
    wrong_bool = isinstance(value, bool) and expected is not bool
    if wrong_bool or not isinstance(value, expected):
        raise ConfigError(f"'{key}' must be of type {expected.__name__}, got {type(value).__name__}")
    return value


def _resolve_path(path: str | Path | None) -> Path | None:
    if path is not None:
        explicit = Path(path).expanduser()
        if not explicit.is_file():
            raise ConfigError(f"Config file not found: {explicit}")
        return explicit
    env_path = os.environ.get("GGS_CONFIG", "").strip()
    if env_path:
        candidate = Path(env_path).expanduser()
        if not candidate.is_file():
            raise ConfigError(f"GGS_CONFIG points to a missing file: {candidate}")
        return candidate
    for name in _DEFAULT_CONFIG_FILES:
        candidate = Path.cwd() / name
        if candidate.is_file():
            if name == "config.example.toml":
                logger.warning("config.toml not found; using config.example.toml. Copy and review it.")
            return candidate
    logger.warning("No config file found; using built-in defaults.")
    return None


def load_config(
    path: str | Path | None = None,
    *,
    use_dotenv: bool = True,
    require_llm: bool = True,
) -> Config:
    """Load and validate configuration.

    Args:
        path: explicit config path (else GGS_CONFIG, then ./config.toml).
        use_dotenv: load a local .env file into the environment first.
        require_llm: fail if the model name or API key env vars are empty.
    """
    if use_dotenv:
        load_dotenv()

    cfg_path = _resolve_path(path)
    raw: dict[str, Any] = {}
    if cfg_path is not None:
        try:
            with cfg_path.open("rb") as handle:
                raw = tomllib.load(handle)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"Invalid TOML in {cfg_path}: {exc}") from exc

    # --- LLM -------------------------------------------------------------
    llm_raw = _section(raw, "llm")
    model_var = _read(llm_raw, "model_env_var", "GGS_MODEL", str)
    key_var = _read(llm_raw, "api_key_env_var", "ANTHROPIC_API_KEY", str)
    model = os.environ.get(model_var, "").strip()
    api_key = os.environ.get(key_var, "").strip()
    if require_llm:
        if not model:
            raise ConfigError(f"Environment variable {model_var} is empty. Set the model name in .env.")
        if not api_key:
            raise ConfigError(f"Environment variable {key_var} is empty. Set your API key in .env.")
    max_tokens = _read(llm_raw, "max_tokens", 8000, int)
    max_retries = _read(llm_raw, "max_retries", 3, int)
    temperature = _read(llm_raw, "temperature", None, float)
    if max_tokens <= 0:
        raise ConfigError("llm.max_tokens must be positive")
    if max_retries < 0:
        raise ConfigError("llm.max_retries must be >= 0")
    if temperature is not None and not 0.0 <= temperature <= 1.0:
        raise ConfigError("llm.temperature must be between 0 and 1")
    llm = LLMConfig(model, api_key, max_tokens, temperature, max_retries)

    # --- Godot -----------------------------------------------------------
    godot_raw = _section(raw, "godot")
    exe_text = _read(godot_raw, "executable_path", "", str).strip()
    exe_path: Path | None = None
    if exe_text:
        candidate = Path(exe_text).expanduser()
        if candidate.is_file():
            exe_path = candidate
        else:
            logger.warning("godot.executable_path not found (%s); QA will be static-only.", candidate)
    godot = GodotConfig(
        executable_path=exe_path,
        target_version=_read(godot_raw, "target_version", "", str).strip(),
        headless_timeout_sec=_read(godot_raw, "headless_timeout_sec", 120.0, float),
    )

    # --- QA --------------------------------------------------------------
    qa_raw = _section(raw, "qa")
    attempts = _read(qa_raw, "max_fix_attempts", 3, int)
    if not 0 <= attempts <= 10:
        raise ConfigError("qa.max_fix_attempts must be between 0 and 10")
    qa = QAConfig(max_fix_attempts=attempts)

    # --- Assets ----------------------------------------------------------
    assets_raw = _section(raw, "assets")
    urls = _read(assets_raw, "kenney_pack_urls", [], list)
    for url in urls:
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            raise ConfigError(f"assets.kenney_pack_urls contains an invalid URL: {url!r}")
    assets = AssetsConfig(
        prefer_kenney=_read(assets_raw, "prefer_kenney", True, bool),
        download_timeout_sec=_read(assets_raw, "download_timeout_sec", 20.0, float),
        kenney_pack_urls=tuple(urls),
    )

    # --- Output ----------------------------------------------------------
    out_raw = _section(raw, "output")
    output = OutputConfig(
        base_dir=Path(_read(out_raw, "base_dir", "output", str)).expanduser().resolve(),
        overwrite_existing=_read(out_raw, "overwrite_existing", False, bool),
    )

    # --- Playtesters -----------------------------------------------------
    pt_raw = _section(raw, "playtesters")
    enabled = _read(pt_raw, "enabled", list(KNOWN_PLAYTESTERS), list)
    unknown = [name for name in enabled if name not in KNOWN_PLAYTESTERS]
    if unknown:
        raise ConfigError(f"Unknown playtesters {unknown}; valid: {list(KNOWN_PLAYTESTERS)}")
    playtesters = PlaytestConfig(enabled=tuple(enabled))

    return Config(llm, godot, qa, assets, output, playtesters, cfg_path)
