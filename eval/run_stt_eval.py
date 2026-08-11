#!/usr/bin/env python3
"""Score STT providers on real Urdu phone audio.

Two numbers per provider:

  WER            how wrong the transcript is
  slot accuracy  how often the answer was still extractable

Slot accuracy is the one that decides the project. A 30% WER transcript that
still yields "confirm, 15:00" every time is a shippable system.
"""

from __future__ import annotations

# TODO
