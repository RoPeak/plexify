from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..cache import Cache
from ..infer import InferredItem
from ..util import make_search_query, now_timestamp


def build_search_query(title: str, hint: str | None) -> str:
    base = make_search_query(title) or title.strip()
    parts = [base]
    if hint:
        hint_text = hint.strip()
        if hint_text:
            parts.append(hint_text)
    return " ".join(part for part in parts if part)


def build_movie_fallback_queries(title: str, hint: str | None, year: int | None = None) -> list[str]:
    hint_text = (hint or "").strip()
    base_title = (title or "").strip()
    canonical = build_search_query(base_title, hint_text)
    title_variants: list[str] = [base_title]
    sequel_markers = {"chapter", "part", "volume", "vol", "episode", "tournament", "returns", "return"}

    if ":" in base_title:
        title_variants.append(base_title.split(":", 1)[0].strip())
    if " - " in base_title:
        title_variants.append(base_title.split(" - ", 1)[0].strip())
    normalized_separators = re.sub(r"[:\-]+", " ", base_title).strip()
    if normalized_separators and normalized_separators != base_title:
        title_variants.append(normalized_separators)

    stripped_suffix = re.sub(r"\s*[\(\[].*?[\)\]]\s*$", "", base_title).strip()
    if stripped_suffix:
        title_variants.append(stripped_suffix)
    tokens = base_title.split()
    for index, token in enumerate(tokens[2:], start=2):
        if token.casefold() in sequel_markers:
            trimmed = " ".join(tokens[:index]).strip()
            if trimmed:
                title_variants.append(trimmed)
            break
    if ":" not in base_title and " - " not in base_title and len(tokens) == 2:
        short_franchise = tokens[0].strip()
        if short_franchise:
            title_variants.append(short_franchise)

    candidates: list[str] = []
    if canonical:
        candidates.append(canonical)
    for variant in title_variants:
        if not variant:
            continue
        normalized = make_search_query(variant) or variant
        if normalized:
            candidates.append(normalized)
            if hint_text:
                candidates.append(f"{normalized} {hint_text}".strip())
            if year is not None:
                candidates.append(f"{normalized} {year}".strip())

    seen: set[str] = set()
    queries: list[str] = []
    for candidate in candidates:
        compact = " ".join(candidate.split()).strip()
        if not compact:
            continue
        marker = compact.casefold()
        if marker in seen:
            continue
        seen.add(marker)
        queries.append(compact)
    return queries


def build_tv_fallback_queries(title: str, hint: str | None, year: int | None = None) -> list[str]:
    hint_text = (hint or "").strip()
    base_title = (title or "").strip()
    canonical = build_search_query(base_title, hint_text)
    title_variants: list[str] = [base_title]

    stripped_year = re.sub(r"\s*[\[(]?(19|20)\d{2}[\])\s]*$", "", base_title).strip()
    if stripped_year:
        title_variants.append(stripped_year)
    stripped_suffix = re.sub(r"\s*[\[(].*?[\])]\s*$", "", base_title).strip()
    if stripped_suffix:
        title_variants.append(stripped_suffix)
    if ":" in base_title:
        title_variants.append(base_title.split(":", 1)[0].strip())
    if " - " in base_title:
        title_variants.append(base_title.split(" - ", 1)[0].strip())
    normalized_separators = re.sub(r"[:\-]+", " ", base_title).strip()
    if normalized_separators and normalized_separators != base_title:
        title_variants.append(normalized_separators)

    tokens = [token for token in make_search_query(base_title).split() if token]
    if len(tokens) >= 3:
        title_variants.append(" ".join(tokens[:-1]))
    if len(tokens) >= 4:
        title_variants.append(" ".join(tokens[:2]))

    candidates: list[str] = []
    if canonical:
        candidates.append(canonical)
    for variant in title_variants:
        if not variant:
            continue
        normalized = make_search_query(variant) or variant.strip()
        if normalized:
            candidates.append(normalized)
            if hint_text:
                candidates.append(f"{normalized} {hint_text}".strip())
            if year is not None:
                candidates.append(f"{normalized} {year}".strip())

    seen: set[str] = set()
    queries: list[str] = []
    for candidate in candidates:
        compact = " ".join(candidate.split()).strip()
        if not compact:
            continue
        marker = compact.casefold()
        if marker in seen:
            continue
        seen.add(marker)
        queries.append(compact)
    return queries


def resolve_media_type_override(
    item: InferredItem,
    incoming_root: Path | None,
    cache: Cache,
    media_type_overrides: dict[str, str] | None,
    media_override_key: Any,
    switch_item_media_type: Any,
) -> tuple[InferredItem, str | None]:
    override_key = media_override_key(item.path, incoming_root)
    if override_key is None:
        return item, None
    override_media_type = None
    if media_type_overrides is not None:
        override_media_type = media_type_overrides.get(override_key)
    if override_media_type is None:
        cached = cache.get_show(override_key)
        if cached and cached.get("confirmed_by_user"):
            cached_media_type = str(cached.get("media_type") or "").lower()
            if cached_media_type in {"movie", "tv"}:
                override_media_type = cached_media_type
                if media_type_overrides is not None:
                    media_type_overrides[override_key] = cached_media_type
    if override_media_type in {"movie", "tv"} and override_media_type != item.media_type:
        return switch_item_media_type(item, override_media_type), override_key
    return item, override_key


def persist_media_type_override(
    cache: Cache,
    override_key: str | None,
    media_type: str,
    media_type_overrides: dict[str, str] | None,
) -> bool:
    if override_key is None:
        return True
    if media_type_overrides is not None:
        media_type_overrides[override_key] = media_type
    cache.set_show(
        override_key,
        {
            "media_type": media_type,
            "confirmed_by_user": True,
            "created_at": now_timestamp(),
            "source": "MediaTypeOverride",
        },
    )
    return cache.save_with_status()
