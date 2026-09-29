from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:  # Python 3.10 support; Python 3.11+ uses the standard library.
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - covered on Python 3.10 CI
    import tomli as tomllib  # type: ignore[no-redef]


class ConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class MediaRoots:
    incoming: Path | None = None
    library: Path | None = None


@dataclass(frozen=True)
class VideoConfig:
    path: Path
    mode: str = "dry-run"
    publication: str = "copy"
    on_conflict: str = "rename"
    min_confidence: float = 0.90
    use_cache: bool = True
    allow_risky_enter_accept: bool = False
    plain_output: bool = False
    require_same_filesystem: bool = False
    movies: MediaRoots = MediaRoots()
    tv: MediaRoots = MediaRoots()

    def roots_for(self, media_type: str) -> MediaRoots:
        if media_type == "movie":
            return self.movies
        if media_type == "tv":
            return self.tv
        raise ConfigurationError("Choose movie or tv to use configured paths.")


def config_path(env: dict[str, str] | None = None) -> Path:
    values = os.environ if env is None else env
    explicit = values.get("VIDEO_INGEST_CONFIG")
    if explicit:
        return Path(explicit).expanduser()
    xdg_home = values.get("XDG_CONFIG_HOME")
    base = Path(xdg_home).expanduser() if xdg_home and Path(xdg_home).is_absolute() else Path.home() / ".config"
    return base / "video-ingest" / "config.toml"



def state_paths(library: Path, env: dict[str, str] | None = None) -> tuple[Path, Path]:
    """Return library-scoped XDG cache and state report paths."""
    values = os.environ if env is None else env
    cache_value = Path(values["XDG_CACHE_HOME"]).expanduser() if values.get("XDG_CACHE_HOME") else Path.home() / ".cache"
    state_value = Path(values["XDG_STATE_HOME"]).expanduser() if values.get("XDG_STATE_HOME") else Path.home() / ".local" / "state"
    cache_home = cache_value if cache_value.is_absolute() else Path.home() / ".cache"
    state_home = state_value if state_value.is_absolute() else Path.home() / ".local" / "state"
    identity = str(library.expanduser().resolve(strict=False)).encode("utf-8")
    scope = hashlib.sha256(identity).hexdigest()[:16]
    base_name = Path("video-ingest") / scope
    return cache_home / base_name / "cache.json", state_home / base_name / "reports"


def _path(section: dict[str, Any], key: str, base: Path) -> Path | None:
    value = section.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"{key} must be a non-empty path string.")
    candidate = Path(value).expanduser()
    return candidate if candidate.is_absolute() else (base / candidate)


def load_config(path: Path | None = None) -> VideoConfig:
    selected_path = path.expanduser() if path is not None else config_path()
    try:
        with selected_path.open("rb") as handle:
            raw = tomllib.load(handle)
    except FileNotFoundError:
        raw = {}
    except (OSError, ValueError) as exc:
        raise ConfigurationError(f"Cannot read configuration {selected_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigurationError("Configuration root must be a TOML table.")

    defaults = raw.get("defaults", {})
    movies = raw.get("movies", {})
    tv = raw.get("tv", {})
    for name, section in (("defaults", defaults), ("movies", movies), ("tv", tv)):
        if not isinstance(section, dict):
            raise ConfigurationError(f"[{name}] must be a TOML table.")

    mode = defaults.get("mode", "dry-run")
    publication = defaults.get("publication", "copy")
    conflict = defaults.get("on_conflict", "rename")
    confidence = defaults.get("min_confidence", 0.90)
    if mode not in {"dry-run", "apply"}:
        raise ConfigurationError("defaults.mode must be 'dry-run' or 'apply'.")
    if publication not in {"copy", "move"}:
        raise ConfigurationError("defaults.publication must be 'copy' or 'move'.")
    if conflict not in {"rename", "skip", "overwrite"}:
        raise ConfigurationError("defaults.on_conflict must be rename, skip, or overwrite.")
    if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        raise ConfigurationError("defaults.min_confidence must be between 0 and 1.")
    bool_fields = (
        "use_cache",
        "allow_risky_enter_accept",
        "plain_output",
        "require_same_filesystem",
    )
    for key in bool_fields:
        if key in defaults and not isinstance(defaults[key], bool):
            raise ConfigurationError(f"defaults.{key} must be true or false.")

    def roots(section: dict[str, Any]) -> MediaRoots:
        return MediaRoots(_path(section, "incoming", selected_path.parent), _path(section, "library", selected_path.parent))

    return VideoConfig(
        path=selected_path,
        mode=mode,
        publication=publication,
        on_conflict=conflict,
        min_confidence=float(confidence),
        use_cache=defaults.get("use_cache", True),
        allow_risky_enter_accept=defaults.get("allow_risky_enter_accept", False),
        plain_output=defaults.get("plain_output", False),
        require_same_filesystem=defaults.get("require_same_filesystem", False),
        movies=roots(movies),
        tv=roots(tv),
    )


def format_effective_config(config: VideoConfig) -> str:
    def show(value: Path | None) -> str:
        return str(value) if value is not None else "<not configured>"

    lines = [
        f"Configuration: {config.path}",
        f"Mode: {config.mode}",
        f"Publication: {config.publication}",
        f"Conflict policy: {config.on_conflict}",
        f"Minimum confidence: {config.min_confidence:.2f}",
        f"Cache: {'enabled' if config.use_cache else 'disabled'}",
        f"Allow risky Enter acceptance: {'yes' if config.allow_risky_enter_accept else 'no'}",
        f"Require same filesystem: {'yes' if config.require_same_filesystem else 'no'}",
        "Movies:",
        f"  Incoming: {show(config.movies.incoming)}",
        f"  Library:  {show(config.movies.library)}",
        "TV Shows:",
        f"  Incoming: {show(config.tv.incoming)}",
        f"  Library:  {show(config.tv.library)}",
    ]
    for label, roots in (("Movies", config.movies), ("TV Shows", config.tv)):
        if roots.library is not None:
            cache, reports = state_paths(roots.library)
            lines.extend((f"{label} cache: {cache}", f"{label} reports: {reports}"))
    return "\n".join(lines)


def write_config(config: VideoConfig, path: Path | None = None) -> Path:
    """Write editable TOML using JSON string escaping, which is TOML-compatible."""
    target = path.expanduser() if path is not None else config.path
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "[defaults]",
        f"mode = {json.dumps(config.mode)}",
        f"publication = {json.dumps(config.publication)}",
        f"on_conflict = {json.dumps(config.on_conflict)}",
        f"min_confidence = {config.min_confidence}",
        f"use_cache = {str(config.use_cache).lower()}",
        f"allow_risky_enter_accept = {str(config.allow_risky_enter_accept).lower()}",
        f"plain_output = {str(config.plain_output).lower()}",
        f"require_same_filesystem = {str(config.require_same_filesystem).lower()}",
    ]
    for name, roots in (("movies", config.movies), ("tv", config.tv)):
        lines.extend(("", f"[{name}]"))
        if roots.incoming is not None:
            lines.append(f"incoming = {json.dumps(str(roots.incoming))}")
        if roots.library is not None:
            lines.append(f"library = {json.dumps(str(roots.library))}")
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target
