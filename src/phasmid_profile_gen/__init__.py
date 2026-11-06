"""Generate Phasmid browser profiles.

    from phasmid_profile_gen import generate_profile

    profile = generate_profile("windows", exit_ip=proxy_exit_ip)   # needs the location extra
    profile = generate_profile("linux", seed="0a1b...", timezone="Europe/Berlin",
                               languages=["de-DE", "de", "en-US", "en"])

A profile regenerates byte for byte from its seed and inputs (``dumps``).
"""

from __future__ import annotations

import os
import secrets
from importlib.metadata import PackageNotFoundError, version
from typing import Any

# Submodules are imported by name only: binding a function under a submodule's name here
# (generate, captures) would hide that submodule as an attribute of the package.
from . import generate as _generate
from . import geoip
from . import location as _location
from .captures import Capture, catalog, gpu_key, validate
from .generate import (
    FAMILIES,
    GenerationError,
    MissingCaptures,
    ProfileGenerator,
    RandomCapture,
    Request,
    dumps,
    from_capture,
)
from .location import Location, LocationError

try:
    __version__ = version("phasmid-profile-gen")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0.0.0"

__all__ = [
    "FAMILIES",
    "Capture",
    "GenerationError",
    "Location",
    "LocationError",
    "MissingCaptures",
    "ProfileGenerator",
    "RandomCapture",
    "Request",
    "__version__",
    "catalog",
    "dumps",
    "from_capture",
    "generate_profile",
    "list_captures",
    "list_gpus",
    "locate",
    "new_seed",
    "validate",
]

GeoipDatabase = str | os.PathLike[str] | None


def new_seed() -> str:
    """A fresh profile seed: 32 lowercase hex characters."""
    return secrets.token_hex(16)


def locate(exit_ip: str, seed: str, *, geoip_db: GeoipDatabase = None) -> Location:
    """Where a profile with ``seed`` behind ``exit_ip`` is placed: zone, position, languages.

    ``geoip_db`` is a DB-IP City Lite .mmdb; by default ``$PHASMID_GEOIP_MMDB``, else the pinned
    build, downloaded into the cache on first use (about 60 MB)."""
    return _location.locate(exit_ip, seed, geoip.reader(geoip_db))


def generate_profile(
    os: str,
    seed: str | None = None,
    *,
    exit_ip: str | None = None,
    timezone: str | None = None,
    languages: list[str] | None = None,
    gpu: str | None = None,
    geoip_db: GeoipDatabase = None,
    generator: ProfileGenerator | None = None,
) -> dict[str, Any]:
    """A schema-valid Phasmid profile.

    - ``os``: ``"linux"`` or ``"windows"``.
    - ``seed``: 32 lowercase hex characters; a new one when omitted (read it back from
      ``profile["seed"]``).
    - ``exit_ip`` or ``timezone``, exactly one. An exit IP places the profile where the proxy
      comes out: zone, geolocation, WebRTC public address and, unless ``languages`` is given,
      Chrome's default languages for that country. Generate Windows profiles with one: Windows
      Chrome answers geolocation from the system provider, so a Windows profile with no
      position is a stronger tell than a Linux one.
    - ``languages``: ``navigator.languages``; default ``["en-US", "en"]`` with a timezone.
    - ``gpu``: ``"vendor_id:device_id"`` of a GPU a bundled capture has (see ``list_gpus()``).
    - ``generator``: how the capture behind the profile is chosen (a ``ProfileGenerator``);
      default ``RandomCapture()``, one of the OS's captures drawn uniformly from the seed.

    Raises ``GenerationError`` (``MissingCaptures`` when no capture covers the OS),
    ``LocationError`` or ``geoip.GeoipError``.
    """
    if (exit_ip is None) == (timezone is None):
        raise GenerationError("give exactly one of exit_ip and timezone")
    seed = seed or new_seed()
    found: Location | None = None
    data_note = ""
    if exit_ip is not None:
        db = geoip.reader(geoip_db)
        found = _location.locate(exit_ip, seed, db)
        data_note = f"{db.provenance}, {_location.provenance()}"
        timezone = found.timezone
    assert timezone is not None
    langs = list(languages) if languages else (found.languages if found else ["en-US", "en"])
    profile = _generate.generate(
        os, seed, langs, timezone, gpu, found, data_note, generator=generator
    )
    errors = validate(profile)
    if errors:
        raise GenerationError("generated profile does not validate: " + "; ".join(errors))
    return profile


def list_gpus() -> list[dict[str, Any]]:
    """Every GPU of the bundled captures, per OS, most captures first:
    ``[{"os", "gpu", "renderer", "captures"}]``. ``gpu`` is what ``generate_profile(gpu=)``
    takes."""
    seen: dict[tuple[str, str], dict[str, Any]] = {}
    for capture in catalog():
        gpu = capture.profile["gpu"]
        key = gpu_key(gpu)
        entry = seen.setdefault(
            (capture.family, key),
            {
                "os": capture.family,
                "gpu": key,
                "renderer": gpu["webgl"]["unmasked_renderer"],
                "captures": 0,
            },
        )
        entry["captures"] += 1
    return sorted(seen.values(), key=lambda e: (e["os"], -e["captures"], e["gpu"]))


def list_captures() -> list[dict[str, Any]]:
    """The bundled captures: ``[{"name", "os", "os_version", "gpu", "renderer", "date",
    "dump_sha256"}]``."""
    return [
        {
            "name": c.name,
            "os": c.family,
            "os_version": c.profile["os"]["version"],
            "gpu": gpu_key(c.profile["gpu"]),
            "renderer": c.profile["gpu"]["webgl"]["unmasked_renderer"],
            "date": c.date,
            "dump_sha256": c.dump_sha256,
        }
        for c in catalog()
    ]
