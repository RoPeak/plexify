from __future__ import annotations

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
) -> None:
    if override_key is None:
        return
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
    cache.save()

