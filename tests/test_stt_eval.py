"""The scoring in eval/run_stt_eval.py, no network."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _harness():
    path = Path(__file__).resolve().parents[1] / "eval" / "run_stt_eval.py"
    spec = importlib.util.spec_from_file_location("run_stt_eval", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_stt_eval"] = module
    spec.loader.exec_module(module)
    return module


def test_score_counts_words_and_characters() -> None:
    harness = _harness()
    references = ["ها ٽي وڳي ٺيڪ آهي", "مهرباني خدا حافظ"]
    hypotheses = ["ها ٽي وڳي ٺيڪ آهي", "مهرباني خدا"]

    wer, cer = harness.score(references, hypotheses)

    assert wer == 100 * 1 / 8, "one word of eight missing"
    assert 0 < cer < 100, "one word missing is a few characters, not none and not all"


def test_every_recognizer_has_a_language_code_for_each_pack() -> None:
    harness = _harness()
    for codes in harness.LANGUAGE_CODES.values():
        assert set(codes) == {"scribe", "gemini", "deepgram"}
