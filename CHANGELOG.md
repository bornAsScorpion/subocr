# Changelog

All notable changes to subocr are listed here. Versions follow
[semantic versioning](https://semver.org).

## [0.4.2] - 2026-11-10

Updated Documents.

## [0.4.1] - 2026-11-09

First public release.

### Added

- Extracts embedded text subtitles (SRT, ASS/SSA, WebVTT, mov_text) to `.srt`
  files next to the video, named the way Jellyfin, Plex, Emby and Bazarr expect.
- OCR of image-based subtitles (PGS and VobSub) with Tesseract via subtile-ocr,
  with a cleanup pass for common misreads and a quality score per subtitle.
- Track rules: commentary is skipped, untagged tracks are left alone, wrongly
  flagged "forced" tracks are detected, SDH handling is configurable.
- Service with a versioned HTTP API, webhook receivers for Radarr, Sonarr and
  Jellyfin, a persistent job queue and a nightly library scan.
- Works alongside the Jellyfin Subtitle Extract plugin (`TEXT_MODE`), and never
  overwrites subtitles written by Bazarr or anything else.
- Cleanup of subtitle files in unwanted languages and of orphans left behind by
  upgrades, with dry-run, archive and delete modes, an indexed archive and
  automatic restore when a language is added.
- OCR models for languages beyond English download on first start.
- Jellyfin item refresh after subtitles change (Jellyfin 10.x and 12).
- Runs as `PUID`/`PGID` with `PGID` as a supplementary group, for NAS shares with
  NFSv4 ACLs.
