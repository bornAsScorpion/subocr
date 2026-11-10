# Cleanup and the archive

Releases often arrive with a dozen subtitle files you'll never use. subocr can
tidy those away into an archive, from which they come back automatically if you
later add their language.

## What gets kept and what goes

| Subtitle file next to the video | What happens |
| --- | --- |
| A language in `LANGUAGES` (with any flags: forced, SDH, default) | Kept |
| Another language subocr recognises | Archived (`CLEANUP_MODE=archive`) or deleted (`delete`) |
| A language subocr can't tell from the name, e.g. `Movie.iTunes.srt` | Kept. Archived only with `ARCHIVE_UNKNOWN=true`, never deleted |
| An orphan: its name matches no video in the folder | Follows `ORPHAN_SUBS`: deleted by default |

Orphans are what an upgrade leaves behind: Radarr replaces `Movie.720p.mkv`
with `Movie.1080p.mkv`, but `Movie.720p.en.srt` stays. Media servers only show
subtitles whose name starts with the video's, so orphans are dead weight.
subocr only looks for orphans in folders that contain at least one video, and
never in subfolders.

## Turning it on

Cleanup starts in `dry-run`: it logs what it would do and changes nothing.

1. **Preview** a folder, or a whole library:

   ```bash
   curl -s -H "X-Api-Key: $KEY" -H 'Content-Type: application/json' \
     -d '{"path": "/data/movies/Some Movie (2011)"}' \
     http://localhost:8585/api/v1/cleanup/preview | jq '.totals, .items'
   ```

2. When it looks right, set `CLEANUP_MODE=archive` and restart. The next nightly
   scan applies it across the library; to do it now:

   ```bash
   curl -s -H "X-Api-Key: $KEY" -H 'Content-Type: application/json' \
     -d '{"paths": ["/data/movies"], "force": true}' http://localhost:8585/api/v1/reconcile
   ```

## The archive

Archived files keep the library's layout under `ARCHIVE_ROOT`:

```
/data/movies/Some Movie (2011)/Some Movie (2011).da.srt
  → /archive/movies/Some Movie (2011)/Some Movie (2011).da.srt
```

Every archived or deleted file is recorded, so you can always see what went where:

```bash
curl -s -H "X-Api-Key: $KEY" "http://localhost:8585/api/v1/archive?path=/data/movies/Some%20Movie%20(2011)" | jq
```

## Getting files back

**Automatically:** add a language to `LANGUAGES` and restart. On each file's
next run, archived subtitles in that language are restored before anything is
extracted or OCR'd. If the video has been replaced by a different release since,
the restored subtitle is marked `restored-unverified`, because its timing may no
longer match.

**By hand:** restore one entry by its ID:

```bash
curl -s -X POST -H "X-Api-Key: $KEY" http://localhost:8585/api/v1/archive/<id>/restore
```

A file restored by hand is archived again on its next run unless its language
is in `LANGUAGES`.

## When it runs

After every job: each import, each API request, each file the nightly scan picks
up. The scan also comes back to a folder when something there is waiting for
cleanup or a restore, such as subtitles Bazarr added later, or a previous
attempt that failed. Changing `LANGUAGES`, `CLEANUP_MODE`, `ARCHIVE_UNKNOWN` or
`ORPHAN_SUBS` makes the next scan revisit the whole library.

A job for specific languages (an API request with `"languages": ["pl"]`) keeps
those languages too, so it never removes what it just produced.
