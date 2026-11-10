# Configuration

Everything is set through environment variables. The defaults are chosen so a
first start changes nothing it can't undo.

## Container basics

| Variable | Default | What it does |
| --- | --- | --- |
| `PUID` / `PGID` | `1000` / `1000` | User and group subocr runs as. Use the same values as your *arr containers. |
| `UMASK` | `022` | Permissions for files subocr creates. |
| `EXTRA_GIDS` | none | Extra supplementary groups, comma-separated. `PGID` is always included. |
| `TZ` | `UTC` | Time zone; the nightly scan runs at local time. |
| `PORT` | `8585` | HTTP port for the API and webhooks. |

## Libraries and access

| Variable | Default | What it does |
| --- | --- | --- |
| `LIBRARY_PATHS` | required | Library folders, comma-separated, as subocr sees them: `/data/movies,/data/tv`. Requests for files outside these are refused. |
| `PATH_MAPPINGS` | none | Translates paths sent by apps that mount the library elsewhere. Pairs of `their-prefix:our-prefix`, e.g. `/media:/data`. Works like the remote path mappings in Radarr and Sonarr. |
| `API_KEY` | generated | Key required on every API and webhook call. If unset, one is generated on first start and saved to `/config/api_key`. |
| `JOB_QUEUE_LIMIT` | `500` | Above this many waiting jobs, new requests get `429 Too Many Requests`. |

## Languages

| Variable | Default | What it does |
| --- | --- | --- |
| `LANGUAGES` | `en` | Languages to produce, in order of preference, as two-letter codes: `en,de,fr`. Also the languages cleanup keeps. |
| `DEFAULT_LANGUAGES` | `en` | Which produced language to mark as default, in order of preference (only with `DEFAULT_MARKING`). |
| `DEFAULT_MARKING` | `false` | Add `.default` to the file name of the default language. Usually better left off: set the preferred subtitle language in your player instead. |
| `TEXT_FALLBACK` | `none` | When a file has none of your languages: `none` does nothing, `first-stream` extracts the first text subtitle whatever its language. OCR is never used for this. |

## Track choices

| Variable | Default | What it does |
| --- | --- | --- |
| `HI_POLICY` | `prefer-regular` | SDH / hearing-impaired tracks. `prefer-regular` uses SDH only when there is no regular track; `both` produces both (`.hi.srt` for SDH); `skip` ignores SDH tracks. |
| `COMMENTARY_REGEX` | `(?i)comment\|director\|коммент` | Track titles matching this are commentary and never extracted. |
| `FORCED_MAX_CUES_PER_MIN` | `2` | A track flagged "forced" with more subtitles per minute than this is really a full track, and is treated as one. |
| `TEXT_MODE` | `auto` | Who handles embedded *text* tracks. `container`: subocr, straight away. `plugin`: leave them to the Jellyfin Subtitle Extract plugin. `auto`: OCR straight away, text extraction after `PLUGIN_GRACE_MINUTES`, only for what the plugin didn't do. |
| `PLUGIN_GRACE_MINUTES` | `10` | How long `auto` gives the Jellyfin plugin before stepping in. |

## OCR

| Variable | Default | What it does |
| --- | --- | --- |
| `OCR_ENABLED` | `true` | Turn OCR off to only extract text tracks. |
| `OCR_BLACKLIST` | ``\|\/`_~`` | Characters Tesseract may never output; removes common junk. |
| `SCRATCH_DIR` | `/scratch` | Where image tracks are unpacked for OCR. Put it on local disk, not on the NAS. |
| `TESSDATA_DIR` | `/config/tessdata` | Where OCR language models live. |
| `OCR_MODEL_DOWNLOAD` | `true` | Download models for `LANGUAGES` on start. Turn off on hosts without internet access. |
| `OCR_MODEL_URL` | tessdata 4.1.0 | Where to download models from: a URL with `{model}` in it, for a local mirror. |

The image includes English. On start, subocr downloads the model for every
other language in `LANGUAGES` (`de` → `deu.traineddata`, a few MB to about
20 MB) into `TESSDATA_DIR`, once. The files come from the
[tessdata 4.1.0 release](https://github.com/tesseract-ocr/tessdata/tree/4.1.0),
the same models Debian ships. If a download fails, that language reports
`bitmap-no-model` until the next start.

To bake models into the image instead, build it yourself:

```bash
docker build --build-arg TESSERACT_LANGS="eng deu fra" -t subocr:local .
```

## Cleanup and archive

See [cleanup.md](cleanup.md) for how these play together.

| Variable | Default | What it does |
| --- | --- | --- |
| `CLEANUP_MODE` | `dry-run` | `off`, `dry-run` (report only), `archive` or `delete`. |
| `ARCHIVE_ROOT` | `/archive` | Where archived subtitles go. Must be outside every library, or your media server would pick them up. |
| `ARCHIVE_UNKNOWN` | `false` | Also archive subtitle files whose language can't be told from the name. They are never deleted. |
| `ORPHAN_SUBS` | `delete` | Subtitle files matching no video in their folder, typically left behind by an upgrade: `delete`, `archive` or `keep`. |

## Scheduling and housekeeping

| Variable | Default | What it does |
| --- | --- | --- |
| `RECONCILE_AT` | `03:30` | Time of the nightly library scan (local time), or `off`. |
| `JOB_RETENTION_DAYS` | `30` | How long finished jobs and events are kept. |

## Jellyfin

| Variable | Default | What it does |
| --- | --- | --- |
| `JELLYFIN_URL` | none | e.g. `http://jellyfin:8096`. With an API key, subocr asks Jellyfin to refresh an item after adding subtitles. |
| `JELLYFIN_API_KEY` | none | Create one under **Dashboard → API Keys**. |
