"""The bundled data: integrity, and agreement with the sources it was extracted from."""

from __future__ import annotations

import hashlib
import json
import re

import pytest

from phasmid_profile_gen import _data
from phasmid_profile_gen.captures import catalog, validate

_SOFTWARE = ("SwiftShader", "llvmpipe", "softpipe", "Microsoft Basic Render Driver")


def test_the_manifest_names_every_bundled_file_by_sha256():
    files = _data.manifest()["files"]
    assert set(files) == {"captures.json.xz", "locale.json.xz", "schema.json.xz"}
    for name, digest in files.items():
        assert hashlib.sha256(_data.path(name).read_bytes()).hexdigest() == digest, name


def test_the_manifest_lists_the_catalog():
    listed = [(c["name"], c["dump_sha256"]) for c in _data.manifest()["captures"]]
    assert listed == [(c.name, c.dump_sha256) for c in catalog()]


def test_both_families_have_captures():
    assert {c.family for c in catalog()} == {"linux", "windows"}


@pytest.mark.parametrize("capture", catalog(), ids=lambda c: c.name)
def test_every_capture_is_a_hardware_desktop_chrome(capture):
    p = capture.profile
    assert p["os"]["family"] == capture.family and p["os"]["version"]
    assert capture.gpu == f"{p['gpu']['vendor_id']}:{p['gpu']['device_id']}"
    assert not any(s in p["gpu"]["webgl"]["unmasked_renderer"] for s in _SOFTWARE)
    assert re.fullmatch(r"[0-9a-f]{64}", capture.dump_sha256)
    assert len(p["fonts"]["families"]) >= 10
    # Only device data is bundled: the capture host's own locale and seed are not.
    assert not {"locale", "seed", "id"} & p.keys()


@pytest.mark.parametrize("capture", catalog(), ids=lambda c: c.name)
def test_every_capture_completes_a_valid_profile(capture):
    """The capture's sections, with what generation adds, satisfy the bundled schema."""
    profile = {
        **json.loads(json.dumps(capture.profile)),
        "schema_version": 1,
        "id": "capture-check",
        "seed": "0" * 32,
        "locale": {"languages": ["en-US", "en"], "timezone": "UTC"},
        "network": {
            "webrtc": {"ip_handling_policy": "default"},
            **capture.profile.get("network", {}),
        },
    }
    assert validate(profile) == []


def _v8_heap_size_limit(physical_memory_mb: int) -> int:
    """Phasmid's V8HeapSizeLimit (core/profile.patch), which refuses a profile whose
    hardware.js_heap_size_limit is any other value for its physical_memory_mb."""
    mb = 1024 * 1024
    physical = physical_memory_mb * mb
    old_generation = min(max(physical // 2, 256 * mb), 4096 * mb)
    semi_space = 2 * mb
    while semi_space < physical // 4 // 32 and semi_space < 32 * mb:
        semi_space *= 2
    return old_generation + 3 * semi_space


@pytest.mark.parametrize("capture", catalog(), ids=lambda c: c.name)
def test_every_captures_heap_limit_is_the_one_its_ram_gives(capture):
    hardware = capture.profile["hardware"]
    assert hardware["js_heap_size_limit"] == _v8_heap_size_limit(hardware["physical_memory_mb"])


def test_the_manifest_says_which_captures_assume_their_memory():
    assumed = {c["name"]: c["assumed"] for c in _data.manifest()["captures"] if "assumed" in c}
    assert assumed
    assert all(a == ["physical_memory_mb", "js_heap_size_limit"] for a in assumed.values())
