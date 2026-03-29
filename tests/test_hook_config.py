from pathlib import Path


def test_pre_commit_config_references_local_ci_script() -> None:
    config = Path(".pre-commit-config.yaml").read_text(encoding="utf-8")
    assert "python scripts/local_ci.py fast" in config
    assert "python scripts/local_ci.py push" in config
    assert "pre-commit" in config
    assert "pre-push" in config
