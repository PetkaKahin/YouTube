"""Seeder recovery and boundaries without replacing the real media qualification."""

import asyncio
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch
from urllib.request import Request

import pytest
from pydantic import ValidationError

from app import seed


def test_manifest_preserves_bigint_and_rejects_unknown_clip(tmp_path):
    path = tmp_path / "state.json"
    state = seed.State(
        accounts={"alice": (2**63 - 2, 2**63 - 1)}, videos={"fern-canyon-1": 2**63 - 1}
    )
    seed.save_state(path, state)
    assert seed.read_state(path) == state
    assert not path.with_suffix(".tmp").exists()
    state.videos["foreign"] = 1
    seed.save_state(path, state)
    with pytest.raises(seed.SeedError):
        seed.read_state(path)


@pytest.mark.parametrize("value", [True, -1, 0, 2**63, "1"])
def test_manifest_rejects_invalid_identity(tmp_path, value):
    path = tmp_path / "state.json"
    import json

    path.write_text(json.dumps({"videos": {"fern-canyon-1": value}}))
    with pytest.raises((ValidationError, seed.SeedError)):
        seed.read_state(path)


def test_second_seed_cannot_enter_same_workspace(tmp_path):
    with seed.seed_lock(tmp_path):
        with pytest.raises(seed.SeedError, match="already running"):
            with seed.seed_lock(tmp_path):
                pytest.fail("Concurrent seed entered")
    with seed.seed_lock(tmp_path):
        pass


@pytest.mark.parametrize("person", [seed.PEOPLE[0], seed.ADMIN])
def test_existing_account_without_manifest_is_not_adopted(tmp_path, person):
    api = Mock(spec=seed.SeedAPI)
    api.call.side_effect = [{"_status": 404}, {"_status": 409}]
    path = tmp_path / "state.json"
    with pytest.raises(seed.SeedError, match="already in use"):
        seed.login_person(api, person, seed.State(), path)
    assert not path.exists()
    assert not any(call.args[1] == "/auth/login" for call in api.call.call_args_list)


def test_existing_account_must_match_recorded_account_and_channel(tmp_path):
    person = seed.PEOPLE[0]
    api = Mock(spec=seed.SeedAPI)
    api.call.side_effect = [
        {},
        {"_status": 409},
        {"id": 1, "name": person.name},
        {"id": 3, "name": person.name},
    ]
    state = seed.State(accounts={person.key: (1, 2)})
    with pytest.raises(seed.SeedError, match="conflicts"):
        seed.login_person(api, person, state, tmp_path / "state.json")


def test_registered_identity_is_persisted_before_login(tmp_path):
    person = seed.PEOPLE[0]
    api = Mock(spec=seed.SeedAPI)
    api.call.side_effect = [
        {},
        {"account_id": 4, "channel_id": 5},
        seed.SeedError("Connection lost"),
    ]
    path = tmp_path / "state.json"
    with pytest.raises(seed.SeedError):
        seed.login_person(api, person, seed.State(), path)
    assert seed.read_state(path).accounts[person.key] == (4, 5)


def import_scope(identity=9):
    importer = AsyncMock(return_value=SimpleNamespace(id=identity))
    operation = MagicMock()
    operation.__aenter__.return_value = operation
    operation.__aexit__.return_value = None
    operation.get = AsyncMock(return_value=importer)
    return Mock(return_value=operation), importer


def test_ready_video_keeps_identity_and_uses_only_prepared_import(tmp_path):
    clip = seed.CLIPS[0]
    api = Mock(spec=seed.SeedAPI)
    api.call.return_value = {"description": clip.description}
    state = seed.State(videos={clip.key: 9})
    app, importer = import_scope()
    with patch.object(seed, "media_file", return_value=tmp_path):
        assert (
            asyncio.run(seed.ensure_video(api, clip, 1, state, tmp_path / "state.json", app)) == 9
        )
    assert importer.await_args.args[0] == 1
    assert importer.await_args.args[-1] == 9
    api.call.assert_called_once_with("GET", "/videos/9/settings", allowed=(404,))


def test_changed_or_foreign_manifest_target_is_not_modified(tmp_path):
    api = Mock(spec=seed.SeedAPI)
    api.call.return_value = {"description": "Real user video"}
    app, importer = import_scope()
    with pytest.raises(seed.SeedError, match="different video"):
        asyncio.run(
            seed.ensure_video(
                api,
                seed.CLIPS[0],
                1,
                seed.State(videos={"fern-canyon-1": 8}),
                tmp_path / "state.json",
                app,
            )
        )
    importer.assert_not_awaited()


def test_failed_prepared_import_does_not_claim_a_created_video(tmp_path):
    app, importer = import_scope()
    importer.side_effect = seed.PreparedMediaError("Unavailable")
    path = tmp_path / "state.json"
    state = seed.State()
    with patch.object(seed, "media_file", return_value=tmp_path):
        with pytest.raises(seed.PreparedMediaError):
            asyncio.run(
                seed.ensure_video(Mock(spec=seed.SeedAPI), seed.CLIPS[0], 1, state, path, app)
            )
    assert not state.videos and not path.exists()


def test_bundled_media_needs_no_external_network(tmp_path):
    import json

    directory = tmp_path / "fern-canyon-1"
    directory.mkdir()
    path = directory / "bundle.json"
    path.write_bytes(b"bundled sample")
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "version": 2,
                "clips": {
                    "fern-canyon-1": {
                        "bundle_sha256": hashlib.sha256(path.read_bytes()).hexdigest()
                    }
                },
            }
        )
    )
    with patch.object(seed, "build_opener", side_effect=AssertionError("Unexpected network")):
        assert seed.media_file(seed.CLIPS[0], tmp_path) == directory


@pytest.mark.parametrize("missing", [False, True])
def test_missing_or_changed_bundled_media_fails_before_upload(tmp_path, missing):
    import json

    directory = tmp_path / "fern-canyon-1"
    directory.mkdir()
    path = directory / "bundle.json"
    if not missing:
        path.write_bytes(b"changed sample")
    (tmp_path / "manifest.json").write_text(
        json.dumps({"version": 2, "clips": {"fern-canyon-1": {"bundle_sha256": "invalid"}}})
    )
    with pytest.raises(seed.SeedError):
        seed.media_file(seed.CLIPS[0], tmp_path)


def test_missing_bundle_stops_before_any_account_or_video_mutation(tmp_path):
    with patch.object(seed, "media_file", side_effect=seed.SeedError("Missing bundle")):
        with patch.object(seed, "SeedAPI") as api:
            with pytest.raises(seed.SeedError):
                seed.run_seed(tmp_path)
    api.assert_not_called()


def test_production_cookies_are_only_allowed_on_the_fixed_internal_host():
    cookie = Mock()
    cookie.secure = True
    policy = seed.InternalCookies()
    assert policy.return_ok_secure(cookie, Request("http://nginx:8080/api/v1/auth/me"))
    assert not policy.return_ok_secure(cookie, Request("http://example.com/auth/me"))
    assert not policy.return_ok_secure(cookie, Request("http://nginx.example.com/auth/me"))
