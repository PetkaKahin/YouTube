#!/bin/sh
set -eu
umask 077

RC_CONFIG_DIR=$(mktemp -d /tmp/rc.XXXXXX)
export RC_CONFIG_DIR
trap 'rm -rf "$RC_CONFIG_DIR"' EXIT
trap 'exit 1' HUP INT TERM

# Import credentials through a private tmpfs file. No values in argv or environment.
jq -n --rawfile access "$RUSTFS_ACCESS_KEY_FILE" --rawfile secret "$RUSTFS_SECRET_KEY_FILE" '
  {schema_version: 1, aliases: [{name: "bootstrap", endpoint: "http://rustfs:9000",
    credentials: {access_key: ($access | rtrimstr("\n")), secret_key: ($secret | rtrimstr("\n"))},
    region: "us-east-1", signature: "v4", bucket_lookup: "path", insecure: false}]}
' > "$RC_CONFIG_DIR/import.json"
rc alias import "$RC_CONFIG_DIR/import.json" --quiet
rc bucket create bootstrap/videos --ignore-existing --quiet

# No anonymous policy is installed. Verify that listing the bucket is forbidden.
status=$(curl --silent --show-error --output /dev/null --write-out '%{http_code}' \
    http://rustfs:9000/videos)
if [ "$status" != 403 ]; then
    printf 'RustFS bucket privacy check failed (HTTP %s).\n' "$status" >&2
    exit 1
fi
printf 'Private videos bucket ready.\n'

if [ "$#" -gt 0 ]; then
    "$@"
fi
