"""Check an actual BuildKit context export without printing file contents."""

import sys
import tarfile
from pathlib import Path, PurePosixPath

component = sys.argv[1]
required = {
    'python': {'backend/uv.lock', 'backend/pyproject.toml', 'backend/src/youtube/bootstrap/app.py'},
    'node': {'frontend/package-lock.json', 'frontend/package.json', 'frontend/src/app/main.ts'},
    'nginx': {'docker/nginx/nginx.conf', 'docker/nginx/prod.conf'},
    'root': {'backend/uv.lock', 'frontend/package-lock.json', 'docker-compose.yml'},
}[component]
values = [path.read_bytes().rstrip(b'\n') for path in Path('/project/docker/secrets').iterdir()
          if path.is_file()]
present = set()
violations = set()
with tarfile.open(fileobj=sys.stdin.buffer, mode='r|*') as archive:
    for member in archive:
        path = PurePosixPath(member.name)
        present.add(str(path))
        if any(part == '.env' or part.startswith('.env.') for part in path.parts):
            violations.add('env file')
        if '.git' in path.parts or 'node_modules' in path.parts:
            violations.add('metadata or dependencies')
        if str(path).startswith(('docker/secrets', 'frontend/dist')):
            violations.add('secrets or build output')
        if member.isfile():
            source = archive.extractfile(member)
            if source is not None:
                overlap = b''
                while chunk := source.read(65536):
                    combined = overlap + chunk
                    if any(value and value in combined for value in values):
                        violations.add('secret value')
                    overlap = combined[-256:]
if not required.issubset(present):
    violations.add('required source or lock missing')
if violations:
    print(f'{component}: context check failed ({", ".join(sorted(violations))})')
    sys.exit(1)
print(f'{component}: BuildKit context checked; required files included, secrets/artifacts excluded.')
