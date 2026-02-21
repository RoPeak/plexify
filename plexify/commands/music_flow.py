from __future__ import annotations

from typing import Any


def normalise_music_decision_entry(entry: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(entry, dict):
        return None
    decision = entry.get("decision")
    if not isinstance(decision, str):
        return None
    decision = decision.strip().lower()
    if decision not in {"selected", "filename_fallback", "filename_titles_fallback", "order_fallback", "skip_album"}:
        return None
    selection_mode = entry.get("selection_mode")
    if isinstance(selection_mode, str):
        selection_mode = selection_mode.strip().lower()
    else:
        selection_mode = "manual"
    if selection_mode not in {"auto", "manual"}:
        selection_mode = "manual"
    chosen_mbid = entry.get("chosen_mbid")
    if not isinstance(chosen_mbid, str) or not chosen_mbid.strip():
        chosen_mbid = None
    chosen_artist = entry.get("chosen_artist")
    if not isinstance(chosen_artist, str) or not chosen_artist.strip():
        chosen_artist = None
    chosen_album = entry.get("chosen_album")
    if not isinstance(chosen_album, str) or not chosen_album.strip():
        chosen_album = None
    reason = entry.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        reason = None
    invalid_track_count = entry.get("invalid_track_count")
    if not isinstance(invalid_track_count, int) or invalid_track_count < 0:
        invalid_track_count = 0
    return {
        "selection_mode": selection_mode,
        "decision": decision,
        "chosen_mbid": chosen_mbid,
        "chosen_artist": chosen_artist,
        "chosen_album": chosen_album,
        "reason": reason,
        "invalid_track_count": invalid_track_count,
    }


def build_music_decision_payload(
    *,
    selection_mode: str,
    decision: str,
    cache_version: int,
    now_timestamp: Any,
    chosen_mbid: str | None = None,
    chosen_artist: str | None = None,
    chosen_album: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    mode = selection_mode.strip().lower() if isinstance(selection_mode, str) else "manual"
    if mode not in {"auto", "manual"}:
        mode = "manual"
    payload: dict[str, Any] = {
        "version": cache_version,
        "selection_mode": mode,
        "decision": decision,
        "created_at": now_timestamp(),
    }
    if chosen_mbid:
        payload["chosen_mbid"] = chosen_mbid
    if chosen_artist:
        payload["chosen_artist"] = chosen_artist
    if chosen_album:
        payload["chosen_album"] = chosen_album
    if reason:
        payload["reason"] = reason
    return payload
