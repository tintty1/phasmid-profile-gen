"""Rebuild src/phasmid_profile_gen/data from its sources.

    uv run python scripts/build_data.py \\
        --phasmid ../phasmid \\
        --dumps /path/to/dumps

Sources:

- A Phasmid checkout: ``profiles/data/captures`` with their records in
  ``profiles/examples`` (checked as tools/genprofile checks them),
  ``profiles/data/cldr``,
  ``profiles/data/chromium/accept-languages.json`` and ``profiles/schema.json``.
  Each dump is mapped onto the schema by the checkout's own
  ``tests/consistency/capture.py``, the code ``consistency capture`` runs.
- Further harness dumps of real devices (``--dumps``, repeatable), mapped the
  same way. Only stock desktop Google Chrome on Windows or Linux with a
  hardware GPU is kept, and only what generation reads is stored: the raw dumps
  are never written here.

A capture is stored under a neutral id (OS family and PCI ids), with no source,
no note and no file name, so nothing says whose device it was or where its dump
came from. ``data/manifest.json`` names each dump only by sha256.

Every output is JSON with sorted keys, xz-compressed, so a rebuild from the
same sources gives the same bytes. ``data/manifest.json`` lists the sources,
each capture and the sha256 of every file.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.util
import json
import lzma
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "src" / "phasmid_profile_gen" / "data"

# The reduced desktop Chrome UA on the two families Phasmid claims.
_CHROME_UA = re.compile(
    r"Mozilla/5\.0 \(([^)]*)\) AppleWebKit/537\.36 \(KHTML, like Gecko\) "
    r"Chrome/(\d+)\.0\.0\.0 Safari/537\.36"
)
_UA_TOKENS = {"X11; Linux x86_64": "linux", "Windows NT 10.0; Win64; x64": "windows"}

_GPU_ID_KEYS = (
    "vendor_id",
    "device_id",
    "vendor_name",
    "device_name",
    "driver_vendor",
    "driver_version",
    "gl_vendor",
    "gl_renderer",
    "gl_version",
)
# Sections a record must agree with its dump on (tools/genprofile/genprofile/captures.py).
_CHECKED_SECTIONS = (
    "gpu",
    "audio",
    "fonts",
    "speech",
    "media",
    "webauthn",
    "capabilities",
    "system_colors",
)
_SOFTWARE = ("SwiftShader", "llvmpipe", "softpipe", "Microsoft Basic Render Driver")


class BuildError(Exception):
    pass


def _json(obj: Any) -> bytes:
    return (json.dumps(obj, indent=None, sort_keys=True, ensure_ascii=False) + "\n").encode()


def _write_xz(name: str, obj: Any) -> Path:
    path = DATA / f"{name}.json.xz"
    path.write_bytes(
        lzma.compress(_json(obj), format=lzma.FORMAT_XZ, check=lzma.CHECK_CRC64, preset=9)
    )
    return path


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def dump_sha256(dump: dict[str, Any]) -> str:
    """How consistency capture names a dump (captured.dump_sha256)."""
    return _sha256(json.dumps(dump, sort_keys=True).encode())


# --- Captures ---------------------------------------------------------------------------------


def harness(phasmid: Path) -> ModuleType:
    """tests/consistency/capture.py of the checkout, loaded as part of its package."""
    package = phasmid / "tests" / "consistency"
    spec = importlib.util.spec_from_file_location(
        "phasmid_harness", package / "__init__.py", submodule_search_locations=[str(package)]
    )
    if spec is None or spec.loader is None:
        raise BuildError(f"cannot load {package}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return importlib.import_module(f"{spec.name}.capture")


def _stored(profile: dict[str, Any]) -> dict[str, Any]:
    """The part of a mapped capture that generation reads. The capture host's locale, seed and
    WebRTC policy are not device data and are dropped."""
    out = {
        k: v for k, v in profile.items() if k not in ("id", "seed", "captured", "locale", "network")
    }
    if "connection" in profile.get("network", {}):
        out["network"] = {"connection": profile["network"]["connection"]}
    return out


def _entry(
    *,
    name: str,
    profile: dict[str, Any],
    dump: dict[str, Any],
    digest: str,
    labels: dict[str, list[str]],
    assumed: list[str] | None = None,
) -> dict[str, Any]:
    gpu = profile["gpu"]
    return {
        "name": name,
        "family": profile["os"]["family"],
        "gpu": f"{gpu['vendor_id']}:{gpu['device_id']}",
        "date": dump["harness"]["started"][:10],
        "browser_version": dump["harness"].get("binary_version", ""),
        "dump_sha256": digest,
        "battery": dump["contexts"]["main"]["navigator"].get("battery"),
        "media_device_labels": labels,
        "profile": _stored(profile),
    } | ({"assumed": assumed} if assumed else {})


def _neutral_names(captures: list[dict[str, Any]]) -> None:
    """Rename each capture to its OS family and PCI ids (windows-0x8086-0x0412), numbered when two
    devices share them. The file a dump came from, and the machine it names, are not kept."""
    taken: dict[str, int] = {}
    for c in captures:
        base = f"{c['family']}-{c['gpu'].replace(':', '-')}"
        taken[base] = taken.get(base, 0) + 1
        c["name"] = base if taken[base] == 1 else f"{base}-{taken[base]}"


def phasmid_captures(phasmid: Path, h: ModuleType) -> list[dict[str, Any]]:
    """The checkout's captures, each checked against its record as tools/genprofile does."""
    captures_dir = phasmid / "profiles" / "data" / "captures"
    records_dir = phasmid / "profiles" / "examples"
    family_names_dir = phasmid / "profiles" / "data" / "fonts" / "metrics"
    out = []
    for path in sorted(captures_dir.glob("*.json")):
        dump = json.loads(path.read_text())
        if "harness" not in dump:  # a supplementary capture (permission-granted media devices)
            continue
        stem = path.name.split(".", 1)[0]
        record_path = records_dir / f"{stem}.json"
        if not record_path.exists():
            raise BuildError(f"{path.name}: no capture record {record_path}")
        record = json.loads(record_path.read_text())
        digest = dump_sha256(dump)
        if record.get("captured", {}).get("dump_sha256") != digest:
            raise BuildError(f"{record_path}: captured.dump_sha256 does not name {path.name}")
        hardware = record["hardware"]
        profile = h.capture(
            dump,
            profile_id=stem,
            os_version=record["os"]["version"],
            gpu_ids={k: record["gpu"][k] for k in _GPU_ID_KEYS if record["gpu"].get(k)},
            seed=record["seed"],
            physical_memory_mb=hardware.get("physical_memory_mb"),
            disk_total_bytes=hardware.get("disk_total_bytes"),
        )
        # A record may narrow fonts.families to part of what the host resolved, never add to it.
        families = record.get("fonts", {}).get("families", [])
        resolved = set(profile["fonts"]["families"])
        for measured in sorted(family_names_dir.glob(f"{stem}.*-family-names.json")):
            resolved |= set(json.loads(measured.read_text())["resolved"])
        if not set(families) <= resolved:
            raise BuildError(
                f"{record_path}: fonts.families lists families {path.name} does not resolve"
            )
        profile["fonts"]["families"] = families
        for section in _CHECKED_SECTIONS:
            if profile.get(section) != record.get(section):
                raise BuildError(f"{record_path}: {section} differs from what {path.name} maps to")
        out.append(
            _entry(
                name=path.name,
                profile=profile,
                dump=dump,
                digest=digest,
                labels=record["media_devices"].get("labels", {}),
            )
        )
    return out


def _dump_os_version(family: str, dump: dict[str, Any]) -> str:
    """The marketing version: the operator's when given, else what UA-CH platformVersion
    determines (Windows 13.0.0 and up is 11). A page cannot read a Linux distribution."""
    stated = (dump.get("operator") or {}).get("os_version") or ""
    high = dump["contexts"]["main"]["navigator"]["userAgentData"]["highEntropy"]
    if family == "windows":
        derived = "11" if int(high["platformVersion"].split(".")[0]) >= 13 else "10"
        if stated and stated != derived:
            raise BuildError(
                f"operator os_version {stated} contradicts platformVersion "
                f"{high['platformVersion']}"
            )
        return derived
    if not stated:
        raise BuildError(
            "a Linux dump without the operator's os_version (a page cannot read the distribution)"
        )
    return stated


def dump_refusal(dump: dict[str, Any]) -> str | None:
    """Why a dump cannot back generated profiles; None when it can."""
    harness_meta = dump.get("harness") or {}
    if dump.get("dump_version") != 1:
        return f"dump_version {dump.get('dump_version')}"
    if harness_meta.get("browser") != "chrome":
        return f"browser {harness_meta.get('browser')}, not Google Chrome"
    if harness_meta.get("headless"):
        return "headless"
    main = dump["contexts"]["main"]
    nav = main["navigator"]
    m = _CHROME_UA.fullmatch(nav.get("userAgent", ""))
    if not m or m.group(1) not in _UA_TOKENS:
        return f"not the reduced desktop Chrome UA on Windows or Linux: {nav.get('userAgent')!r}"
    family = _UA_TOKENS[m.group(1)]
    uad = nav.get("userAgentData") or {}
    high = uad.get("highEntropy") or {}
    brands = {b.get("brand") for b in uad.get("brands") or []}
    if not {"Chromium", "Google Chrome"} <= brands:
        return f"UA-CH brands {sorted(filter(None, brands))}, not Google Chrome"
    expected_platform = {"linux": "Linux", "windows": "Windows"}[family]
    if uad.get("platform") != expected_platform or uad.get("mobile") is not False:
        return (
            f"UA-CH platform {uad.get('platform')!r} or mobile {uad.get('mobile')!r} "
            "contradicts the UA"
        )
    if (high.get("architecture"), high.get("bitness")) != ("x86", "64"):
        return (
            f"UA-CH architecture {high.get('architecture')!r}/{high.get('bitness')!r}, not x86/64"
        )
    renderer = main.get("webgl", {}).get("unmaskedRenderer") or ""
    if not renderer or any(s in renderer for s in _SOFTWARE):
        return f"no hardware WebGL renderer ({renderer!r})"
    if main.get("webgl2", {}).get("unmaskedRenderer") != renderer:
        return "WebGL and WebGL2 name different renderers"
    s = main["screen"]
    if not harness_meta.get("embedded") and (
        s["innerWidth"] > s["outerWidth"] or s["innerHeight"] > s["outerHeight"]
    ):
        return (
            f"viewport {s['innerWidth']}x{s['innerHeight']} larger than the window "
            f"{s['outerWidth']}x{s['outerHeight']}"
        )
    if (
        s["outerWidth"] > s["width"]
        or s["availWidth"] > s["width"]
        or s["availHeight"] > s["height"]
    ):
        return "window or work area larger than the screen"
    return None


def _nearest_memory(h: ModuleType, dump: dict[str, Any]) -> int:
    """RAM, in whole MB, for a dump whose heap limit no whole-MB RAM gives exactly.

    Below 8 GB V8's heap limit follows RAM at half a megabyte per megabyte, so a machine whose
    RAM is a fraction of a MB reports a limit between two whole-MB values. The nearest whole MB
    is taken when its limit is within 1 MiB of the reported one and it rounds to the reported
    deviceMemory; anything further off is refused rather than guessed."""
    main = dump["contexts"]["main"]
    limit = main.get("storage", {}).get("jsHeapSizeLimit")
    if not isinstance(limit, int):
        raise BuildError("no jsHeapSizeLimit to place physical memory from")
    best = min(range(1024, 8192), key=lambda mb: abs(h.v8_heap_size_limit(mb) - limit))
    off = abs(h.v8_heap_size_limit(best) - limit)
    if off > 1024 * 1024 or not h._rounds_to_device_memory(best, main["navigator"]["deviceMemory"]):
        raise BuildError(f"no RAM within 1 MiB of jsHeapSizeLimit {limit} rounds to deviceMemory")
    return best


def _map_dump(
    path: Path, dump: dict[str, Any], digest: str, h: ModuleType
) -> tuple[dict[str, Any], list[str]]:
    """The mapped profile of a dump and what was assumed to map it; BuildError says why it
    cannot back profiles."""
    reason = dump_refusal(dump)
    if reason is not None:
        raise BuildError(reason)
    m = _CHROME_UA.fullmatch(dump["contexts"]["main"]["navigator"]["userAgent"])
    assert m is not None  # dump_refusal checked it
    family = _UA_TOKENS[m.group(1)]
    operator = dump.get("operator") or {}
    main = dump["contexts"]["main"]
    memory, assumed = operator.get("physical_memory_mb"), []
    if not memory:
        try:
            h.physical_memory_from_dump(
                main["navigator"]["deviceMemory"], main.get("storage", {}).get("jsHeapSizeLimit")
            )
        except ValueError:
            memory = _nearest_memory(h, dump)
            assumed = ["physical_memory_mb", "js_heap_size_limit"]
    try:
        profile = h.capture(
            dump,
            profile_id=path.stem,
            os_version=_dump_os_version(family, dump),
            gpu_ids={},
            # Deterministic: the disk the quota headroom leaves open is drawn from this.
            seed=digest[:32],
            physical_memory_mb=memory,
            disk_total_bytes=operator.get("disk_total_bytes"),
        )
    except (ValueError, KeyError) as e:
        raise BuildError(f"cannot be mapped: {e}") from e
    errors = h.validate(profile)
    if errors:
        raise BuildError("maps to an invalid profile: " + "; ".join(errors[:3]))
    if not {"vendor_id", "device_id"} <= profile["gpu"].keys():
        raise BuildError("the WebGL renderer names no PCI ids, and there is no chrome://gpu report")
    if assumed:
        # The browser refuses a profile whose heap limit is not the one its RAM gives.
        profile["hardware"]["js_heap_size_limit"] = h.v8_heap_size_limit(memory)
    return profile, assumed


def dump_captures(
    dirs: list[Path], h: ModuleType
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    kept, refused = [], []
    seen: set[str] = set()
    for path in sorted(p for d in dirs for p in d.glob("*.json")):
        dump = json.loads(path.read_text())
        digest = dump_sha256(dump)
        if digest in seen:
            continue
        seen.add(digest)
        try:
            profile, assumed = _map_dump(path, dump, digest, h)
        except BuildError as e:
            refused.append({"name": path.name, "dump_sha256": digest, "reason": str(e)})
            continue
        if dump["harness"].get("embedded"):
            # In a frame, innerWidth/innerHeight are the frame's: the browser chrome around the
            # top viewport is unmeasured, so generated profiles leave the inner size out.
            for key in ("inner_width", "inner_height"):
                profile["display"]["window"].pop(key, None)
        kept.append(
            _entry(
                name=path.name,
                profile=profile,
                dump=dump,
                digest=digest,
                # A page visit never asks for media permission, so there are no labels; a
                # generated profile claims no device it has no label for.
                labels={},
                assumed=assumed,
            )
        )
    return kept, refused


# --- Main -------------------------------------------------------------------------------------


def _git_head(path: Path) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--phasmid", type=Path, required=True, help="a Phasmid checkout")
    ap.add_argument(
        "--dumps", type=Path, action="append", default=[], help="a directory of harness dumps"
    )
    args = ap.parse_args(argv)
    phasmid = args.phasmid.resolve()
    h = harness(phasmid)
    DATA.mkdir(parents=True, exist_ok=True)

    captures = phasmid_captures(phasmid, h)
    collected, refused = dump_captures([d.resolve() for d in args.dumps], h)
    captures += collected
    _neutral_names(captures)
    cldr = json.loads((phasmid / "profiles" / "data" / "cldr" / "cldr.json").read_text())
    chromium = json.loads(
        (phasmid / "profiles" / "data" / "chromium" / "accept-languages.json").read_text()
    )
    tag = (phasmid / "chromium_version.txt").read_text().strip()
    if chromium["source"]["chromium"] != tag:
        raise BuildError(
            f"accept-languages.json is from Chromium {chromium['source']['chromium']}, "
            f"the checkout pins {tag}"
        )
    schema = json.loads((phasmid / "profiles" / "schema.json").read_text())

    written = {
        "captures": _write_xz("captures", {"captures": captures}),
        "locale": _write_xz("locale", {"cldr": cldr, "chromium": chromium}),
        "schema": _write_xz("schema", schema),
    }
    (DATA / "dbip-city-lite.json").write_text(
        (phasmid / "tools" / "genprofile" / "dbip-city-lite.json").read_text()
    )
    (DATA / "CLDR-LICENSE").write_bytes(
        (phasmid / "profiles" / "data" / "cldr" / "LICENSE").read_bytes()
    )

    manifest = {
        "sources": {
            "phasmid": {"commit": _git_head(phasmid), "chromium": tag},
            "cldr": cldr["source"]["tag"],
        },
        "captures": [
            {
                k: c[k]
                for k in (
                    "name",
                    "family",
                    "gpu",
                    "date",
                    "browser_version",
                    "dump_sha256",
                )
            }
            | {
                "os_version": c["profile"]["os"]["version"],
                "renderer": c["profile"]["gpu"]["webgl"]["unmasked_renderer"],
            }
            | ({"assumed": c["assumed"]} if c.get("assumed") else {})
            for c in captures
        ],
        # By hash only: the dump's file name says where it came from.
        "refused": [{k: r[k] for k in ("dump_sha256", "reason")} for r in refused],
        "files": {p.name: _sha256(p.read_bytes()) for p in written.values()},
    }
    (DATA / "manifest.json").write_text(
        json.dumps(manifest, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
    )

    for p in written.values():
        print(f"{p.relative_to(ROOT)}: {p.stat().st_size} bytes")
    print(f"{len(captures)} captures:")
    for c in captures:
        print(f"  {c['family']:7} {c['gpu']}  {c['name']}")
    for r in refused:
        print(f"  refused   {r['name']}: {r['reason']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
