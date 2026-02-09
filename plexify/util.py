from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

WINDOWS_INVALID = r'<>"/\\|?*'
NOISE_TOKENS = {
    "1080p",
    "720p",
    "2160p",
    "4k",
    "x264",
    "x265",
    "h264",
    "h265",
    "hevc",
    "av1",
    "bluray",
    "blu-ray",
    "bdrip",
    "brrip",
    "web",
    "web-dl",
    "webrip",
    "hdrip",
    "dvdrip",
    "hdtv",
    "remux",
    "proper",
    "repack",
    "extended",
    "unrated",
    "yts",
    "rarbg",
}
ROMAN_NUMERALS = {"i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x", "xi", "xii", "xiii", "xiv", "xv"}
ROMAN_TO_INT = {
    "i": "1",
    "ii": "2",
    "iii": "3",
    "iv": "4",
    "v": "5",
    "vi": "6",
    "vii": "7",
    "viii": "8",
    "ix": "9",
    "x": "10",
    "xi": "11",
    "xii": "12",
    "xiii": "13",
    "xiv": "14",
    "xv": "15",
}
WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    "COM1",
    "COM2",
    "COM3",
    "COM4",
    "COM5",
    "COM6",
    "COM7",
    "COM8",
    "COM9",
    "LPT1",
    "LPT2",
    "LPT3",
    "LPT4",
    "LPT5",
    "LPT6",
    "LPT7",
    "LPT8",
    "LPT9",
}
TV_SEASON_FOLDER_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:season|series|seaon|seson|seasn)[-_. ]*(\d{1,2})(?![A-Za-z0-9])",
    re.IGNORECASE,
)
TV_SEASON_TOKEN_WITH_NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:season|series|seaon|seson|seasn)[-_. ]*\d{1,2}(?![A-Za-z0-9])",
    re.IGNORECASE,
)


def sanitise_name(value: str) -> str:
    if not value:
        return "Unknown"
    cleaned = value.replace(":", " - ")
    cleaned = re.sub(r"[\\/]+", " ", cleaned)
    cleaned = "".join(" " if ch in WINDOWS_INVALID else ch for ch in cleaned)
    cleaned = re.sub(r"_\s+", "_", cleaned)
    cleaned = re.sub(r"\s+_", "_", cleaned)
    cleaned = cleaned.rstrip(". ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if cleaned.upper() in WINDOWS_RESERVED:
        cleaned = f"{cleaned}_"
    return cleaned or "Unknown"


def make_search_query(value: str) -> str:
    if not value:
        return ""
    lowered = value.lower()
    lowered = TV_SEASON_TOKEN_WITH_NUMBER_RE.sub(" ", lowered)
    lowered = lowered.replace("&", " and ")
    lowered = re.sub(r"[\u2013\u2014\u2212]+", "-", lowered)
    lowered = re.sub(r"[\"'`\u2019\u201c\u201d]", "", lowered)
    tokens = re.split(r"[.\s_\-:/\\]+", lowered)
    cleaned: list[str] = []
    for token in tokens:
        if not token:
            continue
        if re.fullmatch(r"(19|20)\d{2}", token):
            continue
        if token in NOISE_TOKENS:
            continue
        if re.fullmatch(r"\d{3,4}p", token):
            continue
        if re.fullmatch(r"s\d{1,2}e\d{1,3}", token):
            continue
        cleaned.append(token)
    return re.sub(r"\s+", " ", " ".join(cleaned)).strip()


def normalize_title_for_similarity(value: str) -> str:
    if not value:
        return ""
    lowered = make_search_query(value)
    tokens = re.split(r"\s+", lowered)
    cleaned: list[str] = []
    for idx, token in enumerate(tokens):
        if not token:
            continue
        if token in ROMAN_NUMERALS and idx == len(tokens) - 1:
            cleaned.append(ROMAN_TO_INT[token])
        else:
            cleaned.append(token)
    return re.sub(r"\s+", " ", " ".join(cleaned)).strip()


def normalize_title(value: str) -> str:
    return normalize_title_for_similarity(value)


def build_cache_key(
    path: Path,
    incoming_root: Path | None,
    media_type: str,
    year: int | None,
) -> str:
    rel = path
    if incoming_root is not None:
        try:
            rel = path.relative_to(incoming_root)
        except ValueError:
            rel = Path(path.name)
    rel_key = rel.as_posix().lower()
    stem_norm = normalize_title_for_similarity(rel.stem)
    year_text = str(year) if year else "unknown"
    return f"{media_type}|{rel_key}|{stem_norm}|{year_text}"


def movie_cache_key(title: str, year: int | None) -> str:
    year_text = str(year) if year else "unknown"
    return f"movie|{normalize_title_for_similarity(title)}|{year_text}"


def tv_show_cache_key(title: str, year: int | None) -> str:
    year_text = str(year) if year else "unknown"
    return f"tv|{normalize_title_for_similarity(title)}|{year_text}"


def tv_episode_cache_key(title: str, year: int | None, season: int | None, episode: int | None) -> str:
    year_text = str(year) if year else "unknown"
    season_text = str(season) if season is not None else "unknown"
    episode_text = str(episode) if episode is not None else "unknown"
    return f"tv|{normalize_title_for_similarity(title)}|{year_text}|s{season_text}|e{episode_text}"


def tv_show_folder_cache_key(path: Path, incoming_root: Path | None) -> str | None:
    rel = path
    if incoming_root is not None:
        try:
            rel = path.relative_to(incoming_root)
        except ValueError:
            rel = Path(path.name)
    current = rel.parent
    season_folder = None
    while str(current) not in {"", "."}:
        if TV_SEASON_FOLDER_RE.search(current.name):
            season_folder = current
            break
        parent = current.parent
        if parent == current:
            break
        current = parent

    if season_folder is not None:
        show_folder = season_folder.parent
    else:
        show_folder = rel.parent
    if str(show_folder) in {"", "."}:
        return None
    return f"tvfolder|{show_folder.as_posix().lower()}"


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def json_load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def json_dump(path: Path, data: dict[str, Any]) -> None:
    ensure_dir(path.parent)
    payload = json.dumps(data, indent=2, sort_keys=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(payload, encoding="utf-8")
    os.replace(tmp_path, path)


def now_timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def _natural_component_key(value: str) -> tuple[tuple[int, int | str], ...]:
    parts: list[tuple[int, int | str]] = []
    for token in re.split(r"(\d+)", value.casefold()):
        if not token:
            continue
        if token.isdigit():
            parts.append((0, int(token)))
        else:
            parts.append((1, token))
    return tuple(parts)


def _natural_path_key(path: Path, root: Path) -> tuple[tuple[tuple[int, int | str], ...], ...]:
    try:
        rel = path.relative_to(root)
    except ValueError:
        rel = path
    return tuple(_natural_component_key(part) for part in rel.parts)


def iter_video_files(root: Path, extensions: Iterable[str]) -> list[Path]:
    exts = {ext.lower().lstrip(".") for ext in extensions}
    results: list[Path] = []
    for base, _, files in os.walk(root):
        for name in files:
            if not name:
                continue
            suffix = Path(name).suffix.lower().lstrip(".")
            if suffix in exts:
                results.append(Path(base) / name)
    return sorted(results, key=lambda path: _natural_path_key(path, root))


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    counter = 2
    while True:
        candidate = path.with_name(f"{path.stem} ({counter}){path.suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def unique_plan_path(path: Path, planned: dict[str, int]) -> tuple[Path, bool]:
    key = str(path).lower()
    if key not in planned:
        planned[key] = 1
        return path, False
    count = planned[key] + 1
    planned[key] = count
    candidate = path.with_name(f"{path.stem} ({count}){path.suffix}")
    return candidate, True


@dataclass(frozen=True)
class MovePlan:
    source: Path
    destination: Path
    mode: str
    media_type: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class ExecutionResult:
    moved: list[MovePlan]
    skipped: list[MovePlan]
    errors: list[str]
