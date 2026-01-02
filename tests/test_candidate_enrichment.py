from pathlib import Path

from rich.console import Console

from plexify import cli
from plexify.infer import InferredItem


def test_candidate_table_handles_missing_enrichment() -> None:
    candidates = [
        cli.Candidate(
            title="Example",
            year=2000,
            source="Wikidata",
            confidence=0.5,
            metadata={},
            enrichment=None,
        )
    ]
    cli._print_candidates("movie", candidates)
    cli._print_candidates("tv", candidates)


def test_tv_candidate_table_uses_metadata_episode_title(monkeypatch) -> None:
    recorder = Console(record=True, force_terminal=False, width=120)
    monkeypatch.setattr(cli, "console", recorder)
    item = InferredItem(
        path=Path("Show.S01E01.mkv"),
        media_type="tv",
        title="Show",
        year=None,
        season=1,
        episode=1,
        episode_title=None,
    )
    candidates = [
        cli.Candidate(
            title="Show",
            year=2020,
            source="TVMaze",
            confidence=0.9,
            metadata={"id": 1, "episode_title": "Pilot"},
            enrichment=None,
        )
    ]
    cli._print_candidates("tv", candidates, item=item)
    output = recorder.export_text()
    assert "Pilot" in output


def test_enrichment_not_called_in_non_interactive(monkeypatch, tmp_path: Path) -> None:
    called = {"enrich": False}

    def _enrich(*_args, **_kwargs) -> None:
        called["enrich"] = True

    def _empty_candidates(*_args, **_kwargs) -> cli.CandidatePage:
        return cli.CandidatePage(candidates=[], raw_results=[], next_offset=0, has_more=False)

    monkeypatch.setattr(cli, "_maybe_enrich_candidates", _enrich)
    monkeypatch.setattr(cli, "_movie_candidates", _empty_candidates)

    incoming = tmp_path / "incoming"
    library = tmp_path / "library"
    incoming.mkdir()
    library.mkdir()
    (incoming / "Movie (2000).mkv").write_text("x", encoding="utf-8")

    plans, errors, _stats = cli._plan_items(
        incoming=incoming,
        library=library,
        mode="dry-run",
        copy_mode=True,
        interactive=False,
        auto_accept=True,
        min_confidence=0.55,
        extensions=cli.DEFAULT_EXTENSIONS,
        cache_path=library / ".plexify" / "cache.json",
        limit=None,
        show_cache=False,
        media_type_filter=None,
        use_cache=True,
        on_conflict="rename",
    )

    assert plans == []
    assert errors == []
    assert called["enrich"] is False
