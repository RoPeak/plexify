from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from guessit import guessit

from .util import NOISE_TOKENS

SEASON_RE = re.compile(r"\b(?:season|series)\s*(\d{1,2})\b", re.IGNORECASE)
SXXEYY_RE = re.compile(r"\bs(\d{1,2})e(\d{1,2})\b", re.IGNORECASE)
EPISODE_RE = re.compile(r"\b(?:episode|ep)\s*(\d{1,3})\b", re.IGNORECASE)
YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")


@dataclass(frozen=True)
class InferredItem:
    path: Path
    media_type: str
    title: str
    year: Optional[int] = None
    season: Optional[int] = None
    episode: Optional[int] = None


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


def _has_tv_context(path: Path) -> bool:
    if SXXEYY_RE.search(path.stem):
        return True
    if any(SEASON_RE.search(parent.name) for parent in path.parents):
        return True
    return False


def infer_item(path: Path) -> InferredItem:
    guess = guessit(path.name)
    media_type = "movie"
    season = None
    episode = None
    explicit_episode = False
    has_tv_context = _has_tv_context(path)

    sxxeyy = SXXEYY_RE.search(path.stem)
    if sxxeyy:
        season = int(sxxeyy.group(1))
        episode = int(sxxeyy.group(2))
        explicit_episode = True
        media_type = "tv"

    season = season or _extract_season_from_parts(path)
    if season is not None:
        media_type = "tv"

    episode_from_name, explicit_from_name = _extract_episode_from_name(path.stem)
    explicit_episode = explicit_episode or explicit_from_name
    episode = episode or episode_from_name
    if episode is not None and (has_tv_context or season is not None or explicit_episode):
        media_type = "tv"

    if guess.get("type") == "episode":
        guessed_season = guess.get("season")
        guessed_episode = guess.get("episode")
        if has_tv_context or season is not None or guessed_season is not None or explicit_episode:
            media_type = "tv"
            season = season or guessed_season
            episode = episode or guessed_episode

    title = guess.get("title") or path.stem
    if media_type == "movie":
        cleaned = _clean_title_from_stem(path.stem)
        if cleaned:
            if _starts_with_number(path.stem) and not _starts_with_number(str(title)):
                title = cleaned
            elif title == path.stem:
                title = cleaned
    if media_type == "tv":
        show_name = _parent_show_name(path)
        title = show_name or title

    year = guess.get("year") or _extract_year(path.stem)
    return InferredItem(
        path=path,
        media_type=media_type,
        title=str(title).strip() if title else path.stem,
        year=year,
        season=int(season) if season is not None else None,
        episode=int(episode) if episode is not None else None,
    )
