from __future__ import annotations

import argparse
import py_compile
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FAST_TESTS = [
    "tests/test_build_command.py",
    "tests/test_cli_help.py",
    "tests/test_matcher_services.py",
    "tests/test_cache_policy.py",
    "tests/test_wizard_logging.py",
    "tests/test_infer.py",
]


def _run(command: list[str]) -> int:
    print("+", " ".join(command))
    completed = subprocess.run(command, cwd=ROOT)
    return int(completed.returncode)


def _tracked_python_files() -> list[Path]:
    completed = subprocess.run(
        ["git", "ls-files", "*.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    files = [ROOT / line.strip() for line in completed.stdout.splitlines() if line.strip()]
    return files


def _compile_tracked_python() -> None:
    for path in _tracked_python_files():
        py_compile.compile(str(path), doraise=True)


def run_fast() -> int:
    _compile_tracked_python()
    if _run([sys.executable, "-m", "plexify.cli", "--help"]) != 0:
        return 1
    return _run([sys.executable, "-m", "pytest", "-q", *FAST_TESTS])


def run_push() -> int:
    return _run([sys.executable, "-m", "pytest", "-q"])


def main() -> int:
    parser = argparse.ArgumentParser(description="Local CI helper for hooks.")
    parser.add_argument("mode", choices=["fast", "push"])
    args = parser.parse_args()
    if args.mode == "fast":
        return run_fast()
    return run_push()


if __name__ == "__main__":
    raise SystemExit(main())
