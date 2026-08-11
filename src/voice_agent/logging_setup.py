"""Logging. Call setup_logging() once at the top of an entrypoint.

Library modules just use logging.getLogger(__name__) and never configure
anything themselves.
"""

from __future__ import annotations

import logging
import sys


def setup_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-7s %(name)-24s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
