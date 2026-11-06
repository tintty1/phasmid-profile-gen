"""The bundled data: xz-compressed JSON under ``data/``, built by scripts/build_data.py."""

from __future__ import annotations

import json
import lzma
from functools import cache
from importlib.resources import files
from typing import Any


def path(name: str):
    return files(__package__).joinpath("data", name)


@cache
def load(name: str) -> Any:
    """``data/<name>.json.xz``, decompressed and parsed once per process. Callers must not
    mutate what this returns."""
    return json.loads(lzma.decompress(path(f"{name}.json.xz").read_bytes()))


@cache
def manifest() -> dict[str, Any]:
    return json.loads(path("manifest.json").read_text())
