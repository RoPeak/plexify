from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

PLEXIFY_PLATFORM_ENV = "PLEXIFY_PLATFORM"
_VALID_PLATFORM_VALUES = {"auto", "windows", "linux", "posix"}


@dataclass(frozen=True)
class PlatformContext:
    requested_platform: str
    detected_platform: str
    effective_platform: str
    override_source: str | None = None


def detect_runtime_platform() -> str:
    return "windows" if os.name == "nt" else "linux"


def _normalise_platform(value: Any) -> str:
    if value is None:
        normalised = "auto"
    elif isinstance(value, str):
        normalised = value.strip().lower()
    else:
        # Direct function calls in tests may pass Typer OptionInfo defaults.
        normalised = "auto"
    if normalised == "posix":
        return "linux"
    if normalised in _VALID_PLATFORM_VALUES:
        return normalised
    raise ValueError("Platform must be one of: auto, windows, linux.")


def resolve_platform(platform: str | None = "auto", *, env: Mapping[str, str] | None = None) -> PlatformContext:
    detected_platform = detect_runtime_platform()
    requested_platform = _normalise_platform(platform)
    env_map: Mapping[str, str] = os.environ if env is None else env

    env_platform = "auto"
    if PLEXIFY_PLATFORM_ENV in env_map:
        env_platform = _normalise_platform(env_map.get(PLEXIFY_PLATFORM_ENV))

    if requested_platform != "auto":
        return PlatformContext(
            requested_platform=requested_platform,
            detected_platform=detected_platform,
            effective_platform=requested_platform,
            override_source="cli",
        )

    if env_platform != "auto":
        return PlatformContext(
            requested_platform=requested_platform,
            detected_platform=detected_platform,
            effective_platform=env_platform,
            override_source="env",
        )

    return PlatformContext(
        requested_platform=requested_platform,
        detected_platform=detected_platform,
        effective_platform=detected_platform,
        override_source=None,
    )


def is_case_sensitive_filesystem(platform: str) -> bool:
    return platform == "linux"


def path_lookup_key(path: Path | str, *, platform: str) -> str:
    value = str(path).replace("\\", "/")
    if platform == "windows":
        return value.casefold()
    return value
