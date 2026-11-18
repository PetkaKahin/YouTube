"""Demo interactions through real HTTP, including interrupted and repeated seeds."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import text

from app import seed
from app.seed_activity import page_items, public_durations
from tests.test_library_http import clones
from tests.test_prepared_seed_import import private_bucket as private_bucket
from tests.test_seed_admin import state_path
from tests.test_watch_progress_http import sql

pytestmark = pytest.mark.usefixtures("isolated_account_database")


def snapshot(client):
    async def read():
        async with client.app.state.resources.sessions() as session:
            return tuple(
                [
                    await session.scalar(text(query))
                    for query in (
                        "SELECT jsonb_agg(to_jsonb(t) ORDER BY account_id,video_id) "
                        "FROM video_watch_progress t",
                        "SELECT jsonb_agg(to_jsonb(t) ORDER BY account_id,video_id) "
                        "FROM video_likes t",
                        "SELECT jsonb_agg(to_jsonb(t) ORDER BY subscriber_id,channel_id) "
                        "FROM channel_subscriptions t",
                        "SELECT jsonb_agg(to_jsonb(t) ORDER BY id) FROM channel_playlists t",
                        "SELECT jsonb_agg(to_jsonb(t) ORDER BY id) FROM channel_playlist_entries t",
                        "SELECT jsonb_agg(to_jsonb(t) ORDER BY id) FROM comment_entries t",
                    )
                ]
            )

    return client.portal.call(read)


def test_seed_activity_recovery_pages_and_preservation(
    client, csrf, tmp_path, private_bucket, monkeypatch
):
    monkeypatch.setenv("RUSTFS_BUCKET", private_bucket.bucket)
    calls = []
    lost_path = ["/comments"]

    class LocalAPI:
        def call(self, method, path, body=None, *, allowed=()):
            calls.append((method, path))
            response = client.request(method, "/v1" + path, json=body, headers=csrf)
            if response.status_code in allowed:
                return {"_status": response.status_code}
            if response.status_code >= 400:
                raise seed.SeedError(f"{method} {path}: {response.status_code} {response.text}")
            result = response.json() if response.content else {}
            if method == "POST" and lost_path and path.endswith(lost_path[0]):
                lost_path.clear()
                raise seed.SeedError("Lost response after committed POST")
            return result

    def populate():
        # Use the bundled ready media; this profile never starts a media worker.
        with (
            patch.object(seed, "SeedAPI", LocalAPI),
            patch("asyncio.create_subprocess_exec", side_effect=AssertionError("Media process")),
        ):
            seed.run_seed(tmp_path)

    with pytest.raises(seed.SeedError, match="Lost response"):
        populate()
    assert sql(client, "SELECT count(*) FROM comment_entries") == 1
    manifest = seed.read_state(state_path(tmp_path))
    assert len(manifest.accounts) == 5 and len(manifest.videos) == 12
    lost_path.append("/playlists")
    with pytest.raises(seed.SeedError, match="Lost response"):
        populate()
    assert sql(client, "SELECT count(*) FROM channel_playlists") == 1
    assert sql(client, "SELECT count(*) FROM comment_entries") == 3
    populate()
    assert seed.read_state(state_path(tmp_path)) == manifest
    assert [len(rows) for rows in snapshot(client)] == [20, 25, 12, 15, 45, 15]
    assert (
        sql(client, "SELECT count(*) FROM procrastinate_jobs WHERE status IN ('todo','doing')") == 0
    )

    for index, person in enumerate(seed.PEOPLE):
        api = LocalAPI()
        seed.login_person(api, person, manifest, state_path(tmp_path))
        history = api.call("GET", "/videos/history")
        assert len(history["items"]) == 4 and history["next_cursor"] is None
        assert all(
            0 < item["watch_progress"]["position_seconds"] < item["duration_seconds"]
            for item in history["items"]
        )
        feed = api.call("GET", "/videos/feed")["items"]
        assert sum(item["watch_progress"] is None for item in feed) == 8
        assert len(api.call("GET", "/videos/liked")["items"]) == 5
        subscribed = api.call("GET", "/channels/subscriptions")["items"]
        assert len(subscribed) == 2 + index % 2
        assert person.tag not in {channel["tag"] for channel in subscribed}
        assert api.call("GET", "/videos/subscriptions")["items"]
        playlists = api.call("GET", "/playlists/mine")["items"]
        assert {row["visibility"] for row in playlists} == {"public", "unlisted", "private"}
        for playlist in playlists:
            contents = api.call("GET", f"/playlists/{playlist['id']}")
            assert len(contents["items"]) == 3
            assert len({row["channel"]["id"] for row in contents["items"]}) == 2
        api.call("POST", "/auth/logout")

    alice = seed.PEOPLE[0]
    api = LocalAPI()
    seed.login_person(api, alice, manifest, state_path(tmp_path))
    video_id = manifest.videos[seed.CLIPS[3].key]
    api.call("PUT", f"/videos/{video_id}/progress", {"position_seconds": 0})
    api.call("PUT", f"/videos/{video_id}/dislike", {"disliked": True})
    public = next(
        row for row in api.call("GET", "/playlists/mine")["items"] if row["visibility"] == "public"
    )
    own_video = manifest.videos[seed.CLIPS[0].key]
    api.call("PUT", f"/playlists/{public['id']}/videos/{own_video}", {"selected": True})
    for index in range(21):
        api.call("POST", f"/videos/{video_id}/comments", {"text": f"Ручной комментарий {index}"})
        api.call(
            "POST",
            "/playlists",
            {
                "title": f"Ручная подборка {index}",
                "description": "Не из сидера",
                "visibility": "private",
            },
        )
    settings = api.call("GET", f"/videos/{own_video}/settings")
    api.call(
        "PUT",
        f"/videos/{own_video}/settings",
        {
            "title": settings["title"],
            "description": settings["description"],
            "visibility": "private",
        },
    )
    foreign_ids = clones(
        client,
        SimpleNamespace(videos=[video_id]),
        manifest.accounts[alice.key][0],
        21,
        date=datetime.now(UTC) + timedelta(days=1),
        title="Публикация вне seed-манифеста",
    )
    api.call("POST", "/auth/logout")
    before = snapshot(client)
    calls.clear()
    populate()
    assert snapshot(client) == before
    assert seed.read_state(state_path(tmp_path)) == manifest
    assert any(path.startswith("/videos/feed?cursor=") for _, path in calls)
    assert any(path.startswith("/playlists/mine?cursor=") for _, path in calls)
    assert any(path.startswith(f"/videos/{video_id}/comments?cursor=") for _, path in calls)
    assert not any(
        method != "GET" and path.startswith(f"/videos/{own_video}/") for method, path in calls
    )
    assert all(row["video_id"] not in foreign_ids for row in snapshot(client)[0])
    assert (
        sql(client, "SELECT visibility FROM video_publications WHERE id=:id", {"id": own_video})
        == "private"
    )


def test_cursor_is_encoded_and_missing_discussions_are_allowed():
    api = Mock()
    api.call.side_effect = [
        {"items": [{"id": 2**63 - 1}], "next_cursor": "x/y+=&cursor=bad"},
        {"items": [], "next_cursor": None},
    ]
    assert list(page_items(api, "/playlists/mine")) == [{"id": 2**63 - 1}]
    query = urlsplit(api.call.call_args.args[1]).query
    assert parse_qs(query) == {"cursor": ["x/y+=&cursor=bad"]}
    api.call.return_value = {"_status": 404}
    api.call.side_effect = None
    assert list(page_items(api, "/videos/1/comments", allow_missing=True)) == []


def test_public_durations_reads_only_requested_seed_ids_and_stops():
    api = Mock()
    api.call.side_effect = [
        {"items": [{"id": 1, "duration_seconds": 200}], "next_cursor": "next"},
        {"items": [{"id": 2**63 - 1, "duration_seconds": 120.5}], "next_cursor": "unused"},
    ]
    assert public_durations(api, (2**63 - 1,)) == {2**63 - 1: 120.5}
    assert api.call.call_count == 2
