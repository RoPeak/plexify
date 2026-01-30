from plexify import cli


def test_wizard_media_choice_accepts_both(monkeypatch) -> None:
    monkeypatch.setattr(cli, "_prompt_choice", lambda *_args, **_kwargs: "both")
    choice = cli._prompt_choice_loop(
        "Media type",
        cli.WIZARD_MEDIA_CHOICES,
        None,
        allow_empty=False,
        error="error",
        default="movie",
    )
    assert choice == "auto"


def test_wizard_mode_choice_accepts_dry_run(monkeypatch) -> None:
    monkeypatch.setattr(cli, "_prompt_choice", lambda *_args, **_kwargs: "dry run")
    choice = cli._prompt_choice_loop(
        "Mode",
        cli.WIZARD_MODE_CHOICES,
        None,
        allow_empty=False,
        error="error",
        default="dry-run",
    )
    assert choice == "dry-run"
