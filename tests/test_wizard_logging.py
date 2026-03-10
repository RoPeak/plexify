from pathlib import Path

from plexify import cli


def test_wizard_prompts_logging_and_passes_to_video_flow(monkeypatch) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr(cli, "_initialise_logging", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "log_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_confirm", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(cli, "_prompt_text", lambda *_args, **_kwargs: ".plexify/custom.log")

    def _fake_choice_loop(prompt, *_args, **_kwargs):
        if prompt.startswith("Log level"):
            return "DEBUG"
        if prompt.startswith("Log format"):
            return "json"
        if prompt.startswith("Organise"):
            return "video"
        raise AssertionError(f"Unexpected prompt: {prompt}")

    monkeypatch.setattr(cli, "_prompt_choice_loop", _fake_choice_loop)

    def _fake_wizard_video(*, log_level: str, log_format: str, log_file: Path | None) -> None:
        captured["log_level"] = log_level
        captured["log_format"] = log_format
        captured["log_file"] = log_file

    monkeypatch.setattr(cli, "_wizard_video", _fake_wizard_video)
    monkeypatch.setattr(cli, "_wizard_music", lambda **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected music")))

    cli.wizard(log_level="INFO", log_format="text", log_file=None)

    assert captured["log_level"] == "DEBUG"
    assert captured["log_format"] == "json"
    assert captured["log_file"] == Path(".plexify/custom.log")


def test_wizard_video_passes_safe_defaults_to_runtime_organise(monkeypatch) -> None:
    incoming = Path("plexify")
    library = Path("tests")

    monkeypatch.setattr(cli, "_prompt_non_overlapping_paths", lambda **_kwargs: (incoming, library))
    monkeypatch.setattr(cli, "_save_wizard_prefs", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_detect_media_in_path", lambda *_args, **_kwargs: (False, True))
    monkeypatch.setattr(
        cli,
        "_prompt_choice_loop",
        lambda prompt, *_args, **_kwargs: (
            "movie"
            if prompt.startswith("Media type")
            else "dry-run"
        ),
    )
    monkeypatch.setattr(cli, "_confirm", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(cli, "_prompt_text", lambda *_args, **_kwargs: str(cli.DEFAULT_MIN_CONFIDENCE))
    monkeypatch.setattr(cli, "_build_command", lambda *_args, **_kwargs: "python -m plexify.cli organise")

    captured: dict[str, object] = {}

    def _fake_run_organise(options: cli.OrganiseOptions):
        captured["offline"] = options.offline
        captured["strict_safe"] = options.strict_safe
        captured["allow_risky_enter_accept"] = options.allow_risky_enter_accept

    monkeypatch.setattr(cli, "run_organise", _fake_run_organise)

    cli._wizard_video(log_level="INFO", log_format="text", log_file=None)

    assert captured.get("offline") is False
    assert captured.get("strict_safe") is False
    assert captured.get("allow_risky_enter_accept") is False


def test_wizard_video_command_and_runtime_flags_stay_aligned(monkeypatch) -> None:
    incoming = Path("plexify")
    library = Path("tests")

    monkeypatch.setattr(cli, "_prompt_non_overlapping_paths", lambda **_kwargs: (incoming, library))
    monkeypatch.setattr(cli, "_save_wizard_prefs", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_detect_media_in_path", lambda *_args, **_kwargs: (False, True))
    monkeypatch.setattr(
        cli,
        "_prompt_choice_loop",
        lambda prompt, *_args, **_kwargs: ("movie" if prompt.startswith("Media type") else "dry-run"),
    )
    monkeypatch.setattr(cli, "_confirm", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(cli, "_prompt_text", lambda *_args, **_kwargs: str(cli.DEFAULT_MIN_CONFIDENCE))

    captured: dict[str, object] = {}

    def _fake_build_command(config: cli.BuildCommandConfig) -> str:
        captured["command_strict_safe"] = config.strict_safe
        captured["command_allow_risky_enter_accept"] = config.allow_risky_enter_accept
        return "python -m plexify.cli organise"

    def _fake_run_organise(options: cli.OrganiseOptions) -> None:
        captured["runtime_strict_safe"] = options.strict_safe
        captured["runtime_allow_risky_enter_accept"] = options.allow_risky_enter_accept

    monkeypatch.setattr(cli, "_build_command", _fake_build_command)
    monkeypatch.setattr(cli, "run_organise", _fake_run_organise)

    cli._wizard_video(log_level="INFO", log_format="text", log_file=None)

    assert captured["command_strict_safe"] is False
    assert captured["runtime_strict_safe"] is False
    assert captured["command_allow_risky_enter_accept"] is False
    assert captured["runtime_allow_risky_enter_accept"] is False


def test_wizard_keeps_log_file_none_when_not_enabled(monkeypatch) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr(cli, "_initialise_logging", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "log_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_confirm", lambda *_args, **_kwargs: False)

    def _fake_choice_loop(prompt, *_args, **_kwargs):
        if prompt.startswith("Log level"):
            return "INFO"
        if prompt.startswith("Log format"):
            return "text"
        if prompt.startswith("Organise"):
            return "video"
        raise AssertionError(f"Unexpected prompt: {prompt}")

    monkeypatch.setattr(cli, "_prompt_choice_loop", _fake_choice_loop)

    def _fake_wizard_video(*, log_level: str, log_format: str, log_file: Path | None) -> None:
        captured["log_level"] = log_level
        captured["log_format"] = log_format
        captured["log_file"] = log_file

    monkeypatch.setattr(cli, "_wizard_video", _fake_wizard_video)
    monkeypatch.setattr(cli, "_wizard_music", lambda **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected music")))

    cli.wizard(log_level="INFO", log_format="text", log_file=None)

    assert captured["log_level"] == "INFO"
    assert captured["log_format"] == "text"
    assert captured["log_file"] is None
