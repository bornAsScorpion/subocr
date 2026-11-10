# API

Everything subocr does can be driven over HTTP. Interactive docs are at
`http://<host>:8585/docs`, and the OpenAPI spec at `/openapi.json`, so you can
generate a client in your language of choice.

**Authentication:** send the key as an `X-Api-Key` header, or as an `apikey`
query parameter (for webhook senders that can't set headers). `/health` needs no
key.

## Endpoints

| Endpoint | What it does |
| --- | --- |
| `GET /api/v1/system/status` | Version, OCR models, languages, queue counts, scan and cleanup settings |
| `POST /api/v1/jobs` | Process a file; answers `202` with job IDs |
| `GET /api/v1/jobs/{id}` | A job's status and result |
| `GET /api/v1/jobs?status=queued` | Recent jobs, optionally by status |
| `GET /api/v1/events?event=archived` | Recent events, optionally by type |
| `POST /api/v1/reconcile` | Scan now: `{"paths": [...], "force": true}`, both optional |
| `POST /api/v1/cleanup/preview` | What cleanup would do for a file or folder; changes nothing |
| `GET /api/v1/archive?path=&status=` | Archived and deleted subtitles |
| `POST /api/v1/archive/{id}/restore` | Put one archived subtitle back |
| `POST /api/v1/webhooks/radarr`, `/sonarr`, `/jellyfin` | Webhook receivers |

## Submitting a file

```bash
curl -s -H "X-Api-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"path": "/data/movies/Some Movie (2011)/Some Movie (2011).mkv"}' \
  http://localhost:8585/api/v1/jobs
```

| Field | Default | Meaning |
| --- | --- | --- |
| `path` | required | The video, as the caller sees it (`PATH_MAPPINGS` applies) |
| `languages` | `LANGUAGES` | Languages for this file only, e.g. `["en", "pl"]` |
| `scope` | `full` | `ocr-only` skips embedded text tracks |
| `callback_url` | none | Receives one POST with the result when the work is done |
| `source` | `api` | A name that shows up in logs and job records |

Answers:

- `202`: accepted, with `job_ids`. Asking again while the file is still queued
  returns the same IDs with `"deduplicated": true`.
- `400`: the path is outside `LIBRARY_PATHS`.
- `404`: the file doesn't exist.
- `422`: unknown language or invalid field.
- `429`: the queue is full; try again after `Retry-After` seconds.

subocr always re-checks the file itself: a request says *look at this file*, not
*do exactly this*.

## Job results

```bash
curl -s -H "X-Api-Key: $KEY" http://localhost:8585/api/v1/jobs/42 | jq '.outcome, .result.targets'
```

Common outcomes:

| Outcome | Meaning |
| --- | --- |
| `complete` | Every wanted language is already covered |
| `text-extracted` | Subtitles were extracted from text tracks |
| `ocr-done` | At least one subtitle was produced by OCR |
| `restored` | Archived subtitles were brought back |
| `left-to-plugin` | Text tracks are waiting for the Jellyfin plugin |
| `no-wanted-language` | The file has none of your languages |
| `untagged-skipped` | Tracks without a language tag were left alone |
| `bitmap-no-model` | OCR needed, but the language's model is missing |
| `failed` | See `error` and the per-language `detail` |

OCR results include a `quality` score from 0 to 1. Clean OCR scores above 0.99;
a noticeably lower score usually means a hard-to-read source.

## Command line

The image also runs one-off commands; anything after the image name replaces
the service:

```bash
docker run --rm -v /path/to/media:/data:ro ghcr.io/bornasscorpion/subocr probe "/data/movies/Some Movie (2011)/Some Movie (2011).mkv"
docker exec subocr subocr cleanup-preview "/data/movies/Some Movie (2011)/Some Movie (2011).mkv"
docker run --rm ghcr.io/bornasscorpion/subocr models
```

| Command | What it does |
| --- | --- |
| `probe FILE` | Lists subtitle tracks and what would be produced; writes nothing |
| `process FILE` | Produces missing subtitles for one file. `--dry-run`, `--lang en,de`, `--output-dir DIR`, `--ignore-existing` (with `--output-dir`), `--json` |
| `cleanup-preview FILE` | What cleanup would keep, archive or delete |
| `models` | Installed OCR language models |
| `serve` | The service (the default) |
