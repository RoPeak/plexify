from pathlib import Path

from plexify import cli


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

    plans, errors = cli._plan_items(
        incoming=incoming,
        library=library,
        mode="dry-run",
        copy_mode=True,
        interactive=False,
        yes=True,
        min_confidence=0.55,
        extensions=cli.DEFAULT_EXTENSIONS,
        cache_path=library / ".plexify" / "cache.json",
        limit=None,
        show_cache=False,
    )

    assert plans == []
    assert errors == []
    assert called["enrich"] is False
