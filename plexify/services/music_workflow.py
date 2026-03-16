from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests


@dataclass
class AlbumVerificationResult:
    planned_tracks: list[Any]
    album_artist: str | None
    album_title: str | None
    music_decision_payload: dict[str, Any] | None
    verify_remaining: bool
    mb_disabled_reported: bool


def resolve_album_verification(
    *,
    album: Any,
    albums: list[Any],
    idx: int,
    orig_album_artist: str | None,
    orig_album_title: str | None,
    album_artist: str | None,
    album_title: str | None,
    planned_tracks: list[Any],
    folder_multidisc: bool,
    verify: bool,
    verify_remaining: bool,
    mismatch_policy: str,
    music_cache: Any,
    music_decision_key: str,
    release_track_cache: dict[str, list[Any]],
    verification_stats: dict[str, int],
    mb_session: requests.Session | None,
    mb_disabled_reported: bool,
    console: Any,
    helpers: Any,
) -> AlbumVerificationResult:
    search_album_artist = album_artist
    search_album_title = album_title
    music_decision_payload: dict[str, Any] | None = None
    reused_cached_decision = False

    if verify_remaining:
        cached_entry = helpers._normalise_music_decision_entry(music_cache.get_music(music_decision_key))
        if cached_entry is not None:
            decision = str(cached_entry.get("decision"))
            cached_selection_mode = str(cached_entry.get("selection_mode") or "manual")
            cached_artist = cached_entry.get("chosen_artist") or album_artist
            cached_album = cached_entry.get("chosen_album") or album_title
            cached_mbid = cached_entry.get("chosen_mbid")
            if decision == "skip_album":
                verification_stats["skipped_album"] += 1
                music_decision_payload = helpers._build_music_decision_payload(
                    selection_mode=cached_selection_mode,
                    decision="skip_album",
                    reason=cached_entry.get("reason"),
                )
                reused_cached_decision = True
            elif decision == "filename_fallback":
                verification_stats["filename_fallback"] += 1
                music_decision_payload = helpers._build_music_decision_payload(
                    selection_mode=cached_selection_mode,
                    decision="filename_fallback",
                    reason=cached_entry.get("reason"),
                )
                reused_cached_decision = True
            elif decision == "filename_titles_fallback":
                verification_stats["filename_titles_fallback"] += 1
                planned_tracks = helpers._music_tracks_from_filenames(
                    album.tracks,
                    disc_number=album.disc_number,
                    multi_disc=folder_multidisc,
                )
                album_artist = cached_artist
                album_title = cached_album
                music_decision_payload = helpers._build_music_decision_payload(
                    selection_mode=cached_selection_mode,
                    decision="filename_titles_fallback",
                    chosen_mbid=cached_mbid if isinstance(cached_mbid, str) else None,
                    chosen_artist=album_artist,
                    chosen_album=album_title,
                    reason=cached_entry.get("reason"),
                )
                reused_cached_decision = True
            elif decision in {"selected", "order_fallback"} and isinstance(cached_mbid, str):
                mb_tracks: list[Any] | None = None
                if cached_mbid in release_track_cache:
                    mb_tracks = release_track_cache[cached_mbid]
                elif helpers.musicbrainz.is_available():
                    mb_tracks = helpers.musicbrainz.fetch_release_tracks(cached_mbid, session=mb_session)
                    release_track_cache[cached_mbid] = mb_tracks
                if mb_tracks:
                    if decision == "selected":
                        mapped, _reason = helpers._map_musicbrainz_tracks(album.tracks, mb_tracks)
                        if mapped is not None:
                            planned_tracks = mapped
                            album_artist = cached_artist
                            album_title = cached_album
                            if cached_selection_mode == "auto":
                                verification_stats["auto_selected"] += 1
                            else:
                                verification_stats["manual_selected"] += 1
                            music_decision_payload = helpers._build_music_decision_payload(
                                selection_mode=cached_selection_mode,
                                decision="selected",
                                chosen_mbid=cached_mbid,
                                chosen_artist=album_artist,
                                chosen_album=album_title,
                                reason=cached_entry.get("reason"),
                            )
                            reused_cached_decision = True
                    else:
                        planned_tracks = helpers._map_musicbrainz_by_order(album.tracks, mb_tracks)
                        album_artist = cached_artist
                        album_title = cached_album
                        if cached_selection_mode == "auto":
                            verification_stats["auto_selected"] += 1
                        else:
                            verification_stats["manual_selected"] += 1
                        verification_stats["order_fallback"] += 1
                        music_decision_payload = helpers._build_music_decision_payload(
                            selection_mode=cached_selection_mode,
                            decision="order_fallback",
                            chosen_mbid=cached_mbid,
                            chosen_artist=album_artist,
                            chosen_album=album_title,
                            reason=cached_entry.get("reason"),
                        )
                        reused_cached_decision = True
        if reused_cached_decision:
            console.print("Reused cached music decision for this album.")

    if verify_remaining and not reused_cached_decision:
        if not helpers.musicbrainz.is_available():
            if not mb_disabled_reported:
                reason = helpers.musicbrainz.unavailable_reason() or "offline"
                console.print(f"MusicBrainz disabled: {reason}")
                mb_disabled_reported = True
            console.print("Skipped MusicBrainz (offline).")
        else:
            skip_musicbrainz, override_artist, override_reason = helpers._musicbrainz_skip_or_override(album)
            if override_artist is not None:
                search_album_artist = override_artist
            if skip_musicbrainz:
                verification_stats["skipped_album"] += 1
                music_decision_payload = helpers._build_music_decision_payload(
                    selection_mode="manual",
                    decision="skip_album",
                    reason="generic_album_metadata",
                )
                console.print("Skipping MusicBrainz verification for generic album metadata.")
            else:
                if override_reason:
                    console.print(override_reason)
                candidates, search_state, final_search_artist, final_search_title = helpers._search_musicbrainz_candidates_with_retry(
                    artist=search_album_artist,
                    album=search_album_title,
                    year=album.year,
                    session=mb_session,
                )
                search_album_artist = final_search_artist
                search_album_title = final_search_title
                if search_state == "offline":
                    if not mb_disabled_reported:
                        reason = helpers.musicbrainz.unavailable_reason() or "offline"
                        console.print(f"MusicBrainz disabled: {reason}")
                        mb_disabled_reported = True
                    console.print("Skipped MusicBrainz (offline).")
                elif search_state == "skip":
                    verification_stats["skipped_album"] += 1
                    music_decision_payload = helpers._build_music_decision_payload(
                        selection_mode="manual",
                        decision="skip_album",
                        reason="user_skip",
                    )
                    console.print("Skipping MusicBrainz verification for this album.")
                elif search_state == "fallback":
                    verification_stats["filename_fallback"] += 1
                    music_decision_payload = helpers._build_music_decision_payload(
                        selection_mode="manual",
                        decision="filename_fallback",
                        reason="no_matches",
                    )
                    console.print("No MusicBrainz matches found. Using filename metadata.")
                elif not candidates:
                    console.print("No MusicBrainz matches found. Using filename metadata.")
                else:
                    candidates = helpers._rank_music_candidates(
                        candidates,
                        len(album.tracks),
                        orig_album_title,
                        album.year,
                    )
                    auto_decision = helpers._music_auto_verification_decision(
                        candidates,
                        file_track_count=len(album.tracks),
                    )
                    if auto_decision is not None and auto_decision.action == "skip":
                        verification_stats["filename_fallback"] += 1
                        music_decision_payload = helpers._build_music_decision_payload(
                            selection_mode="auto",
                            decision="filename_fallback",
                            reason=auto_decision.reason,
                        )
                        console.print(f"{auto_decision.reason} Using filename metadata.")
                    else:
                        auto_candidate = (
                            auto_decision.candidate
                            if auto_decision is not None and auto_decision.action == "accept"
                            else None
                        )
                        auto_candidate_used = False
                        while True:
                            selection_mode = "manual"
                            if auto_candidate is not None and not auto_candidate_used:
                                selection = auto_candidate
                                auto_candidate_used = True
                                selection_mode = "auto"
                                verification_stats["auto_selected"] += 1
                                if auto_decision is not None:
                                    console.print(auto_decision.reason)
                            else:
                                selection = helpers._select_music_candidate(candidates)
                            if selection == "q":
                                raise helpers.typer.Exit(code=0)
                            if selection == "s":
                                verification_stats["skipped_album"] += 1
                                music_decision_payload = helpers._build_music_decision_payload(
                                    selection_mode=selection_mode,
                                    decision="skip_album",
                                    reason="user_skip",
                                )
                                console.print("Skipping MusicBrainz verification for this album.")
                                break
                            if selection == "skip_all":
                                verification_stats["skipped_remaining"] += max(0, len(albums) - idx)
                                verify_remaining = False
                                music_decision_payload = helpers._build_music_decision_payload(
                                    selection_mode=selection_mode,
                                    decision="skip_album",
                                    reason="user_skip_all",
                                )
                                console.print("Skipping MusicBrainz verification for all remaining albums.")
                                break
                            if not isinstance(selection, helpers.musicbrainz.ReleaseCandidate):
                                break
                            if selection_mode == "manual":
                                verification_stats["manual_selected"] += 1
                            selected_album_artist = selection.artist
                            selected_album_title = selection.title
                            if selection.mbid in release_track_cache:
                                mb_tracks = release_track_cache[selection.mbid]
                            else:
                                mb_tracks = helpers.musicbrainz.fetch_release_tracks(selection.mbid, session=mb_session)
                                release_track_cache[selection.mbid] = mb_tracks
                            if not mb_tracks:
                                if not helpers.musicbrainz.is_available():
                                    if not mb_disabled_reported:
                                        reason = helpers.musicbrainz.unavailable_reason() or "offline"
                                        console.print(f"MusicBrainz disabled: {reason}")
                                        mb_disabled_reported = True
                                    console.print("Skipped MusicBrainz (offline).")
                                else:
                                    console.print("No tracklist found. Using filename metadata.")
                                break

                            if len(mb_tracks) != len(album.tracks):
                                console.print(f"Track count mismatch: files={len(album.tracks)} release={len(mb_tracks)}.")
                                if selection_mode == "manual" and helpers._music_mismatch_is_extreme(
                                    len(album.tracks), len(mb_tracks)
                                ):
                                    keep_release = helpers._confirm(
                                        "Large mismatch for chosen release. Continue with this release? [y/N]",
                                        False,
                                        None,
                                        show_default=False,
                                    )
                                    if not keep_release:
                                        continue
                                if selection_mode == "auto" and helpers._music_mismatch_is_extreme(
                                    len(album.tracks), len(mb_tracks)
                                ):
                                    console.print("Large track-count mismatch after auto-selection. Using filename metadata.")
                                    album_artist = orig_album_artist
                                    album_title = orig_album_title
                                    verification_stats["filename_fallback"] += 1
                                    music_decision_payload = helpers._build_music_decision_payload(
                                        selection_mode=selection_mode,
                                        decision="filename_fallback",
                                        reason="extreme_mismatch_after_auto",
                                    )
                                    break
                                choice = helpers._prompt_music_track_mismatch_choice(None, mismatch_policy=mismatch_policy)
                                if choice == "r":
                                    continue
                                if choice == "o":
                                    planned_tracks = helpers._map_musicbrainz_by_order(album.tracks, mb_tracks)
                                    console.print("Using MusicBrainz titles by track order.")
                                    album_artist = selected_album_artist
                                    album_title = selected_album_title
                                    verification_stats["order_fallback"] += 1
                                    music_decision_payload = helpers._build_music_decision_payload(
                                        selection_mode=selection_mode,
                                        decision="order_fallback",
                                        chosen_mbid=selection.mbid,
                                        chosen_artist=album_artist,
                                        chosen_album=album_title,
                                        reason="track_count_mismatch",
                                    )
                                    break
                                if choice == "t":
                                    planned_tracks = helpers._music_tracks_from_filenames(
                                        album.tracks,
                                        disc_number=album.disc_number,
                                        multi_disc=folder_multidisc,
                                    )
                                    console.print("Using filename titles while keeping MusicBrainz album metadata.")
                                    album_artist = selected_album_artist
                                    album_title = selected_album_title
                                    verification_stats["filename_titles_fallback"] += 1
                                    music_decision_payload = helpers._build_music_decision_payload(
                                        selection_mode=selection_mode,
                                        decision="filename_titles_fallback",
                                        chosen_mbid=selection.mbid,
                                        chosen_artist=album_artist,
                                        chosen_album=album_title,
                                        reason="track_count_mismatch_filename_titles",
                                    )
                                    break
                                console.print("Using filename metadata.")
                                album_artist = orig_album_artist
                                album_title = orig_album_title
                                verification_stats["filename_fallback"] += 1
                                music_decision_payload = helpers._build_music_decision_payload(
                                    selection_mode=selection_mode,
                                    decision="filename_fallback",
                                    chosen_mbid=selection.mbid,
                                    reason="track_count_mismatch",
                                )
                                break

                            mapped, reason = helpers._map_musicbrainz_tracks(album.tracks, mb_tracks)
                            if reason:
                                if reason == "Multi-disc release without disc numbers in filenames":
                                    mapped = helpers._map_musicbrainz_by_order(album.tracks, mb_tracks)
                                    console.print(
                                        "Warning: Multi-disc release without disc numbers in filenames. "
                                        "Using MusicBrainz order with disc-prefixed track numbers."
                                    )
                                    album_artist = selected_album_artist
                                    album_title = selected_album_title
                                else:
                                    console.print(f"Warning: {reason}.")
                                    if helpers._confirm("Fallback to filename titles? [Y/n]", True, None, show_default=False):
                                        mapped = None
                                        album_artist = orig_album_artist
                                        album_title = orig_album_title
                                        verification_stats["filename_fallback"] += 1
                                        music_decision_payload = helpers._build_music_decision_payload(
                                            selection_mode=selection_mode,
                                            decision="filename_fallback",
                                            chosen_mbid=selection.mbid,
                                            reason=reason,
                                        )
                                    else:
                                        mapped = helpers._map_musicbrainz_by_order(album.tracks, mb_tracks)
                                        console.print("Using MusicBrainz titles by track order.")
                                        album_artist = selected_album_artist
                                        album_title = selected_album_title
                                        verification_stats["order_fallback"] += 1
                                        music_decision_payload = helpers._build_music_decision_payload(
                                            selection_mode=selection_mode,
                                            decision="order_fallback",
                                            chosen_mbid=selection.mbid,
                                            chosen_artist=album_artist,
                                            chosen_album=album_title,
                                            reason=reason,
                                        )
                            if mapped is not None:
                                planned_tracks = mapped
                                album_artist = selected_album_artist
                                album_title = selected_album_title
                                if music_decision_payload is None:
                                    music_decision_payload = helpers._build_music_decision_payload(
                                        selection_mode=selection_mode,
                                        decision="selected",
                                        chosen_mbid=selection.mbid,
                                        chosen_artist=album_artist,
                                        chosen_album=album_title,
                                    )
                            break

    if verify and music_decision_payload is not None:
        music_decision_payload["invalid_track_count"] = int(getattr(album, "invalid_track_count", 0) or 0)

    return AlbumVerificationResult(
        planned_tracks=planned_tracks,
        album_artist=album_artist,
        album_title=album_title,
        music_decision_payload=music_decision_payload,
        verify_remaining=verify_remaining,
        mb_disabled_reported=mb_disabled_reported,
    )
