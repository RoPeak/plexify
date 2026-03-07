from __future__ import annotations

from pathlib import Path

from ..util import json_dump, json_load


def wizard_prefs_path() -> Path:
    return Path.home() / ".plexify" / "wizard.json"


def load_wizard_prefs() -> dict[str, dict[str, str]]:
    path = wizard_prefs_path()
    try:
        data = json_load(path)
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    cleaned: dict[str, dict[str, str]] = {}
    for key, value in data.items():
        if not isinstance(value, dict):
            continue
        cleaned[key] = {str(k): str(v) for k, v in value.items() if isinstance(k, str) and isinstance(v, str)}
    return cleaned


def save_wizard_prefs(media_key: str, source: Path, library: Path) -> None:
    prefs = load_wizard_prefs()
    prefs[media_key] = {"source": str(source), "library": str(library)}
    json_dump(wizard_prefs_path(), prefs)


def wizard_defaults(media_key: str) -> tuple[Path | None, Path | None]:
    def _sanitize(path: Path | None) -> Path | None:
        if path is None:
            return None
        try:
            expanded = path.expanduser()
            resolved = expanded.resolve(strict=False)
            if resolved == Path.cwd().resolve(strict=False):
                return None
            if not expanded.exists() or not expanded.is_dir():
                return None
        except (OSError, RuntimeError):
            return None
        return expanded

    prefs = load_wizard_prefs()
    section = prefs.get(media_key, {})
    source = _sanitize(Path(section["source"])) if "source" in section else None
    library = _sanitize(Path(section["library"])) if "library" in section else None
    return source, library
