# Integrations

## Radarr and Sonarr

subocr learns about new files from Radarr and Sonarr webhooks. In each app, go
to **Settings → Connect → + → Webhook**:

| Field | Radarr | Sonarr |
| --- | --- | --- |
| Triggers | On File Import, On File Upgrade, On Rename, On Movie File Delete | On File Import, On File Upgrade, On Rename, On Episode File Delete |
| URL | `http://subocr:8585/api/v1/webhooks/radarr?apikey=<key>` | `http://subocr:8585/api/v1/webhooks/sonarr?apikey=<key>` |
| Method | POST | POST |

Press **Test**: you should get a green tick. The key is the `API_KEY` you set, or
the one in `/config/api_key`.

The URL works as written when subocr is on the same Docker network as Radarr and
Sonarr. Otherwise, use the host's address and published port.

**Paths:** subocr must see each file at the path Radarr and Sonarr report.
Mounting your media at the same container path in all of them (e.g. `/data`)
keeps this simple.

**Extra files:** keep `srt` in Radarr/Sonarr's *Import Extra Files* list. subocr
decides what to keep, and archives the rest so you can bring it back later.

## Jellyfin

### Refresh after new subtitles

Set `JELLYFIN_URL` and `JELLYFIN_API_KEY`, and subocr asks Jellyfin to refresh an
item whenever it adds, restores or removes subtitles, so they show up without a
library scan. Works with Jellyfin 10.x and 12.

### Webhook (optional)

Only needed for libraries Radarr and Sonarr don't manage. With the Jellyfin
**Webhook** plugin, add a *Generic* destination:

- URL: `http://<subocr-host>:8585/api/v1/webhooks/jellyfin?apikey=<key>`
- Notification type: *Item Added*; item types: Movies, Episodes
- Template:

  ```json
  {"NotificationType": "{{NotificationType}}", "ItemType": "{{ItemType}}", "ItemId": "{{ItemId}}"}
  ```

subocr then asks Jellyfin for the file's path, so `JELLYFIN_URL` and
`JELLYFIN_API_KEY` are needed. If Jellyfin sees the library at a different path,
add a `PATH_MAPPINGS` entry.

### Subtitle Extract plugin

subocr works alongside the Jellyfin *Subtitle Extract* plugin, or replaces it.
The plugin can't read image-based subtitles; subocr can. With the default
`TEXT_MODE=auto`, OCR runs straight away and text tracks are left to the plugin
for `PLUGIN_GRACE_MINUTES`; subocr fills in whatever the plugin didn't produce.
If the plugin stops working after a Jellyfin upgrade, nothing needs changing.

## Bazarr

subocr and Bazarr share the folder without stepping on each other:

- subocr never overwrites a subtitle file Bazarr has written.
- A subtitle subocr wrote looks like any other external subtitle to Bazarr. If
  you'd rather have a downloaded one, use Bazarr's manual search for that
  language: it replaces the file.

## Other tools

Anything that can make an HTTP request can hand subocr a file. See
[api.md](api.md).
