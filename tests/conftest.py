"""Load .env before test modules import.

Without this, the `live` tests read an empty os.getenv, skip themselves, and
`make test-live` reports success while having called nothing. conftest is
imported before test modules, so the keys are in place by the time
test_stt.py reads them at module level.
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
