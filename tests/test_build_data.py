"""scripts/build_data.py's gate for harness dumps: which may back generated profiles."""

from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "build_data", Path(__file__).parents[1] / "scripts" / "build_data.py"
)
assert _SPEC and _SPEC.loader
build_data = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(build_data)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/156.0.0.0 Safari/537.36"
)
RENDERER = "ANGLE (Intel, Intel(R) HD Graphics 4600 (0x00000412) Direct3D11 vs_5_0 ps_5_0, D3D11)"
MAC_UA = UA.replace("Windows NT 10.0; Win64; x64", "Macintosh; Intel Mac OS X 10_15_7")
FULL_VERSION_UA = UA.replace("156.0.0.0", "156.0.8078.73")
SWIFTSHADER = "ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero)), SwiftShader driver)"
HIGH = "contexts.main.navigator.userAgentData.highEntropy"
BRANDS = [
    {"brand": "Not:A-Brand", "version": "8"},
    {"brand": "Chromium", "version": "156"},
    {"brand": "Google Chrome", "version": "156"},
]
DUMP = {
    "dump_version": 1,
    "harness": {"browser": "chrome", "headless": False, "embedded": False},
    "operator": {"os_version": ""},
    "contexts": {
        "main": {
            "navigator": {
                "userAgent": UA,
                "userAgentData": {
                    "brands": BRANDS,
                    "mobile": False,
                    "platform": "Windows",
                    "highEntropy": {
                        "architecture": "x86",
                        "bitness": "64",
                        "platformVersion": "10.0.0",
                    },
                },
            },
            "webgl": {"unmaskedRenderer": RENDERER},
            "webgl2": {"unmaskedRenderer": RENDERER},
            "screen": {
                "width": 1600,
                "height": 900,
                "availWidth": 1600,
                "availHeight": 860,
                "outerWidth": 1600,
                "outerHeight": 860,
                "innerWidth": 1600,
                "innerHeight": 740,
            },  # fmt: skip
        }
    },
}


def _with(path: str, value) -> dict:
    dump = copy.deepcopy(DUMP)
    *parents, leaf = path.split(".")
    node = dump
    for key in parents:
        node = node[key]
    node[leaf] = value
    return dump


def test_a_consistent_stock_chrome_dump_is_kept():
    assert build_data.dump_refusal(DUMP) is None
    assert build_data._dump_os_version("windows", DUMP) == "10"
    win11 = _with(f"{HIGH}.platformVersion", "19.0.0")
    assert build_data._dump_os_version("windows", win11) == "11"


@pytest.mark.parametrize("path, value, reason", [
    ("harness.browser", "chromium", "not Google Chrome"),
    ("harness.browser", "edge", "not Google Chrome"),
    ("harness.headless", True, "headless"),
    ("dump_version", 2, "dump_version"),
    ("contexts.main.navigator.userAgent", MAC_UA, "reduced desktop Chrome UA"),
    ("contexts.main.navigator.userAgent", FULL_VERSION_UA, "reduced desktop Chrome UA"),
    ("contexts.main.navigator.userAgentData.brands", [], "not Google Chrome"),
    ("contexts.main.navigator.userAgentData.platform", "Linux", "contradicts the UA"),
    ("contexts.main.navigator.userAgentData.mobile", True, "contradicts the UA"),
    (f"{HIGH}.architecture", "arm", "not x86/64"),
    ("contexts.main.webgl.unmaskedRenderer", SWIFTSHADER, "no hardware WebGL"),
    ("contexts.main.webgl.unmaskedRenderer", "", "no hardware WebGL"),
    ("contexts.main.webgl2.unmaskedRenderer", "ANGLE (Intel, other, D3D11)", "different renderers"),
    ("contexts.main.screen.innerWidth", 1700, "larger than the window"),
    ("contexts.main.screen.outerWidth", 1700, "larger than the screen"),
])  # fmt: skip
def test_a_dump_that_is_not_stock_chrome_on_real_hardware_is_refused(path, value, reason):
    refusal = build_data.dump_refusal(_with(path, value))
    assert refusal is not None and reason in refusal


def test_a_framed_visit_may_have_a_viewport_wider_than_its_window():
    """In a frame the viewport is the frame's, so only the window-in-screen check applies."""
    dump = _with("harness.embedded", True)
    dump["contexts"]["main"]["screen"]["innerWidth"] = 1700
    assert build_data.dump_refusal(dump) is None


def test_a_linux_dump_needs_the_operators_os_version():
    with pytest.raises(build_data.BuildError, match="cannot read the distribution"):
        build_data._dump_os_version("linux", DUMP)
    assert build_data._dump_os_version("linux", _with("operator.os_version", "24.04")) == "24.04"


def test_an_operator_version_that_contradicts_ua_ch_is_refused():
    with pytest.raises(build_data.BuildError, match="contradicts platformVersion"):
        build_data._dump_os_version("windows", _with("operator.os_version", "11"))


class _Harness:
    """The two functions of Phasmid's consistency capture module that _nearest_memory uses."""

    @staticmethod
    def v8_heap_size_limit(physical_memory_mb: int) -> int:
        mb = 1024 * 1024
        physical = physical_memory_mb * mb
        old_generation = min(max(physical // 2, 256 * mb), 4096 * mb)
        semi_space = 2 * mb
        while semi_space < physical // 4 // 32 and semi_space < 32 * mb:
            semi_space *= 2
        return old_generation + 3 * semi_space

    @staticmethod
    def _rounds_to_device_memory(physical_memory_mb: int, device_memory: int) -> bool:
        lower = 1 << (physical_memory_mb.bit_length() - 1)
        nearest = (
            lower if physical_memory_mb - lower <= 2 * lower - physical_memory_mb else 2 * lower
        )
        return min(max(nearest // 1024, 2), 32) == device_memory


def _memory_dump(limit, device_memory=8) -> dict:
    return {
        "contexts": {
            "main": {
                "storage": {"jsHeapSizeLimit": limit},
                "navigator": {"deviceMemory": device_memory},
            }
        }
    }


def test_a_heap_limit_between_two_whole_mb_rams_takes_the_nearer():
    exact = _Harness.v8_heap_size_limit(8053)
    assert build_data._nearest_memory(_Harness, _memory_dump(exact)) == 8053
    # A machine with a fraction of a MB more: half a MB of heap limit between two whole-MB RAMs.
    assert build_data._nearest_memory(_Harness, _memory_dump(exact - 262144)) in (8052, 8053)
    assert build_data._nearest_memory(_Harness, _memory_dump(exact + 262144)) in (8053, 8054)


@pytest.mark.parametrize(
    "limit, device_memory, message",
    [
        (None, 8, "no jsHeapSizeLimit"),
        (2_000_000_000, 8, "within 1 MiB|rounds to deviceMemory"),  # 4 GB-class limit, 8 reported
        (4_294_180_864, 4, "rounds to deviceMemory"),  # an 8 GB machine reporting 4
    ],
)
def test_a_heap_limit_no_ram_explains_is_refused(limit, device_memory, message):
    with pytest.raises(build_data.BuildError, match=message):
        build_data._nearest_memory(_Harness, _memory_dump(limit, device_memory))
