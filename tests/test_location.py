"""The exit-IP location derivation. No network: lookups use fixtures/geoip-rows.json; the real
database is read only by the opt-in test at the end (PHASMID_GEOIP_NETWORK=1)."""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import os

import jsonschema
import pytest
from conftest import COUNTRY_ONLY, FALKENSTEIN, GENEVA, HANOI, MONTREAL_V6, READER, SEEDS

import phasmid_profile_gen as ppg
from phasmid_profile_gen import _data, cli, geoip, locales
from phasmid_profile_gen import location as location_module
from phasmid_profile_gen._rng import decision_rng
from phasmid_profile_gen.captures import schema, validate
from phasmid_profile_gen.generate import GenerationError, dumps, generate
from phasmid_profile_gen.location import (
    ACCURACY_RANGE,
    LocationError,
    _destination,
    boundary_zone,
    device_zone,
    distance_m,
    languages_for,
    locate,
    position,
)

pytest.importorskip("timezonefinder", reason="needs the location extra")


def test_locale_geolocation_is_in_the_bundled_schema():
    assert "geolocation" in schema()["properties"]["locale"]["properties"]


@pytest.mark.parametrize("seed", SEEDS)
def test_same_seed_gives_the_same_location(seed):
    assert locate(HANOI, seed, READER) == locate(HANOI, seed, READER)


def test_different_seeds_give_different_positions():
    found = {
        (loc.latitude, loc.longitude, loc.accuracy)
        for loc in (locate(HANOI, s, READER) for s in SEEDS)
    }
    assert len(found) == 3


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("family", ["linux", "windows"])
def test_profile_from_exit_ip_is_byte_identical_and_schema_valid(family, seed):
    loc = locate(HANOI, seed, READER)
    a = generate(
        family, seed, loc.languages, loc.timezone, location=loc, location_note="fixture rows"
    )
    b = generate(
        family, seed, loc.languages, loc.timezone, location=loc, location_note="fixture rows"
    )
    assert dumps(a) == dumps(b)
    assert validate(a) == []
    assert a["locale"]["timezone"] == "Asia/Ho_Chi_Minh"
    assert a["locale"]["geolocation"] == loc.geolocation()
    assert a["network"]["webrtc"] == {"ip_handling_policy": "default", "public_ip": HANOI}
    assert "proxy exit IP: Hanoi, VN" in a["captured"]["note"]


def test_a_timezone_that_contradicts_the_exit_ip_is_refused():
    loc = locate(HANOI, SEEDS[0], READER)
    with pytest.raises(GenerationError, match="differs from the exit IP's zone"):
        generate("linux", SEEDS[0], loc.languages, "Asia/Bangkok", location=loc)


@pytest.mark.parametrize("alias, primary", [
    ("Asia/Saigon", "Asia/Ho_Chi_Minh"),
    ("Asia/Calcutta", "Asia/Kolkata"),
    ("Europe/Kiev", "Europe/Kyiv"),
    ("US/Eastern", "America/New_York"),
    ("Asia/Ho_Chi_Minh", "Asia/Ho_Chi_Minh"),
    # Merged into Brussels by tzdata, kept apart by CLDR and ICU.
    ("Europe/Amsterdam", "Europe/Amsterdam"),
])  # fmt: skip
def test_zone_aliases_resolve_to_the_iana_primary_name(alias, primary):
    assert locales.canonical_zone(alias) == primary


def test_every_zone_name_written_is_its_own_primary():
    primaries = set(locales.cldr()["zones"].values())
    assert all(locales.canonical_zone(z) == z for z in primaries)
    assert "Asia/Saigon" not in primaries and "Asia/Calcutta" not in primaries


def test_north_vietnam_gets_the_vietnamese_zone_not_the_boundary_polygon():
    """timezonefinder puts Hanoi in Asia/Bangkok's polygon; a Vietnamese device is on
    Asia/Ho_Chi_Minh."""
    assert boundary_zone(21.0278, 105.834) == "Asia/Bangkok"
    assert device_zone("Asia/Bangkok", "VN") == "Asia/Ho_Chi_Minh"
    assert device_zone("Europe/Belgrade", "XK") == "Europe/Belgrade"  # no zone of its own
    with pytest.raises(LocationError, match="not one of US's zones"):
        device_zone("America/Toronto", "US")


def test_a_country_only_row_is_refused():
    with pytest.raises(LocationError, match="resolves only to a country"):
        locate(COUNTRY_ONLY, SEEDS[0], READER)


@pytest.mark.parametrize("ip", ["10.0.0.1", "192.168.1.1", "not-an-ip", "::1"])
def test_private_and_malformed_addresses_are_refused(ip):
    with pytest.raises(LocationError):
        locate(ip, SEEDS[0], READER)


def test_an_address_missing_from_the_database_is_refused():
    with pytest.raises(LocationError, match="not in the database"):
        locate("1.1.1.1", SEEDS[0], READER)


@pytest.mark.parametrize("ip, city", [
    (HANOI, (21.0278, 105.834)),
    (GENEVA, (46.2044, 6.1432)),
    (FALKENSTEIN, (50.475, 12.365)),
    (MONTREAL_V6, (45.5019, -73.5674)),
])  # fmt: skip
def test_position_is_jittered_inside_the_accuracy_circle_and_the_city_zone(ip, city):
    geolocation = schema()["properties"]["locale"]["properties"]["geolocation"]
    for i in range(12):
        loc = locate(ip, f"{i:032x}", READER)
        assert ACCURACY_RANGE[0] <= loc.accuracy <= ACCURACY_RANGE[1]
        assert 0 < distance_m(city, (loc.latitude, loc.longitude)) <= loc.accuracy
        assert boundary_zone(loc.latitude, loc.longitude) == boundary_zone(*city)
        assert jsonschema.Draft202012Validator(geolocation).is_valid(loc.geolocation())


def _first_draw_zone(seed: str, lat: float, lon: float, accuracy: float) -> str | None:
    rng = decision_rng(seed, "geo-position@0")
    bearing, r = 2 * math.pi * rng.random(), accuracy * math.sqrt(rng.random())
    return boundary_zone(*(round(v, 6) for v in _destination(lat, lon, bearing, r)))


def test_geneva_jitter_retries_out_of_france():
    """Most of a 25-100 km disc around Geneva is in France: some seed needs more than one draw."""
    first_draw_zones = set()
    for i in range(12):
        seed = f"{i:032x}"
        loc = locate(GENEVA, seed, READER)
        assert (loc.timezone, boundary_zone(loc.latitude, loc.longitude)) == (
            "Europe/Zurich",
            "Europe/Zurich",
        )
        first_draw_zones.add(_first_draw_zone(seed, 46.2044, 6.1432, loc.accuracy))
    assert "Europe/Paris" in first_draw_zones


def test_jitter_that_never_stays_in_zone_fails_loudly(monkeypatch):
    monkeypatch.setattr(location_module, "boundary_zone", lambda lat, lon: "Europe/Paris")
    with pytest.raises(LocationError, match="after 32 draws"):
        position(SEEDS[0], 46.2044, 6.1432, "Europe/Zurich")


def test_languages_are_chromes_default_for_the_countrys_ui_locale():
    # IDS_ACCEPT_LANGUAGES of the UI locale
    # (components/strings/components_locale_settings_<lang>.xtb).
    assert languages_for(SEEDS[0], "VN") == ["vi-VN", "vi", "fr-FR", "fr", "en-US", "en"]
    assert languages_for(SEEDS[0], "DE") == ["de-DE", "de", "en-US", "en"]
    assert languages_for(SEEDS[0], "JP") == ["ja", "en-US", "en"]
    assert languages_for(SEEDS[0], "US") == ["en-US", "en"]
    assert languages_for(SEEDS[0], "BR") == ["pt-BR", "pt", "en-US", "en"]
    assert languages_for(SEEDS[0], "TW") == ["zh-TW", "zh", "en-US", "en"]
    assert languages_for(SEEDS[0], "MX") == ["es-419", "es"]
    swiss = {languages_for(s, "CH")[0] for s in (f"{i:032x}" for i in range(40))}
    assert swiss == {
        "de-DE",
        "fr-FR",
        "it-IT",
    }  # official, modern coverage (no gsw, no rm); Chrome has no -CH UI


@pytest.mark.parametrize(("language", "country", "locale"), [
    # ui/base/l10n/l10n_util_unittest.cc at Phasmid's Chromium tag (GetApplicationLocale).
    ("en", "US", "en-US"), ("en", "GB", "en-GB"), ("en", "AU", "en-GB"), ("en", "NZ", "en-GB"),
    ("en", "CA", "en-GB"), ("en", "ZA", "en-GB"), ("en", "LR", "en-US"), ("en", "PH", "en-US"),
    ("es", "MX", "es-419"), ("es", "AR", "es-419"), ("es", "ES", "es"), ("fr", "CA", "fr"),
    ("pt", "PT", "pt-PT"), ("pt", "BR", "pt-BR"), ("pt", "AO", "pt-PT"), ("zh_Hant", "HK", "zh-TW"),
    ("zh_Hant", "MO", "zh-TW"), ("zh", "SG", "zh-CN"), ("zh", "CN", "zh-CN"), ("no", "NO", "nb"),
    ("ca", "ES", "ca"), ("de", "AT", "de"), ("he", "IL", "he"), ("fil", "PH", "fil"),
    # No desktop UI in that language: GetApplicationLocale falls back to en-US.
    ("rm", "CH", "en-US"),
])  # fmt: skip
def test_ui_locale_resolves_as_chrome_does(language, country, locale):
    assert locales.ui_locale(language, country) == locale


def test_accept_language_table_is_the_bundled_chromiums():
    data = locales.chromium()
    assert data["source"]["chromium"] == _data.manifest()["sources"]["phasmid"]["chromium"]
    assert set(data["accept_languages"]) == set(data["desktop_locales"])
    assert data["accept_languages"]["en-US"] == ["en-US", "en"]
    assert data["accept_languages"]["ms"] == [
        "en-US",
        "en",
    ]  # ms.xtb has no translation: grit's English text
    assert data["accept_languages"]["nb"] == [
        "nb-NO",
        "nb",
        "no",
        "nn",
        "en-US",
        "en",
    ]  # the "no" xtb, as nb.pak


def test_cli_derives_the_location_group(tmp_path, fake_geoip):
    out = tmp_path / "p.json"
    assert (
        cli.main(
            ["generate", "--os", "linux", "--seed", SEEDS[0], "--exit-ip", HANOI, "-o", str(out)]
        )
        == 0
    )
    p = json.loads(out.read_text())
    loc = locate(HANOI, SEEDS[0], READER)
    assert p["locale"] == {
        "languages": loc.languages,
        "timezone": "Asia/Ho_Chi_Minh",
        "geolocation": loc.geolocation(),
    }
    assert p["network"]["webrtc"]["public_ip"] == HANOI
    assert "in fixture rows, timezonefinder" in p["captured"]["note"]
    assert "languages from CLDR" in p["captured"]["note"]


def test_cli_languages_override_the_country(tmp_path, fake_geoip):
    out = tmp_path / "p.json"
    argv = [
        "generate",
        "--os",
        "linux",
        "--seed",
        SEEDS[0],
        "--exit-ip",
        HANOI,
        "--languages",
        "en-US,en",
    ]
    assert cli.main([*argv, "-o", str(out)]) == 0
    p = json.loads(out.read_text())
    assert (
        p["locale"]["languages"] == ["en-US", "en"]
        and p["locale"]["timezone"] == "Asia/Ho_Chi_Minh"
    )
    assert "languages from the caller" in p["captured"]["note"]


def test_cli_refuses_a_country_only_exit(tmp_path, fake_geoip, capsys):
    argv = ["generate", "--os", "linux", "--seed", SEEDS[0], "--exit-ip", COUNTRY_ONLY]
    assert cli.main([*argv, "-o", str(tmp_path / "p.json")]) == 1
    assert "resolves only to a country" in capsys.readouterr().err


def test_cli_timezone_and_exit_ip_are_exclusive(tmp_path):
    with pytest.raises(SystemExit):
        cli.main(["generate", "--os", "linux", "--exit-ip", HANOI, "--timezone", "UTC"])


def test_cli_locate(fake_geoip, capsys):
    assert cli.main(["locate", HANOI, "--seed", SEEDS[0]]) == 0
    answer = json.loads(capsys.readouterr().out)
    assert answer == locate(HANOI, SEEDS[0], READER).__dict__


def test_generate_profile_with_an_exit_ip(fake_geoip):
    p = ppg.generate_profile("windows", SEEDS[0], exit_ip=GENEVA)
    assert (
        p["locale"]["timezone"] == "Europe/Zurich" and p["network"]["webrtc"]["public_ip"] == GENEVA
    )
    assert p["locale"]["languages"] == locate(GENEVA, SEEDS[0], READER).languages


def test_without_an_exit_ip_nothing_changes():
    p = generate("linux", SEEDS[0], ["en-US", "en"], "America/New_York")
    assert "geolocation" not in p["locale"] and "public_ip" not in p["network"]["webrtc"]
    assert p["captured"]["note"].endswith("; locale from the caller.")


def test_geoip_install_checks_the_pin(tmp_path):
    mmdb = b"not really an mmdb"
    archive = tmp_path / "a.mmdb.gz"
    archive.write_bytes(gzip.compress(mmdb, mtime=0))
    data = archive.read_bytes()
    pin = geoip.Pin(
        name="t",
        build="2026-10",
        url="https://example.invalid/a.mmdb.gz",
        size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        mmdb_size=len(mmdb),
        mmdb_sha256=hashlib.sha256(mmdb).hexdigest(),
        licence="CC BY 4.0",
        attribution="t",
    )
    path = geoip.install(archive, pin, tmp_path / "cache")
    assert path.read_bytes() == mmdb
    with pytest.raises(geoip.GeoipError, match="differs from the pin"):
        geoip.install(
            archive, geoip.Pin(**{**pin.__dict__, "sha256": "0" * 64}), tmp_path / "cache"
        )
    with pytest.raises(geoip.GeoipError, match="differs from the pin"):
        geoip.install(
            archive, geoip.Pin(**{**pin.__dict__, "mmdb_sha256": "0" * 64}), tmp_path / "cache2"
        )
    assert not list((tmp_path / "cache2").glob(".partial-*"))


def test_a_missing_database_file_is_refused(tmp_path):
    with pytest.raises(geoip.GeoipError, match="no such database file"):
        geoip.reader(tmp_path / "missing.mmdb")


@pytest.mark.network
@pytest.mark.skipif(
    os.environ.get("PHASMID_GEOIP_NETWORK") != "1",
    reason="opt-in: reads the real DB-IP build (downloads the pinned one when not cached)",
)
def test_real_database_vietnam():
    loc = ppg.locate(HANOI, SEEDS[0])
    assert (loc.country, loc.city, loc.timezone) == ("VN", "Hanoi", "Asia/Ho_Chi_Minh")
    assert loc.languages[:2] == ["vi-VN", "vi"]
