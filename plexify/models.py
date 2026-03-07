from __future__ import annotations

from typing import Any, TypedDict


class CacheData(TypedDict):
    schema_version: int
    shows: dict[str, dict[str, Any]]
    movies: dict[str, dict[str, Any]]
    enrichment: dict[str, dict[str, Any]]
    music: dict[str, dict[str, Any]]


class ReportOperation(TypedDict):
    source: str
    destination: str
    media_type: str | None
    metadata: dict[str, Any] | None


class ReportPayload(TypedDict):
    mode: str
    copy: bool
    operations: list[ReportOperation]
