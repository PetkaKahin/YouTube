"""Prepared bundle integrity and path restrictions, without invoking media programs."""

import copy
import hashlib
import json

import pytest

from video.application.prepared_media import PreparedMediaError
from video.domain.delivery import StoredMedia
from video.infrastructure.prepared_bundle import prepared_prefix, read_bundle
from video.infrastructure.readiness import valid_artifact_prefix


def sample(tmp_path):
    profile = "a" * 64
    path = tmp_path / profile
    path.mkdir()
    (path / "candidate_0.webp").write_bytes(b"fixture")
    artifact = {
        "kind": "thumbnail_set",
        "files": [
            {
                "name": "candidate_0.webp",
                "size": 7,
                "sha256": hashlib.sha256(b"fixture").hexdigest(),
            }
        ],
    }
    artifact["prefix"] = prepared_prefix(profile, artifact)
    bundle = {
        "version": 1,
        "outputs": [{"profile_key": profile, "status": "ready", "artifact": artifact}],
    }
    return bundle


@pytest.mark.parametrize(
    "problem", ["version", "path", "symlink", "size", "hash", "prefix", "duplicate"]
)
def test_invalid_bundle_is_rejected_before_import(tmp_path, problem):
    bundle = sample(tmp_path)
    output = bundle["outputs"][0]
    artifact = output["artifact"]
    if problem == "version":
        bundle["version"] = True
    elif problem == "path":
        artifact["files"][0]["name"] = "../../private"
    elif problem == "symlink":
        file = tmp_path / output["profile_key"] / "candidate_0.webp"
        file.unlink()
        file.symlink_to(tmp_path / "outside")
    elif problem == "size":
        artifact["files"][0]["size"] = 999
    elif problem == "hash":
        artifact["files"][0]["sha256"] = "0" * 64
    elif problem == "prefix":
        artifact["prefix"] = "../other/"
    elif problem == "duplicate":
        bundle["outputs"].append(copy.deepcopy(output))
    if problem != "prefix":
        artifact["prefix"] = prepared_prefix(output["profile_key"], artifact)
    (tmp_path / "bundle.json").write_text(json.dumps(bundle))
    with pytest.raises(PreparedMediaError):
        read_bundle(tmp_path)


def test_prepared_prefix_binds_profile_and_descriptor(tmp_path):
    bundle = sample(tmp_path)
    output = bundle["outputs"][0]
    output["id"] = 2**63 - 1
    assert valid_artifact_prefix(output)
    key = output["artifact"]["prefix"] + "candidate_0.webp"
    assert StoredMedia(key, 7, "image/webp").key == key
    output["artifact"]["files"][0]["size"] += 1
    assert not valid_artifact_prefix(output)


@pytest.mark.parametrize(
    "key",
    [
        "prepared/a/b/image.webp",
        "prepared/" + "a" * 64 + "/" + "b" * 64 + "/../private",
        "https://outside/image.webp",
    ],
)
def test_prepared_storage_key_does_not_allow_arbitrary_paths(key):
    with pytest.raises(ValueError):
        StoredMedia(key, 7, "image/webp")
