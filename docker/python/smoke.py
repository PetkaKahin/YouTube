"""Check HTTP routing from inside the running Python container."""

import json
import urllib.error
import urllib.request

for path in ("/healthz", "/", "/api/docs"):
    with urllib.request.urlopen(f"http://nginx:8080{path}", timeout=10) as response:
        if response.status != 200:
            raise SystemExit(f"Unexpected status for {path}: {response.status}")

with urllib.request.urlopen("http://nginx:8080/api/openapi.json", timeout=10) as response:
    schema = json.load(response)
if not schema.get("openapi") or {"url": "/api"} not in schema.get("servers", []):
    raise SystemExit("OpenAPI is missing or does not use the public /api prefix")

print("Nginx, frontend, API docs and OpenAPI routing passed.")

for path in ("/.env", "/.env.local", "/docker/secrets/postgres_password", "/run/secrets/postgres_password"):
    try:
        urllib.request.urlopen(f"http://nginx:8080{path}", timeout=10).close()
    except urllib.error.HTTPError as error:
        if error.code == 404:
            continue
    raise SystemExit(f"Sensitive path must return 404: {path}")

print("Sensitive HTTP paths are blocked.")
