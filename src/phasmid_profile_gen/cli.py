"""phasmid-profile-gen: generate, place and list Phasmid profiles.

    phasmid-profile-gen generate --os windows --exit-ip <proxy exit IP> -o profile.json
    phasmid-profile-gen generate --os linux --seed <32 hex> --timezone Europe/Berlin \\
        --languages de-DE,de,en-US,en
    phasmid-profile-gen locate <proxy exit IP> --seed <32 hex>
    phasmid-profile-gen gpus
    phasmid-profile-gen captures
    phasmid-profile-gen geoip [--archive dbip-city-lite-YYYY-MM.mmdb.gz]
    phasmid-profile-gen validate profile.json

Placing a profile at an exit IP needs the location extra
(pip install 'phasmid-profile-gen[location]') and the DB-IP City Lite database,
downloaded on first use; --geoip-db or $PHASMID_GEOIP_MMDB names another copy.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

from . import __version__, generate_profile, list_captures, list_gpus, locate, new_seed, validate
from .generate import FAMILIES, GenerationError, MissingCaptures, dumps
from .geoip import GeoipError, fetch
from .locales import LocaleDataError
from .location import LocationError

_ERRORS = (GenerationError, LocationError, GeoipError, LocaleDataError, ValueError)


def _summary(profile: dict) -> str:
    d, h = profile["display"], profile["hardware"]
    w = d["window"]
    return (
        f"{profile['os']['family']} {profile['os'].get('version', '?')}, "
        f"{h['concurrency']} threads, "
        f"{h['device_memory']} GB, {d['width']}x{d['height']}@{d['device_pixel_ratio']} "
        f"window {w['outer_width']}x{w['outer_height']}, "
        f"gpu {profile.get('gpu', {}).get('device_id', '-')}, seed {profile['seed']}"
    )


def _write(text: str, out: str | None) -> None:
    if out is None or out == "-":
        sys.stdout.write(text)
        return
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _languages(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    return [lang.strip() for lang in raw.split(",") if lang.strip()]


def _cmd_generate(args: argparse.Namespace) -> int:
    try:
        profile = generate_profile(
            args.os,
            args.seed,
            exit_ip=args.exit_ip,
            timezone=args.timezone,
            languages=_languages(args.languages),
            gpu=args.gpu,
            geoip_db=args.geoip_db,
        )
    except MissingCaptures as e:
        print(e, file=sys.stderr)
        return 1
    _write(dumps(profile), args.out)
    if args.out not in (None, "-"):
        print(f"wrote {args.out}: {_summary(profile)}", file=sys.stderr)
    return 0


def _cmd_locate(args: argparse.Namespace) -> int:
    found = locate(args.ip, args.seed or new_seed(), geoip_db=args.geoip_db)
    print(json.dumps(dataclasses.asdict(found), indent=1, ensure_ascii=False))
    return 0


def _cmd_gpus(_: argparse.Namespace) -> int:
    print(json.dumps(list_gpus(), indent=1))
    return 0


def _cmd_captures(_: argparse.Namespace) -> int:
    print(json.dumps(list_captures(), indent=1))
    return 0


def _cmd_geoip(args: argparse.Namespace) -> int:
    print(fetch(args.archive))
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    errors = validate(json.loads(Path(args.profile).read_text()))
    for err in errors:
        print(err)
    return 1 if errors else 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="phasmid-profile-gen",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    g = sub.add_parser("generate", help="write one profile")
    g.add_argument("--os", required=True, choices=FAMILIES)
    g.add_argument("--seed", help="32 lowercase hex chars; a new one when omitted")
    where = g.add_mutually_exclusive_group(required=True)
    where.add_argument("--timezone", help="IANA zone, e.g. America/New_York")
    where.add_argument(
        "--exit-ip",
        help="the proxy's exit IP: derives the zone, geolocation, WebRTC public address and "
        "languages",
    )
    g.add_argument(
        "--languages",
        help="navigator.languages, comma separated (default en-US,en, or the exit IP country's)",
    )
    g.add_argument(
        "--gpu", help="vendor_id:device_id of a GPU listed by `gpus`, e.g. 0x10de:0x2504"
    )
    g.add_argument("--geoip-db", help="a DB-IP City Lite .mmdb instead of the pinned build")
    g.add_argument("-o", "--out", help="output file (default: stdout)")
    g.set_defaults(run=_cmd_generate)

    loc = sub.add_parser("locate", help="where a profile behind an exit IP is placed")
    loc.add_argument("ip")
    loc.add_argument("--seed", help="the profile's seed; a new one when omitted")
    loc.add_argument("--geoip-db", help="a DB-IP City Lite .mmdb instead of the pinned build")
    loc.set_defaults(run=_cmd_locate)

    sub.add_parser("gpus", help="GPUs of the bundled captures").set_defaults(run=_cmd_gpus)
    sub.add_parser("captures", help="the bundled captures").set_defaults(run=_cmd_captures)

    geo = sub.add_parser("geoip", help="download and verify the pinned DB-IP database")
    geo.add_argument("--archive", type=Path, help="a local copy of the pinned .mmdb.gz")
    geo.set_defaults(run=_cmd_geoip)

    val = sub.add_parser("validate", help="check a profile against the bundled schema")
    val.add_argument("profile")
    val.set_defaults(run=_cmd_validate)
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return args.run(args)
    except _ERRORS as e:
        print(e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
