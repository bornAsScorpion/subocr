#!/bin/sh
# Run as PUID:PGID (default 1000:1000), like the linuxserver.io *arr images.
#
# PGID is also set as a supplementary group, as linuxserver images do (their user is a
# member of the PGID group). NFS servers with NFSv4 ACLs, such as TrueNAS, can match
# group ACL entries against supplementary groups only; without this, files reachable
# only through a group entry (e.g. written by another user over SMB) are unreadable.
# EXTRA_GIDS adds more supplementary groups: EXTRA_GIDS=1001,545
set -e

PUID="${PUID:-1000}"
PGID="${PGID:-1000}"
umask "${UMASK:-022}"

if [ "$(id -u)" = "0" ]; then
    mkdir -p /config "${SCRATCH_DIR:-/scratch}"
    # Only our own folders; the media mount is never chowned
    chown "$PUID:$PGID" /config "${SCRATCH_DIR:-/scratch}" 2>/dev/null || true
    [ -d /archive ] && chown "$PUID:$PGID" /archive 2>/dev/null || true
    [ -f /config/subocr.db ] && chown "$PUID:$PGID" /config/subocr.db* /config/api_key 2>/dev/null || true
    export HOME=/config
    GROUPS_LIST="$PGID${EXTRA_GIDS:+,$EXTRA_GIDS}"
    exec setpriv --reuid="$PUID" --regid="$PGID" --groups="$GROUPS_LIST" -- "$@"
fi

exec "$@"
