# Bundled data: sources and licences

Every file here is built by `scripts/build_data.py`; `manifest.json` names each
source version and each file's sha256.

| File | Source | Licence |
| --- | --- | --- |
| `captures.json.xz` | Harness dumps of real devices mapped onto the Phasmid profile schema. Only device data is kept, under a neutral name: no raw dump, no file name, no locale. | BSD 3-Clause (Phasmid) |
| `locale.json.xz` `cldr` | Phasmid's `profiles/data/cldr/cldr.json`, extracted from [Unicode CLDR](https://cldr.unicode.org/) (tag in the file). | Unicode License v3, `CLDR-LICENSE` |
| `locale.json.xz` `chromium` | Phasmid's `profiles/data/chromium/accept-languages.json`: Chromium's `IDS_ACCEPT_LANGUAGES` per desktop UI locale at Phasmid's Chromium tag, each source file named by git blob id. | Chromium's BSD-style licence |
| `schema.json.xz` | Phasmid's `profiles/schema.json`. | BSD 3-Clause (Phasmid) |
| `dbip-city-lite.json` | The pin (URL, sizes, sha256) of a DB-IP IP to City Lite build. The database is downloaded on first use, never bundled. | The database: CC BY 4.0, "IP Geolocation by DB-IP, https://db-ip.com"; profiles placed with it name it in `captured.note` |

Not bundled, but used at runtime by the `location` extra: time zone boundaries
from [timezone-boundary-builder](https://github.com/evansiroky/timezone-boundary-builder)
via `timezonefinder-data` (ODbL 1.0, © OpenStreetMap contributors).
