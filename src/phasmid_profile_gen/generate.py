"""One profile from (os, seed, languages, timezone), or (os, seed, exit IP).

Every device field comes from one capture of a real device (captures.py):
the OS identity, hardware, display and window, pointer and preferences, GPU,
fonts, voices, audio, codecs, system colours, media devices and battery. No
field is sampled from a population model or derived for another GPU.

Which capture backs a profile is a ``ProfileGenerator``'s choice. The one used
by default, ``RandomCapture``, draws one of the OS's captures uniformly from
the seed. Another strategy implements the same protocol and is passed as
``generator=``; ``from_capture`` builds the profile once a capture is chosen.

No user-agent, brand or version string is copied: the schema has no field
for one, and the browser builds them from its own version.

With a proxy exit IP the location group comes from it (location.py): the zone,
the served position, the WebRTC public address and, unless given, the
languages.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from . import locales
from ._rng import decision_rng
from .captures import Capture, catalog, gpu_key
from .location import Location

FAMILIES = ("linux", "windows")
SEED_RE = re.compile(r"[0-9a-f]{32}")
# The profile's media device kinds.
_DEVICE_KINDS = ("audioinput", "audiooutput", "videoinput")


class GenerationError(Exception):
    pass


class MissingCaptures(GenerationError):
    """No bundled capture covers the OS (or the requested GPU on it). Nothing is invented."""

    def __init__(self, family: str, gpu: str | None = None):
        self.family = family
        self.gpu = gpu
        what = f"os={family}" + (f" with gpu {gpu}" if gpu else "")
        super().__init__(f"no bundled capture for {what}; device data is never invented")


def dumps(profile: dict[str, Any]) -> str:
    return json.dumps(profile, indent=1, sort_keys=True) + "\n"


@dataclass(frozen=True)
class Request:
    """What the caller asked for, already checked: a known OS family, a well-formed seed and,
    with a location, its zone as ``timezone``."""

    family: str
    seed: str
    languages: tuple[str, ...]
    timezone: str
    gpu: str | None = None  # "vendor_id:device_id"; None leaves the GPU to the generator
    location: Location | None = None
    location_note: str = ""  # the data the location came from, for captured.note


@runtime_checkable
class ProfileGenerator(Protocol):
    """Turns a request into an unvalidated profile. The same request must give the same
    profile; any randomness comes from ``request.seed`` (``_rng.decision_rng``), never from
    the global ``random`` state. Raise ``MissingCaptures`` when no capture can back it."""

    def generate(self, request: Request) -> dict[str, Any]: ...


class RandomCapture:
    """One of the OS's captures (those with the requested GPU, when one is), drawn uniformly
    from the seed, taken whole."""

    def __init__(self, captures: Iterable[Capture] | None = None):
        self._captures = tuple(captures) if captures is not None else None

    @property
    def captures(self) -> tuple[Capture, ...]:
        return self._captures if self._captures is not None else catalog()

    def candidates(self, family: str, gpu: str | None = None) -> list[Capture]:
        return [
            c
            for c in self.captures
            if c.family == family and (gpu is None or gpu_key(c.profile["gpu"]) == gpu)
        ]

    def generate(self, request: Request) -> dict[str, Any]:
        candidates = self.candidates(request.family, request.gpu)
        if not candidates:
            raise MissingCaptures(request.family, request.gpu)
        capture = decision_rng(request.seed, "capture").choice(candidates)
        return from_capture(
            capture,
            request,
            how=f"drawn uniformly from the seed among {len(candidates)} {request.family} "
            "capture" + ("s" if len(candidates) != 1 else ""),
        )


def generate(
    family: str,
    seed: str,
    languages: list[str],
    timezone: str,
    gpu: str | None = None,
    location: Location | None = None,
    location_note: str = "",
    *,
    generator: ProfileGenerator | None = None,
) -> dict[str, Any]:
    """The profile, unvalidated. ``location``, when given, sets the zone, position and WebRTC
    address; ``timezone`` must then be its zone. ``location_note`` names the data it came from.
    ``generator`` defaults to ``RandomCapture()``. ``generate_profile`` in the package root is
    the checked entry point."""
    if location is not None and timezone != location.timezone:
        raise GenerationError(
            f"timezone {timezone} differs from the exit IP's zone {location.timezone}"
        )
    if family not in FAMILIES:
        raise GenerationError(f"os must be one of {', '.join(FAMILIES)}")
    if not SEED_RE.fullmatch(seed):
        raise GenerationError("seed must be 32 lowercase hex characters")
    request = Request(family, seed, tuple(languages), timezone, gpu, location, location_note)
    return (generator or RandomCapture()).generate(request)


def from_capture(capture: Capture, request: Request, *, how: str = "") -> dict[str, Any]:
    """The profile one capture backs for a request: the capture's device data, its own copy,
    with the request's identity and locale. ``how`` says how the capture was chosen, for
    captured.note."""
    if capture.family != request.family:
        raise GenerationError(f"capture {capture.name} is {capture.family}, not {request.family}")
    profile: dict[str, Any] = copy.deepcopy(capture.profile)
    display = profile["display"]
    profile.update(
        {
            "schema_version": 1,
            "id": f"gen-{request.family}-{display['width']}x{display['height']}-{request.seed[:8]}",
            "seed": request.seed,
            "captured": {
                "note": (
                    f"Generated by phasmid-profile-gen, not captured: device data from capture "
                    f"{capture.name}" + (f", {how}" if how else "") + _locale_note(request)
                ),
                "date": capture.date,
                "dump_sha256": capture.dump_sha256,
            },
            # Phasmid docs/decisions.yml: only when the Linux host cannot render the claimed OS.
            "canvas": {"noise": request.family != "linux"},
            "locale": {"languages": list(request.languages), "timezone": request.timezone},
        }
    )
    network: dict[str, Any] = {"webrtc": {"ip_handling_policy": "default"}}
    # The network quality the capture's page saw.
    if "connection" in profile.get("network", {}):
        network["connection"] = profile["network"]["connection"]
    profile["network"] = network
    if request.location is not None:
        profile["locale"]["geolocation"] = request.location.geolocation()
        network["webrtc"]["public_ip"] = request.location.public_ip

    # A claimed media device needs the label the host shows for it: a capture without labels
    # (a page visit has no media permission) claims none, one with labels its first ones.
    devices = profile.setdefault("media_devices", {})
    labels = {}
    for kind in _DEVICE_KINDS:
        known = capture.media_device_labels.get(kind, [])
        devices[kind] = min(devices.get(kind, 0), len(known))
        if devices[kind]:
            labels[kind] = list(known[: devices[kind]])
    devices.pop("labels", None)
    if labels:
        devices["labels"] = labels
    return profile


def _locale_note(request: Request) -> str:
    location = request.location
    if location is None:
        return "; locale from the caller."
    source = (
        f"CLDR territoryInfo through Chromium {locales.chromium_tag()}'s default accept-languages "
        "of the UI locale"
        if list(request.languages) == location.languages
        else "the caller"
    )
    return (
        f"; locale from the proxy exit IP: {location.city}, {location.country} in "
        f"{request.location_note}; zone of the city coordinate (its country's own where the "
        "boundary is another territory's), the position drawn inside the accuracy circle around "
        f"it and in the same boundary; languages from {source}."
    )
