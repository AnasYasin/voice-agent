#!/usr/bin/env python3
"""Pre-generate every fixed script line as a WAV. Built in M2.

Turns the agent's fixed speech into disk reads instead of API calls, which
removes a round trip from the response path.

    make cache
"""

from __future__ import annotations

# TODO (M2): walk lang/<code>/prompts/*.yaml, synthesise every static string,
# write into TTSCache.
