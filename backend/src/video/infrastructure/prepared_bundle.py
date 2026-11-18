"""Local checked packages and content-addressed prefixes for prepared seed imports."""

import hashlib
import json
import re
from pathlib import Path
from typing import Any, cast

from video.application.prepared_media import PreparedMediaError


def artifact_digest(artifact: dict[str, Any]) -> str:
    content = {key: value for key, value in artifact.items() if key != "prefix"}
    return hashlib.sha256(
        json.dumps(content, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def prepared_prefix(profile: str, artifact: dict[str, Any]) -> str:
    if re.fullmatch(r"[0-9a-f]{64}", profile) is None:
        raise PreparedMediaError("Invalid prepared profile")
    return f"prepared/{profile}/{artifact_digest(artifact)}/"


def file_path(directory: Path, profile: str, name: str) -> Path:
    if (
        re.fullmatch(r"[0-9a-f]{64}", profile) is None
        or re.fullmatch(r"[a-zA-Z0-9_.-]{1,100}", name) is None
        or name in {".", ".."}
    ):
        raise PreparedMediaError("Invalid prepared file path")
    path = directory / profile / name
    if directory.is_symlink() or path.parent.is_symlink() or path.is_symlink():
        raise PreparedMediaError("Prepared files cannot be symlinks")
    return path


def read_bundle(directory: Path) -> dict[str, Any]:
    try:
        manifest = directory / "bundle.json"
        if directory.is_symlink() or manifest.is_symlink() or manifest.stat().st_size > 4 * 1024**2:
            raise PreparedMediaError("Invalid prepared manifest")
        value: object = json.loads(manifest.read_text())
        if not isinstance(value, dict):
            raise PreparedMediaError("Invalid prepared manifest")
        bundle = cast(dict[str, Any], value)
        if (
            type(bundle.get("version")) is not int
            or bundle["version"] != 1
            or not 1 <= len(bundle["outputs"]) <= 64
        ):
            raise PreparedMediaError("Unsupported prepared bundle")
        profiles: set[str] = set()
        for output in bundle["outputs"]:
            profile = output["profile_key"]
            if profile in profiles:
                raise PreparedMediaError("Duplicate prepared output")
            profiles.add(profile)
            artifact = output["artifact"]
            if output["status"] != "ready" or artifact["prefix"] != prepared_prefix(
                profile, artifact
            ):
                raise PreparedMediaError("Invalid prepared output")
            names: set[str] = set()
            for entry in artifact["files"]:
                name, size, digest = entry["name"], entry["size"], entry["sha256"]
                if (
                    name in names
                    or type(size) is not int
                    or not 0 < size <= 128 * 1024**2
                    or re.fullmatch(r"[0-9a-f]{64}", digest) is None
                ):
                    raise PreparedMediaError("Invalid prepared file descriptor")
                names.add(name)
                path = file_path(directory, profile, name)
                if path.stat().st_size != size:
                    raise PreparedMediaError("Prepared file size mismatch")
                with path.open("rb") as content:
                    if hashlib.file_digest(content, "sha256").hexdigest() != digest:
                        raise PreparedMediaError("Prepared file checksum mismatch")
            if not names:
                raise PreparedMediaError("Empty prepared package")
        return bundle
    except (OSError, KeyError, TypeError, ValueError) as error:
        if isinstance(error, PreparedMediaError):
            raise
        raise PreparedMediaError("Prepared bundle is missing or invalid") from None
