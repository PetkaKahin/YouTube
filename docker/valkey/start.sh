#!/bin/sh
set -eu
umask 077

# Shell variables stay local. The value is never exported or passed in argv.
password=$(cat "$VALKEY_PASSWORD_FILE")
if [ -z "$password" ]; then
    printf 'Valkey password secret must not be empty.\n' >&2
    exit 1
fi
password_hash=$(printf '%s' "$password" | sha256sum | cut -d ' ' -f 1)
unset password

# /data is tmpfs. Valkey ACLs accept SHA-256 hashes without storing the password.
printf 'user default reset on #%s ~* &* +@all\n' "$password_hash" > /data/users.acl
unset password_hash
printf 'include /etc/valkey/valkey.conf\naclfile /data/users.acl\n' > /data/runtime.conf

exec valkey-server /data/runtime.conf
