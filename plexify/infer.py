from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from guessit import guessit

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


def _extract_episode_from_name(name: str) -> Optional[int]:
    match = EPISODE_RE.search(name)
    if match:
        return int(match.group(1))
    if name.isdigit():
        return int(name)
    return None


def _extract_year(name: str) -> Optional[int]:
    match = YEAR_RE.search(name)
    if match:
        return int(match.group(1))
    return None


def infer_item(path: Path) -> InferredItem:
    guess = guessit(path.name)
    media_type = "movie"
    season = None
    episode = None

    sxxeyy = SXXEYY_RE.search(path.stem)
    if sxxeyy:
        season = int(sxxeyy.group(1))
        episode = int(sxxeyy.group(2))
        media_type = "tv"

    season = season or _extract_season_from_parts(path)
    if season is not None:
        media_type = "tv"

    episode = episode or _extract_episode_from_name(path.stem)
    if episode is not None:
        media_type = "tv"

    if guess.get("type") == "episode":
        media_type = "tv"
        season = season or guess.get("season")
        episode = episode or guess.get("episode")

    title = guess.get("title") or path.stem
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
