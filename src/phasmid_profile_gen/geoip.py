"""The DB-IP IP to City Lite database (CC BY 4.0, https://db-ip.com).

The database is about 127 MB, so it is not bundled. ``fetch`` downloads the
build pinned in ``data/dbip-city-lite.json`` with TLS verification on, checks
the archive and the decompressed file against the pin, and installs it under
``$XDG_CACHE_HOME/phasmid/geoip/<archive sha256>/`` (the cache Phasmid's
tools/genprofile uses). Any mismatch is refused.

DB-IP serves only the current and the previous month's build. Once the pinned
one is gone, install a saved copy of it with ``--archive``, or use another
build of the same database: pass ``mmdb=`` to ``reader`` or set
``PHASMID_GEOIP_MMDB``. That file is not checked against the pin, and the
profile's ``captured.note`` names its build and sha256 instead.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import ssl
import tempfile
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import Any

from . import _data

ENV_MMDB = "PHASMID_GEOIP_MMDB"
DATABASE_TYPE = "DBIP-City-Lite"
_MMDB = "dbip-city-lite.mmdb"


class GeoipError(Exception):
    pass


@dataclass(frozen=True)
class Pin:
    name: str
    build: str
    url: str
    size: int
    sha256: str
    mmdb_size: int
    mmdb_sha256: str
    licence: str
    attribution: str


@cache
def pin() -> Pin:
    return Pin(**json.loads(_data.path("dbip-city-lite.json").read_text()))


def cache_root() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "phasmid" / "geoip"


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def verify(path: Path, p: Pin) -> None:
    if not path.is_file():
        raise GeoipError(f"{path}: missing")
    if path.stat().st_size != p.mmdb_size or _file_sha256(path) != p.mmdb_sha256:
        raise GeoipError(f"{path}: size or sha256 differs from the pin ({p.name} {p.build})")


def install(archive: Path, p: Pin, root: Path) -> Path:
    """Decompress a downloaded archive into ``root/<sha256>/`` after checking both hashes."""
    if archive.stat().st_size != p.size or _file_sha256(archive) != p.sha256:
        raise GeoipError(
            f"{archive}: size or sha256 differs from the pin ({p.name} {p.build}, {p.url})"
        )
    root.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(dir=root, prefix=".partial-"))
    try:
        with gzip.open(archive, "rb") as src, (tmp / _MMDB).open("wb") as out:
            shutil.copyfileobj(src, out, 1 << 20)
        verify(tmp / _MMDB, p)
        dest = root / p.sha256
        if dest.exists():
            shutil.rmtree(dest)
        tmp.rename(dest)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return dest / _MMDB


def _download(url: str, dest: Path) -> None:
    context = ssl.create_default_context()  # verifies the certificate chain and host name
    # DB-IP's CDN answers Python's default User-Agent with 403.
    request = urllib.request.Request(url, headers={"User-Agent": "phasmid-profile-gen"})
    try:
        with (
            urllib.request.urlopen(request, context=context, timeout=120) as response,
            dest.open("wb") as out,
        ):
            shutil.copyfileobj(response, out, 1 << 20)
    except OSError as e:
        raise GeoipError(
            f"cannot download {url} ({e}); DB-IP keeps only the last two monthly builds. Install "
            "a copy of the pinned file with `phasmid-profile-gen geoip --archive <file>`, or point "
            f"{ENV_MMDB} at another DB-IP City Lite .mmdb"
        ) from e


def cached_path() -> Path:
    return cache_root() / pin().sha256 / _MMDB


def fetch(archive: Path | None = None) -> Path:
    """The verified pinned mmdb, downloading and installing it when it is not cached."""
    p = pin()
    path = cached_path()
    if archive is None and path.is_file():
        verify(path, p)
        return path
    if archive is not None:
        return install(archive, p, cache_root())
    cache_root().mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=cache_root(), prefix=".download-") as tmp:
        downloaded = Path(tmp) / "archive.mmdb.gz"
        _download(p.url, downloaded)
        return install(downloaded, p, cache_root())


@dataclass(frozen=True)
class Database:
    """An open mmdb and the provenance a profile records for it."""

    reader: Any  # maxminddb.Reader
    provenance: str

    def get(self, ip_address: str, /) -> Any:
        return self.reader.get(ip_address)


def _open(path: Path) -> Any:
    try:
        import maxminddb
    except ImportError:
        raise GeoipError(
            "placing a profile at an exit IP needs the location extra: "
            "pip install 'phasmid-profile-gen[location]'"
        ) from None
    db = maxminddb.open_database(str(path))
    if db.metadata().database_type != DATABASE_TYPE:
        raise GeoipError(
            f"{path}: database type {db.metadata().database_type}, expected {DATABASE_TYPE}"
        )
    return db


def _pinned_provenance() -> str:
    p = pin()
    licence = p.licence.split(",")[0]
    return f"{p.name} {p.build} (mmdb sha256 {p.mmdb_sha256}, {p.attribution}, {licence})"


@cache
def reader(mmdb: str | os.PathLike[str] | None = None) -> Database:
    """The database: ``mmdb``, else ``$PHASMID_GEOIP_MMDB``, else the pinned build (downloaded
    on first use)."""
    chosen = mmdb or os.environ.get(ENV_MMDB)
    if not chosen:
        return Database(_open(fetch()), _pinned_provenance())
    path = Path(chosen).expanduser()
    if not path.is_file():
        raise GeoipError(f"{path}: no such database file")
    db = _open(path)
    digest = _file_sha256(path)
    if digest == pin().mmdb_sha256:
        return Database(db, _pinned_provenance())
    built = datetime.fromtimestamp(db.metadata().build_epoch, UTC).date().isoformat()
    p = pin()
    return Database(
        db,
        f"{p.name} built {built} (mmdb sha256 {digest}, not the pinned {p.build}; "
        f"{p.attribution}, {p.licence.split(',')[0]})",
    )
