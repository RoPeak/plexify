from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from guessit import guessit

from .util import NOISE_TOKENS, normalize_title_for_similarity

SEASON_TOKEN_RE = r"(?:season|series|seaon|seson|seasn)"
SEASON_RE = re.compile(rf"(?<![A-Za-z0-9]){SEASON_TOKEN_RE}[-_. ]*(\d{{1,2}})(?![A-Za-z0-9])", re.IGNORECASE)
SXXEYY_RE = re.compile(r"\bs(\d{1,2})e(\d{1,3})\b", re.IGNORECASE)
SXXEYY_RANGE_RE = re.compile(r"\bs(\d{1,2})e(\d{1,3})\s*[-_. ]+\s*e?(\d{1,3})\b", re.IGNORECASE)
XYY_RE = re.compile(r"\b(\d{1,2})x(\d{1,3})\b", re.IGNORECASE)
XYY_RANGE_RE = re.compile(r"\b(\d{1,2})x(\d{1,3})\s*[-_. ]+\s*(\d{1,3})\b", re.IGNORECASE)
SEASON_EP_RE = re.compile(
    rf"(?<![A-Za-z0-9]){SEASON_TOKEN_RE}[-_. ]*(\d{{1,2}})[-_. ]+(?:episode|ep)?[-_. ]*(\d{{1,3}})(?![A-Za-z0-9])",
    re.IGNORECASE,
)
EPISODE_RE = re.compile(r"(?<![A-Za-z0-9])(?:episode|ep)[-_. ]*(\d{1,3})(?![A-Za-z0-9])", re.IGNORECASE)
TV_HINT_RE = re.compile(r"\b(?:series|season|seaon|seson|seasn|episode|ep)\b", re.IGNORECASE)
YEAR_RE = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")
YEAR_RANGE_RE = re.compile(r"(?<!\d)(19\d{2}|20\d{2})\s*[-–]\s*(19\d{2}|20\d{2})(?!\d)")
LEADING_EPISODE_RE = re.compile(r"^\s*(\d{1,3})\s*[-_. ]+\s*(.+?)\s*$")
LEADING_EPISODE_RANGE_RE = re.compile(r"^\s*(\d{1,3})\s*[-_. ]+\s*(\d{1,3})(?:\s*[-_. ]+.*)?\s*$")
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
    episode_end: Optional[int] = None
    episode_title: Optional[str] = None


def _is_generic_tv_folder_name(name: str) -> bool:
    normalized = re.sub(r"[_\s]+", " ", name.strip().lower())
    return normalized in GENERIC_TV_FOLDERS


def _parent_show_name(path: Path) -> Optional[str]:
    for parent in path.parents:
        if not SEASON_RE.search(parent.name):
            continue
        stripped = _strip_season_tokens(parent.name)
        if stripped and stripped != parent.name:
            return stripped
        show_parent = parent.parent
        if show_parent != parent and show_parent.name and not _is_generic_tv_folder_name(show_parent.name):
            return show_parent.name
        return None
    return None


def _extract_season_from_parts(path: Path) -> Optional[int]:
    for part in reversed(path.parts):
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
    cleaned = re.sub(rf"(?i){SEASON_TOKEN_RE}[\s._-]*\d{{1,2}}", "", value)
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


def _strip_bracket_suffix(value: str) -> str:
    cleaned = re.sub(r"\s*[\[(].*?[\])]\s*$", "", value).strip()
    return cleaned or value


def _looks_like_title_fragment(value: str) -> bool:
    tokens = [token for token in re.split(r"[.\s_\-]+", value) if token]
    cleaned: list[str] = []
    for token in tokens:
        lower = token.lower()
        if YEAR_RE.fullmatch(token):
            continue
        if lower in NOISE_TOKENS:
            continue
        if re.fullmatch(r"\d{3,4}p", lower):
            continue
        cleaned.append(token)
    return bool(cleaned)


def infer_tv_episode_from_stem(stem: str) -> Optional[int]:
    cleaned = YEAR_RANGE_RE.sub("", stem).strip()
    cleaned = _strip_bracket_suffix(cleaned)
    cleaned = re.sub(r"[\u2013\u2014\u2212]", "-", cleaned)
    match = LEADING_EPISODE_RE.match(cleaned)
    if not match:
        return None
    return int(match.group(1))


def infer_tv_episode_range_from_stem(stem: str) -> tuple[int, int] | None:
    cleaned = YEAR_RANGE_RE.sub("", stem).strip()
    cleaned = _strip_bracket_suffix(cleaned)
    cleaned = re.sub(r"[\u2013\u2014\u2212]", "-", cleaned)
    match = LEADING_EPISODE_RANGE_RE.match(cleaned)
    if not match:
        return None
    start = int(match.group(1))
    end = int(match.group(2))
    if end <= start:
        return None
    if end - start > 10:
        return None
    return start, end


def infer_movie_title_from_stem(stem: str, guess_title: Optional[str]) -> Optional[str]:
    if " - " not in stem:
        return None
    left, right = stem.split(" - ", 1)
    left = left.strip()
    right = right.strip()
    if not left or not right:
        return None
    if guess_title:
        if normalize_title_for_similarity(guess_title) != normalize_title_for_similarity(left):
            return None
    right = _strip_bracket_suffix(right)
    if not right or not _looks_like_title_fragment(right):
        return None
    return f"{left}: {right}"


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
    episode_end = None
    explicit_episode = False
    has_tv_context = _has_tv_context(path)
    has_tv_hint = TV_HINT_RE.search(path.stem) is not None
    title_override = None
    year_override = None
    guess_title = guess.get("title")
    stem_for_tv = YEAR_RANGE_RE.sub("", path.stem)

    if path.stem.isdigit() and 1 <= len(path.stem) <= 3 and _parent_has_multiple_videos(path):
        media_type = "tv"
        episode = int(path.stem)
        season = season or _extract_season_from_parts(path) or 1
        explicit_episode = True
        show_folder = _parent_show_name(path) or path.parent.name
        title_override, year_override = _clean_parent_show_name(show_folder)
        if title_override:
            has_tv_context = True

    if episode is None:
        leading_episode_range = infer_tv_episode_range_from_stem(stem_for_tv)
        parent_name = path.parent.name.strip()
        if (
            leading_episode_range is not None
            and parent_name
            and not _is_generic_tv_folder_name(parent_name)
            and _parent_has_multiple_videos(path)
        ):
            media_type = "tv"
            episode, episode_end = leading_episode_range
            season = season or _extract_season_from_parts(path) or 1
            explicit_episode = True
            show_folder = _parent_show_name(path) or parent_name
            title_override, year_override = _clean_parent_show_name(show_folder)
            has_tv_context = True

    if episode is None:
        leading_episode = infer_tv_episode_from_stem(stem_for_tv)
        parent_name = path.parent.name.strip()
        if (
            leading_episode is not None
            and parent_name
            and not _is_generic_tv_folder_name(parent_name)
            and _parent_has_multiple_videos(path)
        ):
            media_type = "tv"
            episode = leading_episode
            season = season or _extract_season_from_parts(path) or 1
            explicit_episode = True
            show_folder = _parent_show_name(path) or parent_name
            title_override, year_override = _clean_parent_show_name(show_folder)
            has_tv_context = True

    sxxeyy_range = SXXEYY_RANGE_RE.search(stem_for_tv)
    xyy_range = XYY_RANGE_RE.search(stem_for_tv)
    sxxeyy = SXXEYY_RE.search(stem_for_tv)
    if sxxeyy_range:
        season = int(sxxeyy_range.group(1))
        episode = int(sxxeyy_range.group(2))
        episode_end = int(sxxeyy_range.group(3))
        explicit_episode = True
        media_type = "tv"
    elif xyy_range:
        season = int(xyy_range.group(1))
        episode = int(xyy_range.group(2))
        episode_end = int(xyy_range.group(3))
        explicit_episode = True
        media_type = "tv"
    elif sxxeyy:
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

    if episode is None and (media_type == "tv" or has_tv_context or has_tv_hint or season is not None):
        episode_candidate = infer_tv_episode_from_stem(stem_for_tv)
        if episode_candidate is not None:
            episode = episode_candidate
            media_type = "tv"

    title = guess_title or path.stem
    if media_type == "movie" and has_tv_hint:
        media_type = "tv"

    if media_type == "movie":
        cleaned = _clean_title_from_stem(path.stem)
        if cleaned:
            if _starts_with_number(path.stem) and not _starts_with_number(str(title)):
                title = cleaned
            elif title == path.stem:
                title = cleaned
        subtitle = infer_movie_title_from_stem(path.stem, guess_title)
        if subtitle:
            title = subtitle
    if media_type == "tv":
        show_name = _parent_show_name(path)
        if show_name is None:
            parent_name = path.parent.name
            if parent_name and not _is_generic_tv_folder_name(parent_name):
                show_name = parent_name
        title = title_override or show_name or title
        if season is not None:
            title = _strip_season_tokens(str(title))

    year = year_override or _extract_year_range(path.stem) or guess.get("year") or _extract_year(path.stem)
    if season is not None and season >= 1900:
        season = None
    if episode_end is not None and episode is not None and episode_end <= episode:
        episode_end = None
    episode_title = _extract_episode_title(stem_for_tv, episode)
    return InferredItem(
        path=path,
        media_type=media_type,
        title=str(title).strip() if title else path.stem,
        year=year,
        season=int(season) if season is not None else None,
        episode=int(episode) if episode is not None else None,
        episode_end=int(episode_end) if episode_end is not None else None,
        episode_title=episode_title,
    )
