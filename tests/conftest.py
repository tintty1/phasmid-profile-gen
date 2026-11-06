from __future__ import annotations

import ipaddress
import json
from pathlib import Path

import pytest

from phasmid_profile_gen import geoip

FIXTURES = Path(__file__).parent / "fixtures"
SEEDS = (
    "0a1b2c3d4e5f60718293a4b5c6d7e8f9",
    "5f3c9e1a7b2d4086c1e9a3f5b7d20e48",
    "c4e8a2f6b1d3957e0f2a4c6e8b1d3f57",
)
HANOI, GENEVA, FALKENSTEIN, MONTREAL_V6, COUNTRY_ONLY = (
    "14.161.0.1",
    "46.14.0.9",
    "5.9.0.1",
    "2001:4860:4860::8888",
    "8.8.8.8",
)


class FixtureReader:
    """The maxminddb Reader.get interface over fixtures/geoip-rows.json."""

    def __init__(self) -> None:
        rows = json.loads((FIXTURES / "geoip-rows.json").read_text())["rows"]
        self.rows = [(ipaddress.ip_network(r["network"]), r["record"]) for r in rows]

    def get(self, ip: str):
        addr = ipaddress.ip_address(ip)
        return next((rec for net, rec in self.rows if addr in net), None)


READER = FixtureReader()


@pytest.fixture
def fake_geoip(monkeypatch):
    """geoip.reader answers from the fixture rows, whatever database is asked for."""
    db = geoip.Database(READER, "fixture rows")
    monkeypatch.setattr(geoip, "reader", lambda mmdb=None: db)
    return db
