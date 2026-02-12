from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from collections import Counter
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


_FEAT_SUFFIX_RE = re.compile(r"\s+(?:feat\.?|ft\.?|featuring)\s+.*$", re.IGNORECASE)
_ALBUM_YEAR_SUFFIX_RE = re.compile(r"^(?P<album>.+?)\s+\((?P<year>\d{4})\)$")


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


def parse_track_filename(path: Path, default_artist: str | None = None) -> TrackInfo | None:
    stem = path.stem
    parts = stem.split(" - ", 2)
    if len(parts) == 3:
        number_text, artist, title = (part.strip() for part in parts)
    elif len(parts) == 2 and default_artist:
        number_text, title = (part.strip() for part in parts)
        artist = default_artist.strip()
    else:
        return None
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


def _primary_artist(name: str) -> str:
    cleaned = _FEAT_SUFFIX_RE.sub("", name or "")
    return " ".join(cleaned.split()).strip()


def _dominant_track_artist(tracks: list[TrackInfo]) -> tuple[str | None, float]:
    counts: Counter[str] = Counter()
    labels: dict[str, str] = {}
    total = 0
    for track in tracks:
        display = _primary_artist(track.track_artist)
        key = display.casefold()
        if not key:
            continue
        total += 1
        counts[key] += 1
        labels.setdefault(key, display)
    if not total or not counts:
        return None, 0.0
    top_key, top_count = counts.most_common(1)[0]
    return labels.get(top_key, top_key), top_count / total


def _collect_tracks(
    path: Path,
    extensions: set[str],
    *,
    default_artist: str | None,
) -> tuple[list[TrackInfo], list[Path]]:
    tracks: list[TrackInfo] = []
    invalid: list[Path] = []
    for entry in sorted(path.iterdir()):
        if not entry.is_file():
            continue
        if entry.suffix.lower().lstrip(".") not in extensions:
            continue
        parsed = parse_track_filename(entry, default_artist=default_artist)
        if not parsed:
            invalid.append(entry)
            continue
        tracks.append(parsed)
    return tracks, invalid


def _strip_album_year(name: str) -> str:
    match = _ALBUM_YEAR_SUFFIX_RE.match(name.strip())
    if not match:
        return name.strip()
    return match.group("album").strip()


def _infer_album_metadata(path: Path, source: Path) -> tuple[str | None, str | None]:
    parsed = parse_album_folder(path.name)
    if parsed:
        return parsed[0], parsed[1]

    if path.parent != source:
        artist = path.parent.name.strip()
        album = _strip_album_year(path.name)
        if artist and album:
            return artist, album

    album = path.name.strip()
    if album:
        return None, album
    return None, None


def _is_descendant(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return path != parent


def _discover_album_dirs(source: Path, extensions: set[str]) -> list[Path]:
    candidates: set[Path] = set()
    for entry in source.rglob("*"):
        if entry.is_file() and entry.suffix.lower().lstrip(".") in extensions:
            candidates.add(entry.parent)
    if not candidates:
        return []
    ordered = sorted(candidates, key=lambda path: (len(path.parts), str(path).lower()))
    leaf_dirs: list[Path] = []
    for candidate in ordered:
        if any(_is_descendant(other, candidate) for other in ordered):
            continue
        leaf_dirs.append(candidate)
    return leaf_dirs


def discover_albums(source: Path, extensions: Iterable[str]) -> tuple[list[AlbumGroup], list[str]]:
    exts = {ext.lower().lstrip(".") for ext in extensions}
    errors: list[str] = []
    albums: list[AlbumGroup] = []

    def _build_album(path: Path) -> None:
        artist, album = _infer_album_metadata(path, source)
        if not album:
            errors.append(f"Could not infer album title from folder: {path}")
            return
        tracks, invalid_tracks = _collect_tracks(path, exts, default_artist=artist)
        if not tracks:
            if invalid_tracks:
                errors.append(
                    "No valid tracks found in: "
                    f"{path} (unsupported filename format; expected 'NN - Artist - Title' or 'NN - Title')."
                )
            else:
                errors.append(f"No valid tracks found in: {path}")
            return
        if artist is None:
            dominant_artist, ratio = _dominant_track_artist(tracks)
            if dominant_artist is None or ratio < 0.8:
                errors.append(f"Ambiguous album artist in folder: {path} (no dominant track artist).")
                return
            artist = dominant_artist
        if invalid_tracks:
            errors.append(f"Skipped {len(invalid_tracks)} unsupported track filename(s) in: {path}")
        if not artist:
            errors.append(f"Could not infer album artist from folder: {path}")
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
                artist=artist,
                album=album,
                tracks=sorted(tracks, key=lambda track: (track.track_number, track.source.name.lower())),
                images=images,
                cues=cues,
                logs=logs,
            )
        )

    for entry in _discover_album_dirs(source, exts):
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
