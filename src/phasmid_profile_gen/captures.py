"""The capture catalog: real devices, already mapped onto the profile schema.

Each entry was built by scripts/build_data.py from a harness dump of a real
device. The mapping is Phasmid's ``tests/consistency/capture.py``, the code
``consistency capture`` runs, so there is one mapping from a dump to profile
fields. Only what generation reads is bundled, under a neutral name (OS family
and PCI ids); ``data/manifest.json`` names every capture's dump by sha256.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from typing import Any

import jsonschema

from . import _data


@dataclass(frozen=True)
class Capture:
    name: str
    family: str
    gpu: str
    date: str
    dump_sha256: str
    profile: dict[str, Any]
    battery: dict[str, Any] | None
    # media_devices.labels shown after permission. A dump has no capture permission, so it
    # shows no labels; a Phasmid record lists what the host shows after it. A capture
    # without any makes profiles that claim no media device.
    media_device_labels: dict[str, list[str]]


def gpu_key(gpu: dict[str, Any]) -> str:
    return f"{gpu['vendor_id']}:{gpu['device_id']}"


@cache
def catalog() -> tuple[Capture, ...]:
    return tuple(
        Capture(
            name=c["name"],
            family=c["family"],
            gpu=c["gpu"],
            date=c["date"],
            dump_sha256=c["dump_sha256"],
            profile=c["profile"],
            battery=c["battery"],
            media_device_labels=c["media_device_labels"],
        )
        for c in _data.load("captures")["captures"]
    )


def for_os(family: str) -> list[Capture]:
    return [c for c in catalog() if c.family == family]


@cache
def schema() -> dict[str, Any]:
    """Phasmid's profiles/schema.json at the commit the data was built from."""
    return _data.load("schema")


@cache
def _validator() -> jsonschema.Draft202012Validator:
    return jsonschema.Draft202012Validator(schema(), format_checker=jsonschema.FormatChecker())


def validate(profile: dict[str, Any]) -> list[str]:
    """Human-readable schema violations, empty when valid (what ``consistency validate`` runs)."""
    errors = []
    for err in sorted(_validator().iter_errors(profile), key=lambda e: list(e.absolute_path)):
        where = "/".join(str(p) for p in err.absolute_path) or "<root>"
        errors.append(f"{where}: {err.message}")
    return errors
