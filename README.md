# subocr

**Subtitles for every file in your library, without you lifting a finger.**

Plenty of releases ship their subtitles *inside* the video file, and plenty of
players only pick up subtitles that sit *next to* it. Worse, a lot of releases,
especially Blu-ray and DVD rips, carry subtitles as images (PGS or VobSub)
rather than text. Players then either can't show them or have to burn them into
the picture, which costs a full transcode.

subocr runs next to Radarr, Sonarr and Bazarr and closes that gap. When a file
is imported, it:

- **extracts** embedded text subtitles to `.srt` files next to the video;
- **reads** image-based subtitles with OCR (Tesseract) and turns them into
  `.srt`, with timing taken from the release itself;
- **tidies up** subtitle files in languages you don't want, moving them to an
  archive you can restore from later;
- **keeps out of the way** of Bazarr and the Jellyfin subtitle plugin: it never
  overwrites a subtitle file that is already there.

Output follows the naming Jellyfin, Plex, Emby and Bazarr expect:
`Movie (2011).en.srt`, `Movie (2011).en.forced.srt`, `Movie (2011).en.hi.srt`.

## Quick start

```yaml
services:
  subocr:
    image: ghcr.io/bornasscorpion/subocr:latest
    container_name: subocr
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=Etc/UTC
      - LIBRARY_PATHS=/data/movies,/data/tv
      - LANGUAGES=en
    volumes:
      - ./subocr/config:/config
      - ./subocr/archive:/archive
      - /path/to/media:/data
    ports:
      - 8585:8585
    restart: unless-stopped
```

Use the same media mount and the same `PUID`/`PGID` as your Radarr and Sonarr
containers. Then:

1. Start it, and note the API key it generated: `cat ./subocr/config/api_key`.
2. In Radarr and Sonarr, add a webhook under **Settings → Connect** pointing at
   `http://subocr:8585/api/v1/webhooks/radarr?apikey=<key>` (or `/sonarr`).
   See [docs/integrations.md](docs/integrations.md).
3. Leave it running. New imports are handled as they arrive, and a nightly
   scan works through everything that was already in your library.

A fuller example is in [compose.example.yml](compose.example.yml).

## How it works

For every file, and for each language in `LANGUAGES`, subocr uses the cheapest
source that works:

1. a subtitle file already next to the video (from Bazarr, the release, or an
   earlier run) wins, and is left alone;
2. a subtitle archived earlier is restored;
3. an embedded text track is extracted;
4. an embedded image track is read with OCR.

Along the way it skips commentary tracks, leaves untagged tracks alone, notices
when a release has wrongly flagged a full track as "forced", and prefers the
regular track over SDH. OCR output gets a cleanup pass for Tesseract's typical
mistakes, such as music notes read as stray letters.

Nothing is ever written over an existing file, and media files are never
modified.

## Documentation

| | |
| --- | --- |
| [Configuration](docs/configuration.md) | Every setting, with defaults |
| [Integrations](docs/integrations.md) | Radarr, Sonarr, Jellyfin and Bazarr |
| [Cleanup and the archive](docs/cleanup.md) | Which subtitle files get tidied away, and how to get them back |
| [API](docs/api.md) | Submitting files, checking jobs, previews |
| [Troubleshooting](docs/troubleshooting.md) | Permissions on NFS/SMB shares, OCR models, common log messages |

## Good to know

- **Languages:** the image includes the English OCR model. Models for other
  languages in `LANGUAGES` are downloaded once on first start into `/config`.
- **OCR quality** is very good on clean Blu-ray (PGS) and DVD (VobSub)
  subtitles: around 98–99% of words came out right in testing across English,
  German, Hungarian and Polish. It is not perfect: expect the odd misread
  letter, and italics are not preserved.
- **Cleanup starts in dry-run mode.** It only reports what it would do until you
  set `CLEANUP_MODE=archive`. Preview first with
  `POST /api/v1/cleanup/preview`.
- **Architecture:** images are published for `linux/amd64`.

## Contributing

Bug reports and pull requests are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE). subocr builds on [FFmpeg](https://ffmpeg.org),
[MKVToolNix](https://mkvtoolnix.download), [Tesseract](https://github.com/tesseract-ocr/tesseract)
and [subtile-ocr](https://github.com/gwen-lg/subtile-ocr), each under its own license.
