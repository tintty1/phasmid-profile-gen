# phasmid-profile-gen

[![Test](https://github.com/tintty1/phasmid-profile-gen/actions/workflows/test.yml/badge.svg)](https://github.com/tintty1/phasmid-profile-gen/actions/workflows/test.yml)
[![License: BSD-3-Clause](https://img.shields.io/badge/License-BSD_3--Clause-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)

Generates device profiles for [Phasmid](https://github.com/tintty1/phasmid), the
Chromium fork that reports the device described by one JSON profile instead of
the machine it runs on.

Every device field of a profile comes from one capture of a real device: no
population model is sampled and no GPU identity is derived. The captures, the
schema and the locale tables are bundled in compressed form, so it runs without
a Phasmid checkout.

## Install

```bash
pip install phasmid-profile-gen                # profiles with a given time zone
pip install 'phasmid-profile-gen[location]'    # also place profiles at a proxy exit IP
```

Python 3.12 or newer. The `location` extra adds `timezonefinder` (with its
boundary data, about 33 MB) and `maxminddb`, pinned exactly because they decide
which zone and position a seed gets.

## Use

```python
from phasmid_profile_gen import dumps, generate_profile

# Placed where the proxy comes out: zone, geolocation, WebRTC address, languages.
profile = generate_profile("windows", exit_ip=proxy_exit_ip)

# Or with an explicit locale, reproducibly.
profile = generate_profile(
    "linux",
    seed="0a1b2c3d4e5f60718293a4b5c6d7e8f9",
    timezone="Europe/Berlin",
    languages=["de-DE", "de", "en-US", "en"],
)
open("profile.json", "w").write(dumps(profile))
```

```bash
phasmid-profile-gen generate --os windows --exit-ip <proxy exit IP> -o profile.json
phasmid-profile-gen generate --os linux --seed 0a1b2c3d4e5f60718293a4b5c6d7e8f9 \
    --timezone Europe/Berlin --languages de-DE,de,en-US,en -o profile.json
phasmid-profile-gen gpus          # GPUs the bundled captures back (for --gpu)
phasmid-profile-gen captures      # the bundled captures
phasmid-profile-gen locate <proxy exit IP> --seed <seed>
phasmid-profile-gen validate profile.json
```

Give exactly one of an exit IP and a time zone. **Generate Windows profiles
with an exit IP.** Windows Chrome answers geolocation from the system location
provider, so a Windows profile with no position is a stronger tell than a Linux
one. The seed is 32 lowercase hex characters. When omitted, a new one is drawn;
read it back from `profile["seed"]`. The same seed and inputs always give the
same bytes (`dumps`).

### Choosing the capture

Which capture backs a profile is a `ProfileGenerator`'s choice: any object
with `generate(request: Request) -> dict`. The default, `RandomCapture()`,
draws one of the OS's captures (those with the requested GPU, when `gpu=` is
given) uniformly from the seed. Pass another strategy as `generator=`;
`from_capture(capture, request)` builds the profile once a capture is chosen:

```python
from phasmid_profile_gen import Request, catalog, from_capture, generate_profile

class Newest:
    def generate(self, request: Request) -> dict:
        captures = [c for c in catalog() if c.family == request.family]
        newest = max(captures, key=lambda c: c.date)
        return from_capture(newest, request, how="the newest capture")

profile = generate_profile("windows", timezone="UTC", generator=Newest())
```

A generator draws only from `request.seed` (`_rng.decision_rng(seed,
decision)`), so a seed keeps giving the same profile. `generate_profile`
validates whatever it returns.

Other functions: `locate(ip, seed)`, `list_gpus()`, `list_captures()`,
`validate(profile)` (against the bundled Phasmid schema) and `new_seed()`.
Errors are `GenerationError`, `LocationError` and `geoip.GeoipError`.

## What a profile is made of

| Part | From |
| --- | --- |
| OS identity and version, CPU threads, memory, touch points, screen and window geometry, GPU with its WebGL and WebGPU surface, fonts, voices, audio, codecs, system colours, pointer, preferences, RAM, disk, media devices, battery, connection | One capture of a real device, taken whole. |
| Time zone, geolocation, WebRTC public IP, languages | The exit IP: DB-IP City Lite, timezonefinder, CLDR, and Chrome's default accept-languages for the country's UI locale. Otherwise the time zone and languages you pass. |

No user-agent or version string is written. Phasmid builds those from its own
version. Nothing is invented: a field only a capture can supply is never
filled in by guessing, and an OS without a capture fails with
`MissingCaptures`. `captured.note` records where every part came from.

### Bundled captures

Run `phasmid-profile-gen captures` for the current list. It is also in
[`data/manifest.json`](src/phasmid_profile_gen/data/manifest.json), which lists
the dumps refused and why.

Each is named by its OS family and PCI ids (`windows-0x8086-0x0412`). Where a
capture has no media device labels (a page visit grants no media permission),
profiles built from it claim no microphone, speaker or camera.

A profile's OS version is its capture's, so a Windows 11 capture gives
Windows 11 profiles with Windows 11 fonts and colours.

## The location database

The exit IP is looked up in DB-IP IP to City Lite (CC BY 4.0, "IP Geolocation
by DB-IP"). It is about 127 MB, so it is not bundled. The build pinned in
`data/dbip-city-lite.json` is downloaded on first use, checked against the
pin, and kept in `$XDG_CACHE_HOME/phasmid/geoip/`. That is the cache Phasmid's
tools use, so an existing download is reused.

```bash
phasmid-profile-gen geoip                     # download and verify now
phasmid-profile-gen geoip --archive dbip-city-lite-2026-10.mmdb.gz
```

DB-IP serves only the last two monthly builds. Once the pinned one is gone,
either install a saved copy with `--archive`, or use any other DB-IP City Lite
build with `PHASMID_GEOIP_MMDB=/path/to/dbip-city-lite.mmdb`, `--geoip-db` or
`geoip_db=`. Another build is not checked against the pin; the profile's note
names its build date and sha256 instead.

## Rebuilding the data

`src/phasmid_profile_gen/data/` is generated, compressed and committed. It is
rebuilt from a Phasmid checkout and further harness dumps by
[`scripts/build_data.py`](scripts/build_data.py):

```bash
uv run python scripts/build_data.py \
    --phasmid ../phasmid \
    --dumps /path/to/dumps
```

The build:

- maps every dump with the checkout's own `tests/consistency/capture.py` (the
  code `consistency capture` runs), and checks Phasmid's captures against their
  records as `tools/genprofile` does;
- keeps a dump only if it is stock desktop Google Chrome on Windows
  or Linux, with a hardware GPU, a consistent UA and UA-CH, and a window inside
  its screen, and only if it maps to a valid profile. Each refusal is listed
  with its reason in `manifest.json`;
- takes the nearest whole-MB RAM when a dump's heap limit sits within 1 MiB of
  one but matches none exactly (a fraction of a MB of RAM), sets the heap limit
  to the one the browser computes for it, and lists both as `assumed` in
  `manifest.json`; a limit further off is refused;
- stores only what generation reads, under a neutral name (OS family and PCI
  ids). Raw dumps, file names, notes and the device's locale are never written,
  and a refused dump is listed by hash only.

The output is deterministic, so a rebuild from the same sources gives the same
bytes. A Linux dump also needs the operator's OS version (`operator.os_version`),
which a page cannot read.

## Develop

```bash
uv sync --all-extras
uv run pytest
uv run pre-commit run --all-files
PHASMID_GEOIP_NETWORK=1 uv run pytest -m network   # reads the real DB-IP build
```

Releases are published to PyPI by `.github/workflows/publish-pypi.yaml` when a
GitHub release is published. The version comes from the release tag
(`uv-dynamic-versioning`, e.g. `v0.1.0`). Publishing uses PyPI trusted
publishing: register the repository and that workflow as a trusted publisher
of `phasmid-profile-gen` on PyPI once.

## Licence

The code is BSD 3-Clause, as Phasmid is ([`LICENSE`](LICENSE)). The bundled
data keeps its sources' licences:

- Unicode CLDR: Unicode License v3.
- Chromium's locale table: BSD-style.

DB-IP (CC BY 4.0) and timezone-boundary-builder (ODbL 1.0) are used at runtime
but not bundled. See
[`data/LICENSES.md`](src/phasmid_profile_gen/data/LICENSES.md).
