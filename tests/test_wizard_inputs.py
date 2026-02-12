from pathlib import Path

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


def test_wizard_choice_loop_reprompts_once_on_invalid(monkeypatch) -> None:
    answers = iter(["invalid", "tv"])
    messages: list[str] = []
    monkeypatch.setattr(cli, "_prompt_choice", lambda *_args, **_kwargs: next(answers))
    monkeypatch.setattr(cli, "_safe_print", lambda message, _progress=None: messages.append(str(message)))

    choice = cli._prompt_choice_loop(
        "Media type",
        cli.WIZARD_MEDIA_CHOICES,
        None,
        allow_empty=False,
        error="error",
        default="movie",
    )

    assert choice == "tv"
    assert messages == ["error"]


def test_wizard_defaults_ignore_cwd_prefill(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        cli,
        "_load_wizard_prefs",
        lambda: {
            "music": {
                "source": str(Path.cwd()),
                "library": str(tmp_path / "library"),
            }
        },
    )

    source_default, library_default = cli._wizard_defaults("music")

    assert source_default is None
    assert library_default == tmp_path / "library"


def test_wizard_music_apply_uses_copy_move_choice(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "source"
    library = tmp_path / "library"

    prompts: list[str] = []
    confirm_prompts: list[str] = []
    captured: dict[str, object] = {}

    monkeypatch.setattr(cli, "_prompt_non_overlapping_paths", lambda **_kwargs: (source, library))
    monkeypatch.setattr(cli, "_save_wizard_prefs", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_detect_media_in_path", lambda *_args, **_kwargs: (True, False))

    def _fake_prompt_choice_loop(prompt, *_args, **_kwargs):
        prompts.append(prompt)
        if prompt.startswith("Mode"):
            return "apply"
        if prompt.startswith("Copy or move?"):
            return "move"
        if prompt.startswith("Plan output"):
            return "summary"
        raise AssertionError(f"Unexpected prompt: {prompt}")

    monkeypatch.setattr(cli, "_prompt_choice_loop", _fake_prompt_choice_loop)
    monkeypatch.setattr(
        cli,
        "_confirm",
        lambda prompt, *_args, **_kwargs: confirm_prompts.append(prompt) or False,
    )
    monkeypatch.setattr(cli, "music", lambda **kwargs: captured.update(kwargs))

    cli._wizard_music(log_level="INFO", log_format="text", log_file=None)

    assert "Copy or move? (copy/move)" in prompts
    assert all("Copy files instead of moving?" not in prompt for prompt in confirm_prompts)
    assert captured["apply"] is True
    assert captured["copy"] is False
    assert captured["plan_preview_tracks"] == 0


def test_wizard_music_preview_mode_sets_preview_tracks(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "source"
    library = tmp_path / "library"
    captured: dict[str, object] = {}

    monkeypatch.setattr(cli, "_prompt_non_overlapping_paths", lambda **_kwargs: (source, library))
    monkeypatch.setattr(cli, "_save_wizard_prefs", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_detect_media_in_path", lambda *_args, **_kwargs: (True, False))
    monkeypatch.setattr(
        cli,
        "_prompt_choice_loop",
        lambda prompt, *_args, **_kwargs: "dry-run" if prompt.startswith("Mode") else "preview",
    )
    monkeypatch.setattr(cli, "_prompt_int", lambda *_args, **_kwargs: 3)
    monkeypatch.setattr(cli, "_confirm", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(cli, "music", lambda **kwargs: captured.update(kwargs))

    cli._wizard_music(log_level="INFO", log_format="text", log_file=None)

    assert captured["verbose_plan"] is False
    assert captured["plan_preview_tracks"] == 3
