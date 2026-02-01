from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PathOverlapIssue:
    reason: str
    suggestions: list[str]
    suggestion_path: Path | None = None


class PathOverlapError(ValueError):
    def __init__(self, issue: PathOverlapIssue) -> None:
        super().__init__(issue.reason)
        self.issue = issue


def _casefold_parts(path: Path) -> tuple[str, ...]:
    return tuple(part.casefold() for part in path.parts)


def _resolve_for_compare(path: Path) -> Path:
    try:
        return path.resolve(strict=False)
    except Exception:
        return path.resolve()


def _suggest_paths(source: Path, library: Path, label_source: str, label_library: str) -> tuple[list[str], Path | None]:
    suggestions: list[str] = []
    suggestion_path: Path | None = None
    if source == library:
        suggestion_path = source / "Library"
        suggestions.append(f"Suggested {label_source}: {source / 'Incoming'}")
        suggestions.append(f"Suggested {label_library}: {suggestion_path}")
        return suggestions, suggestion_path
    if library.parent == source and library.name.lower() == "organised":
        suggestion_path = source.parent / "Organised"
        suggestions.append(f"Suggested {label_library}: {suggestion_path}")
        return suggestions, suggestion_path
    if library.is_relative_to(source):
        suggestion_path = source.parent / library.name
        suggestions.append(f"Suggested {label_library}: {suggestion_path}")
        return suggestions, suggestion_path
    if source.is_relative_to(library):
        suggestion_path = library.parent / source.name
        suggestions.append(f"Suggested {label_source}: {suggestion_path}")
    return suggestions, suggestion_path


def validate_non_overlapping(source: Path, library: Path) -> tuple[bool, str, Path | None]:
    resolved_source = _resolve_for_compare(source)
    resolved_library = _resolve_for_compare(library)
    source_parts = _casefold_parts(resolved_source)
    library_parts = _casefold_parts(resolved_library)
    if source_parts == library_parts:
        return False, "Source and library point to the same folder.", resolved_source / "Library"
    if source_parts[: len(library_parts)] == library_parts:
        return (
            False,
            "Source is inside library. This would cause Plexify to re-process its own output.",
            resolved_library.parent / resolved_source.name,
        )
    if library_parts[: len(source_parts)] == source_parts:
        return (
            False,
            "Library is inside source. This would cause Plexify to re-process its own output.",
            resolved_source.parent / "Organised",
        )
    return True, "", None


def check_non_overlapping_paths(
    source: Path,
    library: Path,
    *,
    label_source: str = "Incoming",
    label_library: str = "Library",
) -> PathOverlapIssue | None:
    resolved_source = _resolve_for_compare(source)
    resolved_library = _resolve_for_compare(library)
    source_parts = _casefold_parts(resolved_source)
    library_parts = _casefold_parts(resolved_library)
    if source_parts == library_parts:
        reason = f"{label_source} and {label_library} point to the same folder. This would overwrite your files."
        suggestions, suggestion_path = _suggest_paths(resolved_source, resolved_library, label_source, label_library)
        return PathOverlapIssue(reason=reason, suggestions=suggestions, suggestion_path=suggestion_path)
    if source_parts[: len(library_parts)] == library_parts:
        reason = f"Your {label_source} folder is inside {label_library}. This would cause Plexify to re-process its own output."
        suggestions, suggestion_path = _suggest_paths(resolved_source, resolved_library, label_source, label_library)
        return PathOverlapIssue(reason=reason, suggestions=suggestions, suggestion_path=suggestion_path)
    if library_parts[: len(source_parts)] == source_parts:
        reason = f"Your {label_library} folder is inside {label_source}. This would cause Plexify to re-process its own output."
        suggestions, suggestion_path = _suggest_paths(resolved_source, resolved_library, label_source, label_library)
        return PathOverlapIssue(reason=reason, suggestions=suggestions, suggestion_path=suggestion_path)
    return None


def ensure_non_overlapping_paths(
    source: Path,
    library: Path,
    *,
    label_source: str = "Incoming",
    label_library: str = "Library",
) -> None:
    issue = check_non_overlapping_paths(source, library, label_source=label_source, label_library=label_library)
    if issue is None:
        return
    raise PathOverlapError(issue)
