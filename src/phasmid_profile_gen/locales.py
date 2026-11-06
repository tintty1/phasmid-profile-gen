"""The CLDR and Chromium locale facts the location derivation needs.

Both are Phasmid's committed extracts, bundled as ``data/locale.json.xz``:

- ``cldr``: from Unicode CLDR at its ``source.tag`` (Unicode License v3,
  ``data/CLDR-LICENSE``). ``zones`` maps every time zone ID to its IANA primary
  name (Asia/Saigon -> Asia/Ho_Chi_Minh); ``zone_regions`` gives each primary
  name's territory; ``territories`` lists per territory the modern-coverage
  languages of ``territoryInfo`` as [language, population percent, official
  status]; ``parent_locales`` is CLDR's default locale inheritance.
- ``chromium``: Chrome's default accept-languages (``IDS_ACCEPT_LANGUAGES``)
  per desktop UI locale at the Chromium tag Phasmid builds, the same on Windows
  and Linux.

The UI locale follows the OS locale: ``l10n_util::GetApplicationLocale``
resolves it with ``ui_l10n::GetPlatformLanguageMatcher``, which walks CLDR's
locale inheritance (es_MX -> es_419 -> es) to the nearest desktop locale.
``ui_locale`` follows the same walk; ``ANCESTORS`` are the matcher's edges from a
locale Chrome has no resources for to the one it prefers.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from . import _data

# CLDR locale (underscore form) -> the desktop UI locale the matcher picks for it when it is
# not itself one (base/i18n/language_tag_matcher.cc at the tag): GetEdgeWeight prefers
# en -> en-US, en-001 -> en-GB, pt -> pt-BR, zh -> zh-CN, zh-Hant -> zh-TW;
# LanguageTagPreferenceGraph adds en-PH and en-LR -> en-US and en-CA -> en-GB; ICU4X falls nb
# back to no, so no (and nn, whose CLDR parent is no) reaches nb.
ANCESTORS = {
    "en": "en-US",
    "en_001": "en-GB",
    "en_CA": "en-GB",
    "en_LR": "en-US",
    "en_PH": "en-US",
    "no": "nb",
    "pt": "pt-BR",
    "zh": "zh-CN",
    "zh_Hans": "zh-CN",
    "zh_Hant": "zh-TW",
}
# GetApplicationLocale's answer when nothing matches.
FALLBACK_LOCALE = "en-US"


class LocaleDataError(Exception):
    pass


def cldr() -> dict[str, Any]:
    return _data.load("locale")["cldr"]


def chromium() -> dict[str, Any]:
    return _data.load("locale")["chromium"]


def cldr_tag() -> str:
    return cldr()["source"]["tag"]


def chromium_tag() -> str:
    return chromium()["source"]["chromium"]


def canonical_zone(name: str) -> str:
    """The IANA primary name of a zone ID, e.g. Asia/Saigon -> Asia/Ho_Chi_Minh."""
    try:
        return cldr()["zones"][name]
    except KeyError:
        raise LocaleDataError(f"time zone {name!r} is not in CLDR {cldr_tag()}") from None


def zone_region(name: str) -> str | None:
    """The territory of an IANA primary zone name; None for UTC and the Etc zones."""
    return cldr()["zone_regions"].get(name)


def region_zones(region: str) -> list[str]:
    return sorted(z for z, r in cldr()["zone_regions"].items() if r == region)


def territory_languages(country: str) -> list[list[Any]]:
    """[language, population percent, official status] rows of a territory."""
    return cldr()["territories"].get(country, [])


def parent_locale(locale: str) -> str | None:
    """CLDR's parent of a locale in underscore form (es_MX -> es_419); None when it inherits
    by truncation."""
    return cldr()["parent_locales"].get(locale)


def _chain(locale: str) -> Iterator[str]:
    """``locale`` and its CLDR ancestors, nearest first (es_MX, es_419, es)."""
    while locale and locale != "root":
        yield locale
        locale = parent_locale(locale) or locale.rpartition("_")[0]


def ui_locale(language: str, country: str) -> str:
    """Chrome's UI locale on an OS set to ``language`` (a CLDR code, possibly with a script:
    zh_Hant) in ``country``: de, DE -> de; es, MX -> es-419; zh_Hant, HK -> zh-TW."""
    desktop = set(chromium()["desktop_locales"])
    for node in _chain(f"{language}_{country}"):
        tag = node.replace("_", "-")
        if node in ANCESTORS:
            return ANCESTORS[node]
        if tag in desktop:
            return tag
    return FALLBACK_LOCALE


def accept_languages(locale: str) -> list[str]:
    """The default intl.accept_languages of a desktop UI locale, in order."""
    try:
        return list(chromium()["accept_languages"][locale])
    except KeyError:
        raise LocaleDataError(
            f"{locale!r} is not a desktop UI locale of Chromium {chromium_tag()}"
        ) from None
