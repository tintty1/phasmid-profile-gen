# Changelog

## Unreleased

- Profiles come from the bundled captures only. BrowserForge's network and the
  fpgen GPU-claim extract are gone (with `forge.json.xz`, `fpgen.json.xz` and
  the `build-data` dependency group): every device field is one capture's,
  and `gpu=` selects a capture with that GPU.
- `ProfileGenerator` protocol for choosing the capture, passed as
  `generate_profile(generator=)`. `RandomCapture` (the default) draws one of
  the OS's captures uniformly from the seed; `from_capture` builds the profile.
- `list_gpus()` reports `captures` per GPU instead of `weight`.
- `MissingCaptures` no longer carries a draft; `generate --draft` is removed.

First release: Phasmid's `tools/genprofile` as a package, with its data bundled.

- Generates the same profiles as `tools/genprofile` from the same captures,
  byte for byte except for the generator's name in `captured.note`.
- Data built from Phasmid at the commit in `data/manifest.json`, BrowserForge's
  fingerprint network (`apify_fingerprint_datapoints` 0.15.0) and further
  captures: seven in all (one Linux, six Windows).
- Captures are named by OS family and PCI ids (`windows-0x8086-0x0412`). The
  manifest lists refused dumps by hash only.
- A Windows capture backs only its own marketing version (10 or 11), so the
  claimed `platformVersion` agrees with the capture's fonts and colours.
- One capture (`0x5916`) reports a heap limit that no whole-MB RAM gives
  exactly, so its RAM is the nearest whole MB (8053) and its heap limit the one
  the browser computes for it; `manifest.json` lists both as `assumed`.
- A Windows sample that drew 640 CPU threads (over the schema's 256; about 1 seed
  in 500) fails validation in `tools/genprofile`. It is drawn again here; every
  other seed's profile is the same as there.
- `PHASMID_GEOIP_MMDB` / `--geoip-db` use a DB-IP City Lite build other than
  the pinned one once DB-IP stops serving it.
- CLI subcommands: `generate`, `locate`, `gpus`, `captures`, `geoip`, `validate`.
