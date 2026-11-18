"""Real discussions, sessions, prepared media, SQL transactions and access races."""

import asyncio
import json
from unittest.mock import patch

import pytest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from comment.public import CreateComment, DiscussionNotFound, GetComments, SetCommentLike
from tests import test_feed_http as feed
from tests import test_video_access_http as access
from tests.test_channels_http import query_hook
from tests.test_video_likes_http import register
from tests.test_videos_http import create, login
from user.public import SetRole

pytestmark = pytest.mark.usefixtures("isolated_account_database")


@pytest.fixture(scope="module", autouse=True)
def clean_media(backend_runtime):
    yield from access.clean_media.__wrapped__(backend_runtime)


@pytest.fixture
def owner(client, csrf, registered_account):
    return access.owner.__wrapped__(client, csrf, registered_account)


@pytest.fixture
def published(client, csrf, owner, source_queue):
    return access.published.__wrapped__(client, csrf, owner, source_queue)


def post(client, csrf, base, value="Комментарий", **extra):
    response = client.post(base + "/comments", headers=csrf, json={"text": value, **extra})
    assert response.status_code == 201, response.text
    return response.json()


def visibility(client, csrf, base, desired):
    current = client.get(base + "/settings").json()
    response = client.put(
        base + "/settings",
        headers=csrf,
        json={**{key: current[key] for key in ("title", "description")}, "visibility": desired},
    )
    assert response.status_code == 200


def test_public_comments_authorship_text_counters_and_schema(
    client, csrf, owner, published, account_payload, caplog
):
    base, draft = published
    assert feed.expose(client, csrf, base).status_code == 200
    before = client.get(base + "/settings").json()
    peer, peer_account = register(client, csrf, account_payload)
    value = " \n<script>alert('x')</script>\n'; DROP TABLE comment_entries;--\t "
    first = post(
        client,
        csrf,
        base,
        value,
        author_id=peer_account["account_id"],
        account_id=peer_account["account_id"],
        video_id=2**62,
        like_count=100,
        created_at="2000-01-01",
    )
    assert first["text"] == value and first["author_name"] == owner[0]["name"]
    assert set(first) == {"id", "text", "author_name", "created_at", "liked", "like_count"}
    assert not first["liked"] and first["like_count"] == 0
    endpoint = base + f"/comments/{first['id']}/like"
    for desired, count in ((False, 0), (True, 1), (True, 1), (False, 0), (False, 0), (True, 1)):
        response = client.put(
            endpoint,
            headers=csrf,
            json={"liked": desired, "account_id": peer_account["account_id"], "like_count": 100},
        )
        assert response.status_code == 200 and response.json() == {
            "liked": desired,
            "like_count": count,
            "can_like": True,
        }
        assert client.get(endpoint).json() == response.json()
    assert login(client, csrf, peer).status_code == 200
    second = post(client, csrf, base, "🙂" * 2000)
    assert client.put(endpoint, headers=csrf, json={"liked": True}).json()["like_count"] == 2
    page = client.get(base + "/comments").json()
    assert [item["id"] for item in page["items"]] == [second["id"], first["id"]]
    assert page["can_comment"] and page["next_cursor"] is None
    assert client.post("/v1/auth/logout", headers=csrf).status_code == 204
    response = client.get(base + "/comments")
    page = response.json()
    assert not page["can_comment"] and not any(item["liked"] for item in page["items"])
    assert page["items"][1]["like_count"] == 2
    assert client.get(endpoint).json() == {"liked": False, "like_count": 2, "can_like": False}
    assert client.post(base + "/comments", headers=csrf, json={"text": "guest"}).status_code == 401
    assert client.put(endpoint, headers=csrf, json={"liked": False}).status_code == 401
    assert (
        response.headers["cache-control"] == "no-store"
        and "cookie" in response.headers["vary"].lower()
    )
    for marker in (
        owner[0]["email"],
        peer["email"],
        "author_id",
        "owner_id",
        "password",
        "role",
        "encoded/",
        "fingerprint",
    ):
        assert marker not in response.text
    assert value not in caplog.text
    assert login(client, csrf, owner[0]).status_code == 200
    assert client.get(base + "/settings").json() == before
    other = create(client, csrf, visibility="public")
    assert client.get(f"/v1/videos/{other['id']}/comments/{first['id']}/like").status_code == 404
    schema = client.get("/openapi.json").json()
    comments = schema["paths"]["/v1/videos/{video_id}/comments"]
    likes = schema["paths"]["/v1/videos/{video_id}/comments/{comment_id}/like"]
    for route, method in ((comments, "get"), (likes, "get")):
        assert route[method]["security"] == [{}, {"SessionCookie": []}]
    for route, method in ((comments, "post"), (likes, "put")):
        assert route[method]["security"] == [{"SessionCookie": [], "CSRFToken": []}]
    assert schema["components"]["schemas"]["CommentBody"]["properties"]["text"]["maxLength"] == 2000
    assert draft["id"] == int(base.rsplit("/", 1)[1])


@pytest.mark.parametrize(
    "value", ["", " \t\n\r", "\u2003\u00a0", "a" * 2001, None, 1, True, [], {}, "a\0b", "\ud800"]
)
def test_invalid_text_and_csrf_never_insert(client, csrf, owner, published, value):
    base, _ = published
    assert feed.expose(client, csrf, base).status_code == 200
    endpoint = base + "/comments"
    response = client.post(
        endpoint,
        headers={**csrf, "Content-Type": "application/json"},
        content=json.dumps({"text": value}).encode(),
    )
    assert response.status_code == 422
    assert response.headers["cache-control"] == "no-store"
    assert "cookie" in response.headers["vary"].lower()
    assert client.get(endpoint).json()["items"] == []


def test_csrf_strict_like_targets_and_invalid_identifiers(client, csrf, owner, published):
    base, _ = published
    assert feed.expose(client, csrf, base).status_code == 200
    assert client.post(base + "/comments", json={"text": "blocked"}).status_code == 403
    assert client.get(base + "/comments").json()["items"] == []
    item = post(client, csrf, base)
    path = base + f"/comments/{item['id']}/like"
    assert client.put(path, json={"liked": True}).status_code == 403
    for body in ({}, {"liked": None}, {"liked": 0}, {"liked": 1}, {"liked": "true"}, {"liked": []}):
        assert client.put(path, headers=csrf, json=body).status_code == 422
    for identity in ("0", "-1", str(2**63), "abc", "1;DROP TABLE comment_likes"):
        assert client.get(base + f"/comments/{identity}/like").status_code == 422
    assert client.get(path).json()["like_count"] == 0


@pytest.mark.parametrize("role", ["user", "moderator", "admin"])
def test_private_hidden_and_draft_close_all_discussion_paths_without_deleting(
    client, csrf, owner, published, account_payload, role
):
    base, draft = published
    assert feed.expose(client, csrf, base).status_code == 200
    item = post(client, csrf, base, "private-comment-marker")
    endpoint = base + "/comments"
    like = endpoint + f"/{item['id']}/like"
    assert client.put(like, headers=csrf, json={"liked": True}).status_code == 200
    viewer, _ = register(client, csrf, account_payload)

    async def promote():
        async with client.app.state.dishka_container() as operation:
            await (await operation.get(SetRole))(viewer["email"], role)

    client.portal.call(promote)
    visibility(client, csrf, base, "private")
    assert (
        client.put(
            base + "/permissions", headers=csrf, json={"email": viewer["email"], "allowed": True}
        ).status_code
        == 200
    )
    for credentials in (owner[0], viewer):
        assert login(client, csrf, credentials).status_code == 200
        assert client.get(base).status_code == 200
        for method, path, data in (
            ("GET", endpoint, None),
            ("POST", endpoint, {"text": "denied"}),
            ("GET", like, None),
            ("PUT", like, {"liked": False}),
        ):
            response = client.request(method, path, headers=csrf, json=data)
            assert (
                response.status_code == 404
                and "private-comment-marker" not in response.text
                and "like_count" not in response.text
            )
    assert login(client, csrf, owner[0]).status_code == 200
    visibility(client, csrf, base, "public")
    assert client.get(endpoint).json()["items"][0]["id"] == item["id"]
    assert client.get(like).json()["liked"]
    for column, state, previous in (
        ("moderation", "hidden", "visible"),
        ("publication", "draft", "published"),
    ):

        async def set_state(value, field=column):
            async with client.app.state.resources.sessions.begin() as session:
                statement = (
                    "UPDATE video_publications SET moderation=:state WHERE id=:id"
                    if field == "moderation"
                    else "UPDATE video_publications SET publication=:state WHERE id=:id"
                )
                await session.execute(
                    text(statement),
                    {"state": value, "id": draft["id"]},
                )

        client.portal.call(set_state, state)
        assert client.get(endpoint).status_code == 404
        assert client.post(endpoint, headers=csrf, json={"text": "denied"}).status_code == 404
        assert client.get(like).status_code == 404
        assert client.put(like, headers=csrf, json={"liked": False}).status_code == 404
        client.portal.call(set_state, previous)
    assert client.get(like).json()["like_count"] == 1


def test_full_set_sorts_cursor_pages_ties_and_like_changes(client, csrf, owner, published):
    base, draft = published
    assert feed.expose(client, csrf, base).status_code == 200
    items = [post(client, csrf, base, f"comment-{i}") for i in range(43)]

    async def tie_dates():
        async with client.app.state.resources.sessions.begin() as session:
            await session.execute(
                text(
                    "UPDATE comment_entries SET created_at='2026-01-02 03:04:05.123456+00' "
                    "WHERE video_id=:video"
                ),
                {"video": draft["id"]},
            )

    client.portal.call(tie_dates)
    first_like = base + f"/comments/{items[0]['id']}/like"
    assert client.put(first_like, headers=csrf, json={"liked": True}).status_code == 200
    for order in ("newest", "popular"):
        result, cursor, sizes = [], None, []
        while True:
            query = {"sort": order, **({"cursor": cursor} if cursor else {})}
            response = client.get(base + "/comments", params=query)
            assert response.status_code == 200
            page = response.json()
            sizes.append(len(page["items"]))
            result.extend(item["id"] for item in page["items"])
            cursor = page["next_cursor"]
            if cursor is None:
                break
        ids = [item["id"] for item in reversed(items)]
        assert result == (ids if order == "newest" else [items[0]["id"], *ids[:-1]])
        assert sizes == [20, 20, 3] and len(set(result)) == 43
    token = client.get(base + "/comments").json()["next_cursor"]
    assert (
        client.get(base + "/comments", params={"cursor": token}).json()
        == client.get(base + "/comments", params={"cursor": token}).json()
    )
    assert (
        client.get(base + "/comments", params={"sort": "popular", "cursor": token}).status_code
        == 422
    )
    assert client.put(first_like, headers=csrf, json={"liked": False}).status_code == 200
    popular = client.get(base + "/comments", params={"sort": "popular"}).json()["items"]
    assert popular[0]["id"] == items[-1]["id"]


@pytest.mark.parametrize(
    "token",
    ["", "x", "x" * 10000, "';DROP TABLE comment_entries;--", "../etc/passwd", "<script>", "\0"],
)
def test_bad_query_is_generic_and_never_changes_data(client, csrf, owner, published, token):
    base, _ = published
    assert feed.expose(client, csrf, base).status_code == 200
    response = client.get(base + "/comments", params={"cursor": token})
    assert response.status_code == 422 and response.json()["code"] == "invalid_cursor"
    assert token == "" or token not in response.text
    assert client.get(base + "/comments").json()["items"] == []


def test_query_binding_cross_video_and_duplicate_parameters(client, csrf, owner, published):
    base, _ = published
    assert feed.expose(client, csrf, base).status_code == 200
    for index in range(21):
        post(client, csrf, base, str(index))
    cursor = client.get(base + "/comments").json()["next_cursor"]
    for query in (
        [("sort", "newest"), ("sort", "popular")],
        [("cursor", cursor), ("cursor", cursor)],
        {"sort": "invalid"},
    ):
        assert client.get(base + "/comments", params=query).status_code == 422
    other = create(client, csrf, visibility="public")
    # Reusing a cursor on a missing/unready video still hides that object's discussion.
    assert (
        client.get(f"/v1/videos/{other['id']}/comments", params={"cursor": cursor}).status_code
        == 404
    )


def test_names_and_like_aggregate_failures_roll_back_without_leaks(client, csrf, owner, published):
    base, _ = published
    assert feed.expose(client, csrf, base).status_code == 200
    before = client.get(base + "/settings").json()

    def fail_names(_, __, statement, *args):
        if (
            statement.lstrip().upper().startswith("SELECT")
            and "FROM user_accounts" in statement
            and "user_accounts.name" in statement
            and "user_accounts.email" not in statement
        ):
            raise SQLAlchemyError("sensitive-name-query")

    with query_hook(client, fail_names):
        response = client.post(base + "/comments", headers=csrf, json={"text": "rollback"})
        assert response.status_code == 500 and "sensitive-name-query" not in response.text
    assert client.get(base + "/comments").json()["items"] == []
    item = post(client, csrf, base)
    path = base + f"/comments/{item['id']}/like"

    reads = 0

    def fail_after_write(_, __, statement, *args):
        nonlocal reads
        if (
            statement.lstrip().upper().startswith("SELECT")
            and "count(comment_likes.account_id)" in statement
        ):
            reads += 1
            if reads == 2:
                raise SQLAlchemyError("sensitive-like-query")

    for current in (False, True):
        client.put(path, headers=csrf, json={"liked": current})
        reads = 0
        with query_hook(client, fail_after_write):
            # Fail after the pair write, proving rollback of both target states.
            response = client.put(path, headers=csrf, json={"liked": not current})
            assert response.status_code == 500 and "sensitive-like-query" not in response.text
        assert client.get(path).json()["liked"] is current
    assert client.get(base + "/settings").json() == before


def test_parallel_likes_and_persistent_data_when_valkey_is_unavailable(
    client, csrf, owner, published
):
    base, draft = published
    assert feed.expose(client, csrf, base).status_code == 200
    item = post(client, csrf, base)

    async def exercise():
        container = client.app.state.dishka_container

        async def invoke(scenario_type, *args):
            async with container() as operation:
                return await (await operation.get(scenario_type))(*args)

        actor = owner[1]["account_id"]
        repeated = await asyncio.gather(
            *[invoke(SetCommentLike, actor, draft["id"], item["id"], True) for _ in range(8)]
        )
        assert all(value.liked and value.like_count == 1 for value in repeated)
        removed = await asyncio.gather(
            *[invoke(SetCommentLike, actor, draft["id"], item["id"], False) for _ in range(8)]
        )
        assert all(not value.liked and value.like_count == 0 for value in removed)
        with patch(
            "redis.asyncio.Redis.execute_command", side_effect=ConnectionError("synthetic outage")
        ):
            assert (await invoke(GetComments, None, draft["id"])).items[0].id == item["id"]
            assert (
                await invoke(SetCommentLike, actor, draft["id"], item["id"], True)
            ).like_count == 1

    client.portal.call(exercise)
    assert client.get(base + f"/comments/{item['id']}/like").json()["like_count"] == 1


@pytest.mark.parametrize("scenario", ["read", "create", "like"])
@pytest.mark.parametrize("change", ["visibility", "hidden"])
@pytest.mark.parametrize("commit", [False, True])
def test_pending_operations_recheck_after_video_lock(
    client, csrf, owner, published, scenario, change, commit
):
    base, draft = published
    assert feed.expose(client, csrf, base).status_code == 200
    item = post(client, csrf, base)

    async def exercise():
        resources, container = client.app.state.resources, client.app.state.dishka_container

        async def pending_operation():
            async with container() as operation:
                args = (owner[1]["account_id"], draft["id"])
                if scenario == "read":
                    return await (await operation.get(GetComments))(*args)
                if scenario == "create":
                    return await (await operation.get(CreateComment))(*args, "pending")
                return await (await operation.get(SetCommentLike))(*args, item["id"], True)

        async with resources.sessions() as blocker:
            await blocker.begin()
            pid = await blocker.scalar(text("SELECT pg_backend_pid()"))
            await blocker.execute(
                text("SELECT id FROM video_publications WHERE id=:id FOR UPDATE"),
                {"id": draft["id"]},
            )
            sql = (
                "UPDATE video_publications SET visibility='private' WHERE id=:id"
                if change == "visibility"
                else "UPDATE video_publications SET moderation='hidden' WHERE id=:id"
            )
            await blocker.execute(text(sql), {"id": draft["id"]})
            pending = asyncio.create_task(pending_operation())
            async with asyncio.timeout(10), resources.engine.connect() as observer:
                await observer.execution_options(isolation_level="AUTOCOMMIT")
                while not await observer.scalar(
                    text(
                        "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() "
                        "AND :pid=ANY(pg_blocking_pids(pid))"
                    ),
                    {"pid": pid},
                ):
                    await asyncio.sleep(0.01)
            assert not pending.done()
            await blocker.commit() if commit else await blocker.rollback()
            if commit:
                with pytest.raises(DiscussionNotFound):
                    await pending
            else:
                await pending

    client.portal.call(exercise)
