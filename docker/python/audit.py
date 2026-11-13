"""Inspect this project's Docker surfaces, reporting secret names rather than values.

Run via docker/run audit. Docker socket access is privileged; the script uses
inspection endpoints and read-only commands in the selected project containers.
"""

import base64
import hashlib
import http.client
import json
import socket
import sys
from pathlib import Path
from urllib.parse import quote


class DockerConnection(http.client.HTTPConnection):
    def __init__(self):
        super().__init__('localhost', timeout=45)

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect('/var/run/docker.sock')


def request(method, path, payload=None, allowed=(200, 201)):
    connection = DockerConnection()
    body = json.dumps(payload).encode() if payload is not None else None
    connection.request(method, path, body, {'Content-Type': 'application/json'})
    response = connection.getresponse()
    data = response.read()
    connection.close()
    if response.status not in allowed:
        raise RuntimeError(f'Docker inspection failed: HTTP {response.status} at {path}')
    return response.status, data


def demux(data):
    # Docker's non-TTY logs/exec streams have an eight-byte header per frame.
    if not data or data[0] not in (0, 1, 2) or data[1:4] != b'\0\0\0':
        return data
    output = bytearray()
    while len(data) >= 8:
        size = int.from_bytes(data[4:8], 'big')
        output.extend(data[8:8 + size])
        data = data[8 + size:]
    return bytes(output)


def execute(identifier, command, user=None):
    specification = {'AttachStdout': True, 'AttachStderr': True, 'Cmd': command}
    if user is not None:
        specification['User'] = user
    _, created = request('POST', f'/containers/{identifier}/exec', specification)
    execution = json.loads(created)['Id']
    _, output = request('POST', f'/exec/{execution}/start', {'Detach': False, 'Tty': False})
    _, inspected = request('GET', f'/exec/{execution}/json')
    if json.loads(inspected)['ExitCode'] != 0:
        raise RuntimeError('A read-only inspection command failed')
    return demux(output)


def main():
    identifiers = sys.argv[1:]
    if not identifiers:
        raise RuntimeError('No project containers selected')
    values = {}
    for path in sorted(Path('/project/docker/secrets').iterdir()):
        if path.is_file():
            value = path.read_bytes().rstrip(b'\n')
            if not value:
                raise RuntimeError('An empty secret file was found')
            values[path.name] = {
                'literal': value,
                'base64': base64.b64encode(value),
                'urlencoded': quote(value.decode(), safe='').encode(),
                'sha256': hashlib.sha256(value).hexdigest().encode(),
            }
    if not values:
        raise RuntimeError('No secret files available for comparison')
    findings = []
    checks = 0
    process_coverage = []
    admin_can_read_secret = False
    allowed_secrets = {
        'python': {'postgres_password', 'valkey_password', 'rustfs_access_key', 'rustfs_secret_key',
                   'csrf_secret'},
        'postgres': {'postgres_password'},
        'valkey': {'valkey_password'},
        'rustfs': {'rustfs_access_key', 'rustfs_secret_key'},
        'rustfs-init': {'rustfs_access_key', 'rustfs_secret_key'},
        'node': set(),
        'nginx': set(),
    }

    def scan(service, surface, data):
        nonlocal checks
        checks += 1
        for name, encodings in values.items():
            for encoding, value in encodings.items():
                if value in data:
                    findings.append({'service': service, 'surface': surface, 'secret': name,
                                     'encoding': encoding})
                    break

    scan('compose', 'resolved configuration', sys.stdin.buffer.read())
    images = set()
    for identifier in identifiers:
        _, raw = request('GET', f'/containers/{identifier}/json')
        container = json.loads(raw)
        service = container['Config']['Labels']['com.docker.compose.service']
        scan(service, 'container inspect', raw)
        mounted = {item['Destination'].removeprefix('/run/secrets/')
                   for item in container['Mounts']
                   if item['Destination'].startswith('/run/secrets/')}
        if mounted != allowed_secrets[service]:
            findings.append({'service': service, 'surface': 'unexpected secret grants'})
        _, logs = request('GET', f'/containers/{identifier}/logs?stdout=1&stderr=1')
        scan(service, 'container logs', demux(logs))
        image = container['Image']
        if image not in images:
            images.add(image)
            for suffix in ('json', 'history'):
                status, metadata = request('GET', f'/images/{image}/{suffix}', allowed=(200, 404))
                if status == 404:
                    reference = quote(container['Config']['Image'], safe='')
                    _, metadata = request('GET', f'/images/{reference}/{suffix}')
                scan(service, 'image ' + suffix, metadata)
        if not container['State']['Running']:
            continue
        user = 'postgres' if service == 'postgres' else None
        for surface, filename in [('process environment', 'environ'), ('process arguments', 'cmdline')]:
            script = ('for item in /proc/[0-9]*/' + filename + '; do '
                      'printf "AUDIT_FILE:%s\\n" "$item"; '
                      'if cat "$item" 2>/dev/null; then printf "\\nAUDIT_OK\\n"; '
                      'else printf "\\nAUDIT_UNREADABLE\\n"; fi; done')
            content = execute(identifier, ['sh', '-c', script], user)
            process_coverage.append({'service': service, 'surface': surface,
                                     'readable': content.count(b'\nAUDIT_OK\n'),
                                     'unreadable': content.count(b'\nAUDIT_UNREADABLE\n')})
            if b'\nAUDIT_OK\n' not in content:
                raise RuntimeError('Could not inspect any process files for ' + service)
            scan(service, surface, content)
        # These files are never legitimate image/source inputs in application containers.
        for filename in ('/app/.env', '/app/docker/secrets', '/usr/share/nginx/html/.env'):
            status, _ = request('GET', f'/containers/{identifier}/archive?path={quote(filename)}',
                                allowed=(200, 404))
            if status == 200:
                findings.append({'service': service, 'surface': 'unexpected config file',
                                 'path': filename})
        if service in ('python', 'node', 'nginx'):
            directory = '/usr/share/nginx/html' if service == 'nginx' else '/app/src'
            status, archive = request('GET', f'/containers/{identifier}/archive?path={quote(directory)}',
                                      allowed=(200, 404))
            if status == 200:
                scan(service, 'application files', archive)
        if service == 'python':
            # This is intended privileged access, not an accidental disclosure surface.
            _, archive = request('GET', f'/containers/{identifier}/archive?path=/run/secrets/postgres_password')
            admin_can_read_secret = values['postgres_password']['literal'] in archive
            if not admin_can_read_secret:
                raise RuntimeError('Could not verify the Docker administrator trust boundary')
            script = ("import urllib.request\n"
                      "for path in ('/', '/api/openapi.json', '/api/docs', '/.env', '/docker/secrets/postgres_password'):\n"
                      " try:\n"
                      "  with urllib.request.urlopen('http://nginx:8080'+path, timeout=10) as response:\n"
                      "   print(response.read().decode(errors='replace'))\n"
                      " except urllib.error.HTTPError as error:\n"
                      "  print(error.read().decode(errors='replace'))\n")
            scan(service, 'HTTP responses', execute(identifier, ['python', '-c', script]))
    print(json.dumps({'containers': len(identifiers), 'surfaces_checked': checks,
                      'process_coverage': process_coverage,
                      'docker_admin_can_read_mounted_secrets': admin_can_read_secret,
                      'findings': findings, 'status': 'fail' if findings else 'pass'}, indent=2))
    return bool(findings)


try:
    sys.exit(main())
except Exception as error:
    # Do not include exception text: upstream responses could contain a credential.
    detail = str(error) if isinstance(error, RuntimeError) else None
    print(json.dumps({'status': 'error', 'error_type': type(error).__name__, 'detail': detail}),
          file=sys.stderr)
    sys.exit(2)
