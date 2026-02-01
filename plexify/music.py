from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import re

from .util import sanitise_name


@dataclass(frozen=True)
class TrackInfo:
    source: Path
    track_number: int
    track_title: str
    track_artist: str
    ext: str


@dataclass(frozen=True)
class AlbumGroup:
    source: Path
    artist: str
    album: str
    tracks: list[TrackInfo]
    images: list[Path]
    cues: list[Path]
    logs: list[Path]


def parse_album_folder(name: str) -> tuple[str, str] | None:
    if not name:
        return None
    if " - " not in name:
        return None
    artist, album = name.split(" - ", 1)
    artist = artist.strip()
    album = album.strip()
    if not artist or not album:
        return None
    return artist, album


def parse_track_filename(path: Path) -> TrackInfo | None:
    stem = path.stem
    parts = stem.split(" - ", 2)
    if len(parts) != 3:
        return None
    number_text, artist, title = (part.strip() for part in parts)
    if not number_text.isdigit():
        return None
    track_number = int(number_text)
    if track_number <= 0:
        return None
    if not artist or not title:
        return None
    return TrackInfo(
        source=path,
        track_number=track_number,
        track_title=title,
        track_artist=artist,
        ext=path.suffix,
    )


def _collect_tracks(path: Path, extensions: set[str], errors: list[str]) -> list[TrackInfo]:
    tracks: list[TrackInfo] = []
    for entry in sorted(path.iterdir()):
        if not entry.is_file():
            continue
        if entry.suffix.lower().lstrip(".") not in extensions:
            continue
        parsed = parse_track_filename(entry)
        if not parsed:
            errors.append(f"Unrecognised track name: {entry}")
            continue
        tracks.append(parsed)
    return tracks


def discover_albums(source: Path, extensions: Iterable[str]) -> tuple[list[AlbumGroup], list[str]]:
    exts = {ext.lower().lstrip(".") for ext in extensions}
    errors: list[str] = []
    albums: list[AlbumGroup] = []

    def _build_album(path: Path) -> None:
        parsed = parse_album_folder(path.name)
        if not parsed:
            errors.append(f"Unrecognised album folder name: {path}")
            return
        tracks = _collect_tracks(path, exts, errors)
        if not tracks:
            errors.append(f"No valid tracks found in: {path}")
            return
        images = sorted(
            entry
            for entry in path.iterdir()
            if entry.is_file() and entry.suffix.lower() in {".jpg", ".jpeg", ".png"}
        )
        cues = sorted(entry for entry in path.iterdir() if entry.is_file() and entry.suffix.lower() == ".cue")
        logs = sorted(entry for entry in path.iterdir() if entry.is_file() and entry.suffix.lower() == ".log")
        albums.append(
            AlbumGroup(
                source=path,
                artist=parsed[0],
                album=parsed[1],
                tracks=sorted(tracks, key=lambda track: (track.track_number, track.source.name.lower())),
                images=images,
                cues=cues,
                logs=logs,
            )
        )

    if any(entry.is_file() and entry.suffix.lower().lstrip(".") in exts for entry in source.iterdir()):
        _build_album(source)
    else:
        for entry in sorted(source.iterdir()):
            if not entry.is_dir():
                continue
            _build_album(entry)

    return albums, errors


def select_best_artwork(images: list[Path]) -> Path | None:
    if not images:
        return None
    cover_like = [img for img in images if "cover" in img.stem.lower() or "folder" in img.stem.lower()]
    candidates = cover_like or images
    return max(candidates, key=lambda path: path.stat().st_size)


def format_track_number(track_number: int, *, disc_number: int | None = None, multi_disc: bool = False) -> str:
    if multi_disc and disc_number is not None:
        return f"{disc_number}{track_number:02d}"
    if track_number >= 100:
        return f"{track_number:03d}"
    return f"{track_number:02d}"


def album_destination(library: Path, artist: str, album: str) -> Path:
    safe_artist = sanitise_name(artist)
    safe_album = sanitise_name(album)
    return library / "Music" / safe_artist / safe_album


def track_destination(
    library: Path,
    artist: str,
    album: str,
    track_number_text: str,
    track_title: str,
    ext: str,
) -> Path:
    safe_artist = sanitise_name(artist)
    safe_album = sanitise_name(album)
    safe_title = _safe_track_title(track_title, track_number_text)
    filename = f"{track_number_text} - {safe_title}{ext}"
    return library / "Music" / safe_artist / safe_album / filename


def _safe_track_title(title: str, track_number_text: str) -> str:
    cleaned = title.strip() if title else ""
    stripped = re.sub(r"[\W_]+", "", cleaned).casefold()
    if not stripped:
        return f"Track {track_number_text}"
    if stripped == "untitled":
        return "Untitled"
    safe = sanitise_name(cleaned)
    if not safe or safe == "Unknown":
        return f"Track {track_number_text}"
    if re.sub(r"[\W_]+", "", safe).casefold() in {""}:
        return f"Track {track_number_text}"
    return safe
