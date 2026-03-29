from __future__ import annotations

import os
import secrets
import shutil
from pathlib import Path

import pytest
import requests
from rich.prompt import Prompt


@pytest.fixture
def tmp_path() -> Path:
    base = Path(__file__).resolve().parents[1] / ".t"
    base.mkdir(parents=True, exist_ok=True)
    while True:
        candidate = base / secrets.token_hex(4)
        try:
            candidate.mkdir()
            path = candidate
            break
        except FileExistsError:
            continue
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture(autouse=True)
def _pin_temp_dirs(monkeypatch: pytest.MonkeyPatch) -> None:
    base = Path(__file__).resolve().parents[1]
    temp_dir = base / ".t-temp"
    local_app_data = base / ".t-localappdata"
    temp_dir.mkdir(parents=True, exist_ok=True)
    local_app_data.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("TMP", str(temp_dir))
    monkeypatch.setenv("TEMP", str(temp_dir))
    monkeypatch.setenv("TMPDIR", str(temp_dir))
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))


@pytest.fixture(autouse=True)
def _disable_network(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    if request.node.get_closest_marker("network"):
        return

    def _blocked(_self, method: str, url: str, *_args, **_kwargs):
        raise AssertionError(f"Network calls are forbidden in tests: {method} {url}")

    monkeypatch.setattr(requests.sessions.Session, "request", _blocked)


@pytest.fixture(autouse=True)
def _disable_interactive_prompts(monkeypatch: pytest.MonkeyPatch) -> None:
    def _ask(*_args, **kwargs):
        default = kwargs.get("default")
        if default is not None:
            return default
        return "1"

    monkeypatch.setattr(Prompt, "ask", _ask)
