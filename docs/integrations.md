# Integrations

## Radarr and Sonarr

subocr learns about new files from Radarr and Sonarr webhooks. Setup takes a
minute per app: see **[webhooks.md](webhooks.md)**.

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

Only needed for libraries Radarr and Sonarr don't manage. Setup with the Jellyfin
**Webhook** plugin is in [webhooks.md](webhooks.md#jellyfin-optional).

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
