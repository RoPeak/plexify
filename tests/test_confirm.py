from plexify import cli


def test_confirm_reprompts_then_accepts_yes(monkeypatch) -> None:
    answers = iter(["maybe", "y"])
    prompts: list[str] = []

    monkeypatch.setattr(cli, "_prompt_choice", lambda *_args, **_kwargs: next(answers))
    monkeypatch.setattr(cli, "_safe_print", lambda message, _progress=None: prompts.append(message))

    assert cli._confirm("Proceed?", True, None) is True
    assert prompts == ["Please enter y/n."]


def test_confirm_reprompts_then_accepts_no(monkeypatch) -> None:
    answers = iter(["?", "n"])
    prompts: list[str] = []

    monkeypatch.setattr(cli, "_prompt_choice", lambda *_args, **_kwargs: next(answers))
    monkeypatch.setattr(cli, "_safe_print", lambda message, _progress=None: prompts.append(message))

    assert cli._confirm("Proceed?", False, None) is False
    assert prompts == ["Please enter y/n."]


def test_confirm_passes_show_default_false(monkeypatch) -> None:
    seen: list[bool] = []

    def _fake_prompt_choice(*_args, **kwargs):
        seen.append(kwargs.get("show_default"))
        return "y"

    monkeypatch.setattr(cli, "_prompt_choice", _fake_prompt_choice)

    assert cli._confirm("Proceed?", True, None, show_default=False) is True
    assert seen == [False]
