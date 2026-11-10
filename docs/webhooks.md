# Webhooks

Webhooks tell subocr about new files the moment they land, instead of waiting for
the nightly scan. Set them up in Radarr and Sonarr; the Jellyfin webhook is optional.

Before you start, you need two things:

- **The API key.** Either the `API_KEY` you set, or the one subocr generated on
  first start:

  ```bash
  cat ./subocr/config/api_key        # or: docker exec subocr cat /config/api_key
  ```

- **An address the sender can reach.** If Radarr and Sonarr run in the same
  Docker Compose project or network as subocr, use `http://subocr:8585`.
  Otherwise use the Docker host's address and the published port, e.g.
  `http://192.168.1.20:8585`.

Every webhook URL below ends in `?apikey=<key>`, because not every sender lets you
add headers. Where you can add headers, you can send `X-Api-Key: <key>` instead
and leave the query parameter off.

## Radarr

1. **Settings → Connect → +** (Add Connection) → **Webhook**.
2. Fill in:

   | Field | Value |
   | --- | --- |
   | Name | `subocr` |
   | Notification Triggers | **On File Import**, **On File Upgrade**, **On Rename**, **On Movie File Delete** |
   | Webhook URL | `http://subocr:8585/api/v1/webhooks/radarr?apikey=<key>` |
   | Method | `POST` |
   | Username / Password | leave empty |

3. Press **Test**. A green tick means subocr received it and the key is right.
4. **Save**.

Leave the other triggers off. *On Grab*, *On Movie Added* and the health
notifications are accepted but ignored. *On Movie File Delete For Upgrade* is
unnecessary, because the upgrade event already covers the new file.

## Sonarr

1. **Settings → Connect → +** (Add Connection) → **Webhook**.
2. Fill in:

   | Field | Value |
   | --- | --- |
   | Name | `subocr` |
   | Notification Triggers | **On File Import**, **On File Upgrade**, **On Rename**, **On Episode File Delete** |
   | Webhook URL | `http://subocr:8585/api/v1/webhooks/sonarr?apikey=<key>` |
   | Method | `POST` |
   | Username / Password | leave empty |

3. Press **Test**, then **Save**.

Season packs work as expected: each episode file becomes its own job. If your
Sonarr also offers **On Import Complete**, ticking it is harmless. The episodes it
reports are already queued, and subocr merges the duplicate requests.

## Jellyfin (optional)

Radarr and Sonarr already cover every file they manage, so this webhook is only
useful for libraries that you fill some other way, such as home videos or
manually copied files.

It needs the **Webhook** plugin, and subocr needs to be able to ask Jellyfin for
an item's file path. So first set these two on subocr and restart it:

```yaml
- JELLYFIN_URL=http://jellyfin:8096
- JELLYFIN_API_KEY=<key from Jellyfin: Dashboard → API Keys → +>
```

If Jellyfin sees your media at a different path than subocr does (for example
`/media` in Jellyfin and `/data` in subocr), also add
`PATH_MAPPINGS=/media:/data`.

Then, in Jellyfin:

1. **Dashboard → Plugins → Catalog → Webhook → Install**, and restart Jellyfin.
2. **Dashboard → Plugins → Webhook** → **Add Generic Destination**.
3. Fill in:

   | Field | Value |
   | --- | --- |
   | Webhook Name | `subocr` |
   | Webhook Url | `http://<subocr-host>:8585/api/v1/webhooks/jellyfin?apikey=<key>` |
   | Notification Type | **Item Added** only |
   | Item Type | **Movies**, **Episodes** |
   | Send All Properties | off |
   | Template | see below |

   Template:

   ```json
   {"NotificationType": "{{NotificationType}}", "ItemType": "{{ItemType}}", "ItemId": "{{ItemId}}"}
   ```

4. **Save**.

The template must not be empty: an empty template sends an empty request, which
subocr rejects with `400 empty request body`. Field names in the plugin vary a
little between versions; what matters is a Generic (JSON) destination, the
*Item Added* event, and that template.

Jellyfin runs on a separate host or LXC? Use the Docker host's address in the
URL, and make sure port 8585 is reachable from there.

## Checking that it works

**From the sender:** Radarr and Sonarr's **Test** button should give a green tick.
A red one with *401* means the key is wrong; a timeout or *connection refused*
means the address or port is wrong.

**From subocr:** after an import, the file shows up as a job:

```bash
curl -s -H "X-Api-Key: $KEY" "http://localhost:8585/api/v1/jobs?limit=5" | jq '.[] | {id, source, status, outcome, path}'
```

The `source` field says which webhook sent it (`radarr`, `sonarr` or `jellyfin`).

**Without waiting for an import,** you can send a webhook by hand. This is what
Radarr sends when a movie is imported:

```bash
curl -s -H "X-Api-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"eventType": "Download",
       "movie": {"folderPath": "/data/movies/Some Movie (2011)"},
       "movieFile": {"relativePath": "Some Movie (2011).mkv"}}' \
  http://localhost:8585/api/v1/webhooks/radarr | jq
```

The answer lists the accepted paths and the queued job IDs. Paths that were
refused are listed under `rejected`, with the reason. The interactive API docs at
`http://<host>:8585/docs` let you send the same payloads from the browser, with
an example body for each webhook.

## When something's off

| Symptom | Likely cause |
| --- | --- |
| `401` | Wrong or missing API key in the URL or header |
| `"status": "ignored"`, path under `rejected` with *outside LIBRARY_PATHS* | The path Radarr/Sonarr reports isn't inside subocr's `LIBRARY_PATHS`: mount the media at the same path in all containers, or add `PATH_MAPPINGS` |
| *file not found* in `rejected` | subocr can't see the file at that path: check its volume mounts |
| `400 empty request body` from Jellyfin | The webhook's template is empty |
| Jellyfin webhook accepted but `"paths": []` | `JELLYFIN_URL` / `JELLYFIN_API_KEY` missing or wrong, so subocr couldn't look up the item. Check `docker logs subocr` for *Jellyfin lookup failed* |
| Job created, text subtitles appear only later | Expected with `TEXT_MODE=auto`: text tracks are handled after `PLUGIN_GRACE_MINUTES` |

More in [troubleshooting.md](troubleshooting.md).
