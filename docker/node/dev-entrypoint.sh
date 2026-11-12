#!/bin/sh
set -eu

current_digest=$(sha256sum package-lock.json | cut -d ' ' -f 1)
installed_digest=$(cat node_modules/.lock-digest 2>/dev/null || true)
if [ "$current_digest" != "$installed_digest" ]; then
    npm ci
    printf '%s\n' "$current_digest" > node_modules/.lock-digest
fi

exec "$@"
