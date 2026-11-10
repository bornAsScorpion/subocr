# Troubleshooting

Start with the logs (`docker logs subocr`) and the event list:

```bash
curl -s -H "X-Api-Key: $KEY" "http://localhost:8585/api/v1/events?limit=20" | jq
```

## "Permission denied" on a NAS share

subocr reads, writes and moves subtitle files as `PUID`/`PGID`. When several
apps write to the same share as different users, for instance Jellyfin over SMB
and the *arr stack over NFS, files can end up readable only by whoever created
them.

- **Give one group access to everything.** On the NAS, grant a group (e.g. the
  one your *arr containers use as `PGID`) modify rights on the media dataset,
  inherited by new files and folders, and apply it recursively. Make every
  account that writes to the share a member.
- **NFSv4 ACLs (e.g. TrueNAS):** group entries may be checked against
  *supplementary* groups only. subocr always runs with `PGID` as a supplementary
  group, as the linuxserver.io images do. If your files rely on another group,
  add it with `EXTRA_GIDS`.
- **Check what subocr sees,** as its own user:

  ```bash
  docker exec subocr grep -E '^(Uid|Gid|Groups)' /proc/1/status
  docker exec subocr setpriv --reuid=1000 --regid=1000 --groups=1000 -- head -c1 "/data/path/to/file.srt"
  ```

Failed cleanups are logged with the step that failed: `read-source`,
`write-archive` or `remove-source`.

## "Operation not permitted" when writing

Some NAS shares refuse `chmod` (TrueNAS datasets with `aclmode=restricted`, many
SMB mounts). subocr never needs `chmod`, so this only shows up from other tools;
it's harmless to subocr.

## A language says `bitmap-no-model`

OCR needs that language's model and it isn't installed. Check the startup log
for a line about downloading it. If the download failed (no internet access, a
proxy), either allow access to `github.com`, point `OCR_MODEL_URL` at a mirror,
or build the image with the model baked in (see
[configuration.md](configuration.md#ocr)).

## Nothing happens on import

- In Radarr/Sonarr, press **Test** on the webhook: you should get a green tick.
  [webhooks.md](webhooks.md#when-somethings-off) lists what each error means.
- Check the path in the webhook is inside `LIBRARY_PATHS`. Rejected paths are
  logged with the reason.
- With `TEXT_MODE=auto`, text tracks are handled `PLUGIN_GRACE_MINUTES` after
  import. That's expected.

## Files that were already there are not being processed

The nightly scan (`RECONCILE_AT`) picks up new and changed files, and folders
with pending cleanup. To process a folder now, whatever its state:

```bash
curl -s -H "X-Api-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"paths": ["/data/tv/Some Show"], "force": true}' http://localhost:8585/api/v1/reconcile
```

## A subtitle shows `{\an8}` or appears in the wrong place

`{\an8}` is a positioning tag from the release ("show at the top"). subocr copies
text tracks as they are. Most players honour it; some web players ignore the
position but still show the text.
