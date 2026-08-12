"""Lint and formatting, enforced as tests so `make test` catches style drift
instead of it landing and being noticed in a later diff.

The rule set is pinned in pyproject.toml under [tool.ruff.lint]. Ruff's
defaults change between releases, so an unpinned list would let a ruff upgrade
fail this suite without a line of project code changing.

Both tests print ruff's own output on failure, so the message tells you what to
fix rather than just that something is wrong.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TARGETS = ["src", "tests", "scripts", "eval"]


def ruff(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "ruff", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )


pytestmark = pytest.mark.skipif(
    ruff("--version").returncode != 0,
    reason="ruff not installed, run make install",
)


def test_no_lint_errors() -> None:
    result = ruff("check", "--output-format=concise", *TARGETS)

    assert result.returncode == 0, (
        f"ruff found lint errors. Fix them, or run `make fmt` for the "
        f"auto-fixable ones:\n\n{result.stdout}{result.stderr}"
    )


def test_everything_is_formatted() -> None:
    result = ruff("format", "--check", *TARGETS)

    assert result.returncode == 0, (
        f"these files are not formatted. Run `make fmt`:\n\n{result.stdout}{result.stderr}"
    )


def test_the_rule_set_is_pinned() -> None:
    """If someone deletes the pinned select list, this suite silently starts
    tracking ruff's defaults and will break on the next upgrade."""
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert "[tool.ruff.lint]" in pyproject
    assert "select" in pyproject
