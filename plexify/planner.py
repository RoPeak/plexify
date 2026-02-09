from __future__ import annotations

from pathlib import Path

from .util import sanitise_name


def plan_movie(library: Path, title: str, year: int | None, ext: str) -> Path:
    year_text = str(year) if year else "Unknown Year"
    safe_title = sanitise_name(title)
    safe_year = sanitise_name(year_text)
    folder = library / "Movies" / f"{safe_title} ({safe_year})"
    filename = f"{safe_title} ({safe_year}){ext}"
    return folder / filename


def plan_tv_show(
    library: Path,
    show_name: str,
    year: int | None,
    season: int,
    episode: int,
    episode_end: int | None,
    episode_title: str | None,
    ext: str,
) -> Path:
    year_text = str(year) if year else "Unknown Year"
    safe_show = sanitise_name(show_name)
    safe_year = sanitise_name(year_text)
    season_folder = f"Season {season:02d}"
    episode_title = episode_title or f"Episode {episode:02d}"
    safe_episode = sanitise_name(episode_title)
    episode_token = f"s{season:02d}e{episode:02d}"
    if episode_end is not None and episode_end > episode:
        episode_token = f"{episode_token}-e{episode_end:02d}"
    filename = f"{safe_show} ({safe_year}) - {episode_token} - {safe_episode}{ext}"
    return library / "TV Shows" / f"{safe_show} ({safe_year})" / season_folder / filename
