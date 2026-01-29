from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest
import requests
from rich.prompt import Prompt


@pytest.fixture
def tmp_path() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        base = Path(local_app_data) / "Temp" / "plexify-pytest"
        base.mkdir(parents=True, exist_ok=True)
        path = Path(tempfile.mkdtemp(prefix="pytest-plexify-", dir=str(base)))
    else:
        path = Path(tempfile.mkdtemp(prefix="pytest-plexify-"))
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture(autouse=True)
def _disable_network(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    if request.node.get_closest_marker("network"):
        return

    def _blocked(*_args, **_kwargs):
        raise AssertionError("Network disabled in tests")

    monkeypatch.setattr(requests.sessions.Session, "request", _blocked)


@pytest.fixture(autouse=True)
def _disable_interactive_prompts(monkeypatch: pytest.MonkeyPatch) -> None:
    def _ask(*_args, **kwargs):
        default = kwargs.get("default")
        if default is not None:
            return default
        return "1"

    monkeypatch.setattr(Prompt, "ask", _ask)
