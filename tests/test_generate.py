"""The generator. No browser, no network."""

from __future__ import annotations

import copy
import os
import random
import re
import subprocess
import sys
from typing import Any

import pytest
from conftest import SEEDS

import phasmid_profile_gen as ppg
from phasmid_profile_gen.captures import catalog, for_os, gpu_key, validate
from phasmid_profile_gen.generate import (
    GenerationError,
    MissingCaptures,
    ProfileGenerator,
    RandomCapture,
    Request,
    dumps,
    from_capture,
    generate,
)

# A UA product token or a four-part Chrome version, e.g. 155.0.8059.84 (a driver version such
# as Intel's 31.0.101.2140 is device data and stays).
VERSION_STRING = re.compile(r"Chrome/|\b1\d{2}\.0\.\d{4}\.\d+\b")
# What the generator sets itself; every other section is the capture's.
GENERATED = {"schema_version", "id", "seed", "captured", "canvas", "locale", "network"}


def _gen(family: str, seed: str, **kw: Any) -> dict:
    return generate(family, seed, ["en-US", "en"], "America/New_York", **kw)


def _capture_of(profile: dict):
    return next(c for c in catalog() if c.dump_sha256 == profile["captured"]["dump_sha256"])


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("family", ["linux", "windows"])
def test_same_seed_gives_identical_bytes(family, seed):
    assert dumps(_gen(family, seed)) == dumps(_gen(family, seed))


def test_identical_bytes_across_processes_and_hash_seeds(tmp_path):
    outputs = []
    for hash_seed in ("1", "2"):
        out = tmp_path / f"{hash_seed}.json"
        argv = ["generate", "--os", "windows", "--seed", SEEDS[0], "--timezone", "America/New_York"]
        subprocess.run(
            [sys.executable, "-m", "phasmid_profile_gen", *argv, "-o", str(out)],
            env={**os.environ, "PYTHONHASHSEED": hash_seed},
            check=True,
            capture_output=True,
        )
        outputs.append(out.read_bytes())
    assert (
        outputs[0]
        == outputs[1]
        == dumps(ppg.generate_profile("windows", SEEDS[0], timezone="America/New_York")).encode()
    )


def test_different_seeds_give_different_profiles():
    assert len({dumps(_gen("linux", seed)) for seed in SEEDS}) == len(SEEDS)


def test_global_random_is_never_used():
    state = random.getstate()
    _gen("linux", SEEDS[0])
    _gen("windows", SEEDS[0])
    assert random.getstate() == state


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("family", ["linux", "windows"])
def test_no_version_string_in_output(family, seed):
    assert not VERSION_STRING.search(dumps(_gen(family, seed)))


@pytest.mark.parametrize("family", ["linux", "windows"])
def test_output_is_schema_valid(family):
    for i in range(32):
        assert validate(_gen(family, f"{i:032x}")) == []


@pytest.mark.parametrize("capture", catalog(), ids=lambda c: c.name)
def test_every_capture_backs_a_valid_profile(capture):
    request = Request(capture.family, SEEDS[0], ("en-US", "en"), "UTC")
    assert validate(from_capture(capture, request)) == []


def test_a_returned_profile_is_the_callers_to_change():
    """Profiles share no object with the catalog: editing one leaves the next one alone."""
    before = dumps(_gen("windows", SEEDS[0]))
    p = _gen("windows", SEEDS[0])
    for section in ("gpu", "fonts", "audio", "speech", "capabilities", "display", "hardware"):
        p[section].clear()
    assert dumps(_gen("windows", SEEDS[0])) == before


@pytest.mark.parametrize("family", ["linux", "windows"])
def test_every_device_field_is_the_captures(family):
    """Nothing is sampled or derived: outside the generator's own sections and the media device
    counts, a profile is its capture verbatim."""
    for i in range(24):
        p = _gen(family, f"{i:032x}")
        capture = _capture_of(p).profile
        assert p["os"]["family"] == family
        for section in capture.keys() - GENERATED - {"media_devices"}:
            assert p[section] == capture[section], section
        assert p["network"].get("connection") == capture.get("network", {}).get("connection")


def test_random_selection_covers_every_capture_of_the_os():
    for family in ("linux", "windows"):
        seen = {_capture_of(_gen(family, f"{i:032x}")).name for i in range(96)}
        assert seen == {c.name for c in for_os(family)}


def test_canvas_noise_only_off_linux():
    assert _gen("linux", SEEDS[0])["canvas"] == {"noise": False}
    assert _gen("windows", SEEDS[0])["canvas"] == {"noise": True}


def test_a_capture_without_labels_claims_no_media_device():
    """No label, no device claimed; no measured inner window size, none invented."""
    bare = [c for c in catalog() if not c.media_device_labels]
    assert bare
    for capture in bare:
        p = from_capture(capture, Request(capture.family, SEEDS[0], ("en-US",), "UTC"))
        assert p["media_devices"] == {"audioinput": 0, "audiooutput": 0, "videoinput": 0}
        if "inner_width" not in capture.profile["display"]["window"]:
            assert "inner_width" not in p["display"]["window"]


def test_a_capture_with_labels_claims_a_labelled_device_each():
    labelled = [c for c in catalog() if c.media_device_labels]
    assert labelled
    for capture in labelled:
        p = from_capture(capture, Request(capture.family, SEEDS[0], ("en-US",), "UTC"))
        devices = p["media_devices"]
        for kind, labels in devices.get("labels", {}).items():
            assert len(labels) == devices[kind] > 0


def test_the_gpu_option_selects_that_captures_gpu():
    for entry in ppg.list_gpus():
        p = ppg.generate_profile(entry["os"], SEEDS[1], timezone="UTC", gpu=entry["gpu"])
        assert gpu_key(p["gpu"]) == entry["gpu"]
    with pytest.raises(MissingCaptures, match="0x10de:0x2482"):
        _gen("linux", SEEDS[0], gpu="0x10de:0x2482")  # no capture has it


def test_missing_captures_fail_loudly():
    with pytest.raises(MissingCaptures) as e:
        _gen("windows", SEEDS[0], generator=RandomCapture(for_os("linux")))
    assert e.value.family == "windows"


def test_random_capture_is_a_profile_generator():
    assert isinstance(RandomCapture(), ProfileGenerator)


def test_another_generator_can_be_swapped_in():
    class First:
        """Always the first capture of the OS."""

        def __init__(self):
            self.requests: list[Request] = []

        def generate(self, request: Request) -> dict:
            self.requests.append(request)
            return from_capture(for_os(request.family)[0], request, how="the first")

    first = First()
    p = ppg.generate_profile("windows", SEEDS[2], timezone="UTC", generator=first)
    assert _capture_of(p) == for_os("windows")[0]
    assert "the first" in p["captured"]["note"]
    assert first.requests == [Request("windows", SEEDS[2], ("en-US", "en"), "UTC")]


def test_a_generated_profile_that_does_not_validate_is_refused():
    class Broken:
        def generate(self, request: Request) -> dict:
            p = from_capture(for_os(request.family)[0], request)
            p["hardware"] = copy.deepcopy(p["hardware"]) | {"concurrency": 10_000}
            return p

    with pytest.raises(GenerationError, match="does not validate"):
        ppg.generate_profile("windows", SEEDS[0], timezone="UTC", generator=Broken())


def test_from_capture_refuses_another_os():
    capture = for_os("linux")[0]
    with pytest.raises(GenerationError, match="not windows"):
        from_capture(capture, Request("windows", SEEDS[0], ("en-US",), "UTC"))


def test_macos_is_not_a_family():
    with pytest.raises(GenerationError, match="os must be one of linux, windows"):
        _gen("macos", SEEDS[0])


@pytest.mark.parametrize("seed", ["", "0A1B2C3D4E5F60718293A4B5C6D7E8F9", "0a1b", "g" * 32])
def test_a_malformed_seed_is_refused(seed):
    with pytest.raises(GenerationError, match="32 lowercase hex"):
        _gen("linux", seed)


def test_generate_profile_takes_exactly_one_of_timezone_and_exit_ip():
    with pytest.raises(GenerationError, match="exactly one"):
        ppg.generate_profile("linux", SEEDS[0])
    with pytest.raises(GenerationError, match="exactly one"):
        ppg.generate_profile("linux", SEEDS[0], timezone="UTC", exit_ip="14.161.0.1")


def test_generate_profile_draws_a_seed_when_none_is_given():
    p = ppg.generate_profile("windows", timezone="Europe/Berlin", languages=["de-DE", "de"])
    assert re.fullmatch(r"[0-9a-f]{32}", p["seed"]) and p["locale"]["languages"] == ["de-DE", "de"]
    assert dumps(
        ppg.generate_profile(
            "windows", p["seed"], timezone="Europe/Berlin", languages=["de-DE", "de"]
        )
    ) == dumps(p)
