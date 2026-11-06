"""Per-decision randomness: every draw a seed decides has its own RNG.

``decision_rng(seed, decision)`` is ``random.Random(sha256(seed + ":" + decision))``, so a
seed gives the same profile byte for byte and the global ``random`` state is never read or
advanced.
"""

from __future__ import annotations

import hashlib
import random


def decision_rng(seed: str, decision: str) -> random.Random:
    return random.Random(
        int.from_bytes(hashlib.sha256(f"{seed}:{decision}".encode()).digest(), "big")
    )
