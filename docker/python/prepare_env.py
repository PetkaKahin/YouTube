"""Prepare non-secret settings and Compose Secret files inside the bootstrap container."""

import os
import secrets
import shlex
import tempfile
from pathlib import Path

directory = Path('/project')
destination = directory / '.env'
secret_directory = directory / 'docker/secrets'
secret_names = {
    'CSRF_SECRET': 'csrf_secret',
    'POSTGRES_PASSWORD': 'postgres_password',
    'VALKEY_PASSWORD': 'valkey_password',
    'RUSTFS_ACCESS_KEY': 'rustfs_access_key',
    'RUSTFS_SECRET_KEY': 'rustfs_secret_key',
}


def assignment(line):
    key, separator, value = line.partition('=')
    key = key.strip().removeprefix('export ').strip()
    if not separator or key.startswith('#'):
        return None
    return key, value


def legacy_value(raw, name):
    # Decode literals without evaluating shell commands or environment expansion.
    if not raw.strip() or raw.lstrip().startswith('#'):
        return None
    try:
        tokens = shlex.split(raw, comments=False)
    except ValueError:
        raise SystemExit(f'Invalid legacy setting {name}; move it to its secret file.') from None
    if len(tokens) > 1 and not tokens[1].startswith('#'):
        raise SystemExit(f'Invalid legacy setting {name}; move it to its secret file.')
    return tokens[0] if tokens and tokens[0] else None


def validate(value, name):
    if not value or any(character in value for character in '\r\n\0'):
        raise SystemExit(f'Secret {name} must contain one nonempty UTF-8 line.')
    if name.startswith('rustfs_') and value != value.strip():
        raise SystemExit(f'Secret {name} must not have surrounding whitespace.')
    return value


def atomic_write(path, value, mode):
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as output:
        temporary = Path(output.name)
        output.write(value)
    try:
        temporary.chmod(mode)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


example = (directory / '.env.example').read_text()
content = destination.read_text() if destination.exists() else example
lines = []
legacy = {}
for line in content.splitlines():
    item = assignment(line)
    if item and item[0] in secret_names:
        value = legacy_value(item[1], item[0])
        if value:
            if item[0] in legacy and legacy[item[0]] != value:
                raise SystemExit(f'Conflicting legacy settings for {item[0]}.')
            legacy[item[0]] = value
        continue
    lines.append(line)

positions = {item[0] for line in lines if (item := assignment(line))}
for line in example.splitlines():
    item = assignment(line)
    if item and item[0] not in positions:
        lines.append(line)
        positions.add(item[0])

# Validate every existing value before writing files or removing legacy values.
values = {}
for variable, filename in secret_names.items():
    path = secret_directory / filename
    value = legacy.get(variable)
    if path.exists():
        try:
            current = validate(path.read_bytes().decode('utf-8').removesuffix('\n'), filename)
        except (OSError, UnicodeError):
            raise SystemExit(f'Cannot read secret {filename}.') from None
        if value is not None and value != current:
            raise SystemExit(f'Conflicting .env and secret file for {variable}; resolve manually.')
        value = current
    elif value is None:
        value = secrets.token_hex(10).upper() if variable == 'RUSTFS_ACCESS_KEY' else secrets.token_hex(24)
    values[filename] = validate(value, filename)

secret_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
secret_directory.chmod(0o700)
for filename, value in values.items():
    path = secret_directory / filename
    if not path.exists():
        atomic_write(path, value + '\n', 0o444)
    else:
        # Compose file secrets retain host file ownership/modes. Containers have different UIDs.
        path.chmod(0o444)

updated = '\n'.join(lines) + '\n'
if updated != content or not destination.exists():
    atomic_write(destination, updated, 0o600)

print('.env and Compose Secret files prepared; existing values preserved; no credentials printed.')
