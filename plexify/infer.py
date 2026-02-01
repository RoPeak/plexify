from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from guessit import guessit

from .util import NOISE_TOKENS

SEASON_RE = re.compile(r"(?<![A-Za-z0-9])(?:season|series)[-_. ]*(\d{1,2})\b", re.IGNORECASE)
SXXEYY_RE = re.compile(r"\bs(\d{1,2})e(\d{1,3})\b", re.IGNORECASE)
XYY_RE = re.compile(r"\b(\d{1,2})x(\d{1,3})\b", re.IGNORECASE)
SEASON_EP_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:season|series)[-_. ]*(\d{1,2})[-_. ]+(?:episode|ep)?\s*(\d{1,3})(?!\d)",
    re.IGNORECASE,
)
EPISODE_RE = re.compile(r"\b(?:episode|ep)\s*(\d{1,3})\b", re.IGNORECASE)
TV_HINT_RE = re.compile(r"\b(?:series|season|episode|ep)\b", re.IGNORECASE)
YEAR_RE = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")
YEAR_RANGE_RE = re.compile(r"(?<!\d)(19\d{2}|20\d{2})\s*[-–]\s*(19\d{2}|20\d{2})(?!\d)")
VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".m4v", ".mov", ".ts"}
GENERIC_TV_FOLDERS = {
    "tv",
    "tv shows",
    "television",
    "shows",
    "series",
    "movies",
    "films",
    "unorganised",
    "unsorted",
    "incoming",
}


@dataclass(frozen=True)
class InferredItem:
    path: Path
    media_type: str
    title: str
    year: Optional[int] = None
    season: Optional[int] = None
    episode: Optional[int] = None
    episode_title: Optional[str] = None


def _parent_show_name(path: Path) -> Optional[str]:
    parts = [p.name for p in path.parents]
    for idx, name in enumerate(parts):
        if SEASON_RE.search(name):
            return parts[idx + 1] if idx + 1 < len(parts) else None
    return None


def _extract_season_from_parts(path: Path) -> Optional[int]:
    for part in path.parts:
        match = SEASON_RE.search(part)
        if match:
            return int(match.group(1))
    return None


def _extract_episode_from_name(name: str) -> tuple[Optional[int], bool]:
    match = EPISODE_RE.search(name)
    if match:
        return int(match.group(1)), True
    if name.isdigit():
        return int(name), False
    return None, False


def _extract_season_episode_from_name(name: str) -> tuple[Optional[int], Optional[int], bool]:
    match = SEASON_EP_RE.search(name)
    if match:
        return int(match.group(1)), int(match.group(2)), True
    match = XYY_RE.search(name)
    if match:
        return int(match.group(1)), int(match.group(2)), True
    return None, None, False


def _extract_year_range(name: str) -> Optional[int]:
    match = YEAR_RANGE_RE.search(name)
    if match:
        return int(match.group(1))
    return None


def _parent_has_multiple_videos(path: Path) -> bool:
    parent = path.parent
    if not parent.exists() or not parent.is_dir():
        return False
    count = 0
    for entry in parent.iterdir():
        if entry.is_file() and entry.suffix.lower() in VIDEO_EXTS:
            count += 1
            if count >= 2:
                return True
    return False


def _clean_parent_show_name(name: str) -> tuple[str, Optional[int]]:
    year = _extract_year_range(name) or _extract_year(name)
    cleaned = re.sub(r"\s*[\[(].*?[\])]\s*$", "", name).strip()
    cleaned = re.sub(r"[._]+", " ", cleaned).strip()
    return cleaned, year


def _strip_season_tokens(value: str) -> str:
    cleaned = re.sub(r"(?i)(?:season|series)[\s._-]*\d{1,2}", "", value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or value


def _extract_year(name: str) -> Optional[int]:
    match = YEAR_RE.search(name)
    if match:
        return int(match.group(1))
    return None


def _starts_with_number(value: str) -> bool:
    return bool(re.match(r"^\s*\d+", value))


def _clean_title_from_stem(stem: str) -> str:
    tokens = re.split(r"[.\s_\-]+", stem)
    cleaned: list[str] = []
    for token in tokens:
        if not token:
            continue
        lower = token.lower()
        if YEAR_RE.fullmatch(token):
            continue
        if lower in NOISE_TOKENS:
            continue
        if re.fullmatch(r"\d{3,4}p", lower):
            continue
        if re.fullmatch(r"s\d{1,2}e\d{1,2}", lower):
            continue
        cleaned.append(token)
    return " ".join(cleaned).strip()


def _extract_episode_title(stem: str, episode: Optional[int]) -> Optional[str]:
    if episode is None:
        return None
    tokens = [token for token in re.split(r"[.\s_\-]+", stem) if token]
    if not tokens:
        return None
    episode_values = {
        str(episode),
        str(episode).zfill(2),
        str(episode).zfill(3),
    }
    start_idx = None
    for idx, token in enumerate(tokens):
        lower = token.lower()
        if lower in episode_values:
            start_idx = idx + 1
            break
        match = re.fullmatch(r"s(\d{1,2})e(\d{1,3})", lower)
        if match and int(match.group(2)) == episode:
            start_idx = idx + 1
            break
        match = re.fullmatch(r"e(\d{1,3})", lower)
        if match and int(match.group(1)) == episode:
            start_idx = idx + 1
            break
        match = re.fullmatch(r"ep(\d{1,3})", lower)
        if match and int(match.group(1)) == episode:
            start_idx = idx + 1
            break
    if start_idx is None or start_idx >= len(tokens):
        return None
    cleaned: list[str] = []
    for token in tokens[start_idx:]:
        lower = token.lower()
        if YEAR_RE.fullmatch(token):
            continue
        if lower in NOISE_TOKENS:
            continue
        if lower in {"season", "series", "episode", "ep"}:
            continue
        if re.fullmatch(r"s\d{1,2}e\d{1,3}", lower):
            continue
        if re.fullmatch(r"\d{1,3}", lower):
            continue
        cleaned.append(token)
    title = " ".join(cleaned).strip()
    return title or None


def _has_tv_context(path: Path) -> bool:
    if SXXEYY_RE.search(path.stem):
        return True
    if any(SEASON_RE.search(parent.name) for parent in path.parents):
        return True
    if any(part.lower() in {"tv", "tv shows"} for part in path.parts):
        return True
    if TV_HINT_RE.search(path.stem):
        return True
    return False


def infer_item(path: Path) -> InferredItem:
    guess = guessit(path.name)
    media_type = "movie"
    season = None
    episode = None
    explicit_episode = False
    has_tv_context = _has_tv_context(path)
    has_tv_hint = TV_HINT_RE.search(path.stem) is not None
    title_override = None
    year_override = None

    if path.stem.isdigit() and 1 <= len(path.stem) <= 3 and _parent_has_multiple_videos(path):
        media_type = "tv"
        episode = int(path.stem)
        season = season or 1
        explicit_episode = True
        title_override, year_override = _clean_parent_show_name(path.parent.name)
        if title_override:
            has_tv_context = True

    stem_for_tv = YEAR_RANGE_RE.sub("", path.stem)
    sxxeyy = SXXEYY_RE.search(stem_for_tv)
    if sxxeyy:
        season = int(sxxeyy.group(1))
        episode = int(sxxeyy.group(2))
        explicit_episode = True
        media_type = "tv"
    else:
        season_candidate, episode_candidate, explicit_pair = _extract_season_episode_from_name(stem_for_tv)
        if season_candidate is not None and episode_candidate is not None:
            season = season_candidate
            episode = episode_candidate
            explicit_episode = explicit_episode or explicit_pair
            media_type = "tv"

    season = season or _extract_season_from_parts(path)
    if season is not None:
        media_type = "tv"

    episode_from_name, explicit_from_name = _extract_episode_from_name(stem_for_tv)
    explicit_episode = explicit_episode or explicit_from_name
    episode = episode or episode_from_name
    if episode is not None and (has_tv_context or has_tv_hint or season is not None or explicit_episode):
        media_type = "tv"

    if guess.get("type") == "episode":
        guessed_season = guess.get("season")
        guessed_episode = guess.get("episode")
        if has_tv_context or has_tv_hint or season is not None or guessed_season is not None or explicit_episode:
            media_type = "tv"
            season = season or guessed_season
            episode = episode or guessed_episode

    title = guess.get("title") or path.stem
    if media_type == "movie" and has_tv_hint:
        media_type = "tv"

    if media_type == "movie":
        cleaned = _clean_title_from_stem(path.stem)
        if cleaned:
            if _starts_with_number(path.stem) and not _starts_with_number(str(title)):
                title = cleaned
            elif title == path.stem:
                title = cleaned
    if media_type == "tv":
        show_name = _parent_show_name(path)
        if show_name is None:
            parent_name = path.parent.name
            if parent_name and parent_name.strip().lower() not in GENERIC_TV_FOLDERS:
                show_name = parent_name
        title = title_override or show_name or title
        if season is not None:
            title = _strip_season_tokens(str(title))

    year = year_override or _extract_year_range(path.stem) or guess.get("year") or _extract_year(path.stem)
    if season is not None and season >= 1900:
        season = None
    episode_title = _extract_episode_title(stem_for_tv, episode)
    return InferredItem(
        path=path,
        media_type=media_type,
        title=str(title).strip() if title else path.stem,
        year=year,
        season=int(season) if season is not None else None,
        episode=int(episode) if episode is not None else None,
        episode_title=episode_title,
    )
