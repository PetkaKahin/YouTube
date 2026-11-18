"""R21 state transitions, ordinary access, counter atomicity and liked chronology."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import SQLAlchemyError

from tests import test_watch_progress_http as support
from tests.test_library_http import clones
from tests.test_watch_progress_http import check, login, sql
from user.public import SetRole

pytestmark = pytest.mark.usefixtures("isolated_account_database")
FIELDS = {"liked", "disliked", "can_like", "can_dislike", "like_count"}


@pytest.fixture(scope="module")
def media(backend_runtime):
    yield from support.media.__wrapped__(backend_runtime)


@pytest.fixture(autouse=True)
def reset(media, client):
    support.reset.__wrapped__(media, client)
    sql(client, "DELETE FROM video_likes")


@pytest.fixture
def viewer(client, csrf, media):
    login(client, csrf, media.people[1])
    return media.people[1]


def command(client, csrf, video, kind, selected):
    field = "liked" if kind == "like" else "disliked"
    return client.put(f"/v1/videos/{video}/{kind}", json={field: selected}, headers=csrf)


def state(client, video):
    response = client.get(f"/v1/videos/{video}/like")
    check(response)
    value = response.json()
    assert set(value) == FIELDS and not (value["liked"] and value["disliked"])
    return value


@pytest.mark.parametrize("initial", [None, "like", "dislike"])
@pytest.mark.parametrize(
    "kind,selected", [("like", True), ("like", False), ("dislike", True), ("dislike", False)]
)
def test_all_transitions_idempotence_and_opposite_false(
    client, csrf, media, viewer, initial, kind, selected
):
    video = media.videos[0]
    if initial is not None:
        check(command(client, csrf, video, initial, True))
    response = command(client, csrf, video, kind, selected)
    check(response)
    expected = kind if selected else None if initial == kind else initial
    assert response.json() == {
        "liked": expected == "like",
        "disliked": expected == "dislike",
        "can_like": True,
        "can_dislike": True,
        "like_count": int(expected == "like"),
    }
    pair = sql(
        client,
        "SELECT to_jsonb(l) FROM video_likes l WHERE video_id=:video AND account_id=:actor",
        {"video": video, "actor": viewer.id},
    )
    assert (pair is None) == (expected is None)
    if pair is not None:
        assert pair["reaction"] == expected
    repeated = command(client, csrf, video, kind, selected)
    check(repeated)
    assert repeated.json() == response.json() == state(client, video)
    assert (
        sql(
            client,
            "SELECT to_jsonb(l) FROM video_likes l WHERE video_id=:video AND account_id=:actor",
            {"video": video, "actor": viewer.id},
        )
        == pair
    )


def test_accounts_publication_isolation_guest_and_no_read_side_effects(client, csrf, media, viewer):
    video, other_video = media.videos
    login(client, csrf, media.people[0])
    check(command(client, csrf, video, "like", True))
    login(client, csrf, viewer)
    check(command(client, csrf, video, "dislike", True))
    assert state(client, video) == {
        "liked": False,
        "disliked": True,
        "can_like": True,
        "can_dislike": True,
        "like_count": 1,
    }
    assert not state(client, other_video)["disliked"]
    assert state(client, other_video)["like_count"] == 0
    metadata = sql(client, "SELECT jsonb_agg(to_jsonb(p) ORDER BY id) FROM video_publications p")
    pairs = sql(
        client, "SELECT jsonb_agg(to_jsonb(l) ORDER BY video_id,account_id) FROM video_likes l"
    )
    for _ in range(2):
        state(client, video)
        check(client.get("/v1/videos/liked"))
    assert client.post("/v1/auth/logout", headers=csrf).status_code == 204
    assert state(client, video) == {
        "liked": False,
        "disliked": False,
        "can_like": False,
        "can_dislike": False,
        "like_count": 1,
    }
    assert (
        sql(client, "SELECT jsonb_agg(to_jsonb(l) ORDER BY video_id,account_id) FROM video_likes l")
        == pairs
    )
    assert (
        sql(client, "SELECT jsonb_agg(to_jsonb(p) ORDER BY id) FROM video_publications p")
        == metadata
    )
    assert sql(client, "SELECT count(*) FROM video_watch_progress") == 0


def test_auth_csrf_strict_body_ids_and_openapi(client, csrf, media, viewer):
    video = media.videos[0]
    assert client.post("/v1/auth/logout", headers=csrf).status_code == 204
    check(command(client, csrf, video, "dislike", True), 401)
    login(client, csrf, viewer)
    endpoint = f"/v1/videos/{video}/dislike"
    check(client.put(endpoint, json={"disliked": True}), 403)
    for body in (
        {},
        {"disliked": None},
        {"disliked": 1},
        {"disliked": "true"},
        {"disliked": "'; DROP TABLE video_likes;--"},
        {"disliked": []},
        {"disliked": True, "liked": True},
        {"disliked": True, "actor_id": viewer.id},
        {"disliked": True, "role": "admin"},
    ):
        response = client.put(endpoint, json=body, headers=csrf)
        check(response, 422)
        assert "DROP TABLE" not in response.text
    for identity in ("0", "-1", str(2**63), "1' OR TRUE--"):
        check(command(client, csrf, identity, "dislike", True), 422)
    assert sql(client, "SELECT count(*) FROM video_likes") == 0
    schema = client.get("/openapi.json").json()
    definitions = schema["components"]["schemas"]
    model = definitions["LikeResponse"]
    assert set(model["properties"]) == FIELDS and set(model["required"]) == FIELDS
    assert model["properties"]["like_count"]["minimum"] == 0
    assert definitions["DislikeBody"]["additionalProperties"] is False
    for suffix, method in (("like", "get"), ("like", "put"), ("dislike", "put")):
        route = schema["paths"]["/v1/videos/{video_id}/" + suffix][method]
        assert route["responses"]["200"]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/LikeResponse"
        }
        if method == "put":
            assert route["security"] == [{"SessionCookie": [], "CSRFToken": []}]


def test_dislike_excluded_before_liked_limit_and_returning_like_has_new_date(
    client, csrf, media, viewer
):
    liked = clones(client, media, media.people[0].id, 21)
    disliked = clones(client, media, media.people[0].id, 23)
    sql(
        client,
        "INSERT INTO video_likes (video_id,account_id,created_at,reaction) "
        "SELECT id,:actor,'2026-01-01'::timestamptz,'like' FROM video_publications "
        "WHERE id=ANY(:ids)",
        {"actor": viewer.id, "ids": liked},
    )
    sql(
        client,
        "INSERT INTO video_likes (video_id,account_id,created_at,reaction) "
        "SELECT id,:actor,now(),'dislike' FROM video_publications WHERE id=ANY(:ids)",
        {"actor": viewer.id, "ids": disliked},
    )
    first = client.get("/v1/videos/liked")
    check(first)
    assert len(first.json()["items"]) == 20 and first.json()["next_cursor"]
    assert [item["id"] for item in first.json()["items"]] == sorted(liked, reverse=True)[:20]
    second = client.get("/v1/videos/liked", params={"cursor": first.json()["next_cursor"]})
    check(second)
    assert [item["id"] for item in second.json()["items"]] == [min(liked)]
    assert second.json()["next_cursor"] is None
    target = min(liked)
    timestamp = sql(
        client,
        "SELECT created_at FROM video_likes WHERE account_id=:actor AND video_id=:id",
        {"actor": viewer.id, "id": target},
    )
    check(command(client, csrf, target, "like", True))
    assert (
        sql(
            client,
            "SELECT created_at FROM video_likes WHERE account_id=:actor AND video_id=:id",
            {"actor": viewer.id, "id": target},
        )
        == timestamp
    )
    check(command(client, csrf, target, "dislike", True))
    assert target not in [item["id"] for item in client.get("/v1/videos/liked").json()["items"]]
    check(command(client, csrf, target, "like", True))
    assert (
        sql(
            client,
            "SELECT created_at FROM video_likes WHERE account_id=:actor AND video_id=:id",
            {"actor": viewer.id, "id": target},
        )
        > timestamp
    )
    assert client.get("/v1/videos/liked").json()["items"][0]["id"] == target


def test_private_owner_grants_revocation_and_role_cannot_bypass(client, csrf, media, viewer):
    video = media.videos[0]
    sql(client, "UPDATE video_publications SET visibility='private' WHERE id=:id", {"id": video})

    async def admin():
        async with client.app.state.dishka_container() as operation:
            await (await operation.get(SetRole))(viewer.email, "admin")

    client.portal.call(admin)
    check(command(client, csrf, video, "dislike", True), 404)
    login(client, csrf, media.people[0])
    check(command(client, csrf, video, "dislike", True))
    check(
        client.put(
            f"/v1/videos/{video}/permissions",
            json={"email": viewer.email, "allowed": True},
            headers=csrf,
        )
    )
    login(client, csrf, viewer)
    check(command(client, csrf, video, "dislike", True))
    assert state(client, video)["disliked"]
    login(client, csrf, media.people[0])
    check(
        client.put(
            f"/v1/videos/{video}/permissions",
            json={"email": viewer.email, "allowed": False},
            headers=csrf,
        )
    )
    login(client, csrf, viewer)
    check(command(client, csrf, video, "dislike", False), 404)
    response = client.get(f"/v1/videos/{video}/like")
    check(response, 404)
    assert not FIELDS & response.json().keys()
    assert (
        sql(
            client,
            "SELECT count(*) FROM video_likes WHERE video_id=:id AND reaction='dislike'",
            {"id": video},
        )
        == 2
    )
    sql(client, "UPDATE video_publications SET visibility='public' WHERE id=:id", {"id": video})
    assert state(client, video)["disliked"]


@pytest.mark.parametrize("state_name", ["hidden", "draft", "unready", "missing"])
def test_unavailable_target_no_reaction_or_counter_response(
    client, csrf, media, viewer, state_name
):
    video = media.videos[0]
    if state_name == "hidden":
        sql(client, "UPDATE video_publications SET moderation='hidden' WHERE id=:id", {"id": video})
    elif state_name == "draft":
        sql(client, "UPDATE video_publications SET publication='draft' WHERE id=:id", {"id": video})
    elif state_name == "unready":
        sql(client, "UPDATE video_media_plans SET status='pending'")
    else:
        video = 2**62
    for kind in ("like", "dislike"):
        response = command(client, csrf, video, kind, True)
        check(response, 404)
        assert not FIELDS & response.json().keys()
    assert sql(client, "SELECT count(*) FROM video_likes") == 0


@pytest.mark.parametrize(
    "initial,target", [(None, "dislike"), ("like", "dislike"), ("dislike", "like")]
)
def test_failed_aggregate_rolls_back_new_or_changed_reaction(
    client, csrf, media, viewer, initial, target
):
    video = media.videos[0]
    if initial:
        check(command(client, csrf, video, initial, True))
    pair = sql(
        client,
        "SELECT to_jsonb(l) FROM video_likes l WHERE account_id=:actor AND video_id=:video",
        {"actor": viewer.id, "video": video},
    )
    engine = client.app.state.resources.engine.sync_engine

    def fail(connection, cursor, statement, parameters, context, many):
        if statement.startswith("SELECT") and "FROM video_likes" in statement:
            raise SQLAlchemyError("private-reaction-counter-marker")

    event.listen(engine, "before_cursor_execute", fail)
    try:
        response = command(client, csrf, video, target, True)
    finally:
        event.remove(engine, "before_cursor_execute", fail)
    check(response, 500)
    assert not FIELDS & response.json().keys()
    assert "private-reaction-counter-marker" not in response.text
    assert (
        sql(
            client,
            "SELECT to_jsonb(l) FROM video_likes l WHERE account_id=:actor AND video_id=:video",
            {"actor": viewer.id, "video": video},
        )
        == pair
    )


def test_parallel_like_dislike_serialise_one_pair_and_exact_counter(client, csrf, media, viewer):
    video = media.videos[0]
    commands = [("like", True), ("dislike", True), ("like", False), ("dislike", False)] * 4
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda action: command(client, csrf, video, *action), commands))
    for response in responses:
        check(response)
        value = response.json()
        assert set(value) == FIELDS and not (value["liked"] and value["disliked"])
        assert value["like_count"] == int(value["liked"])
    value = state(client, video)
    active = sql(
        client,
        "SELECT reaction FROM video_likes WHERE account_id=:actor AND video_id=:video",
        {"actor": viewer.id, "video": video},
    )
    assert value["liked"] == (active == "like") and value["disliked"] == (active == "dislike")
    assert value["like_count"] == int(active == "like")
    assert (
        sql(
            client,
            "SELECT count(*) FROM video_likes WHERE account_id=:actor AND video_id=:video",
            {"actor": viewer.id, "video": video},
        )
        <= 1
    )


def test_access_revocation_winning_lock_blocks_dislike_change(client, csrf, media, viewer):
    video = media.videos[0]
    check(command(client, csrf, video, "like", True))

    async def lock():
        session = client.app.state.resources.sessions()
        await session.begin()
        await session.execute(
            text("UPDATE video_publications SET visibility='private' WHERE id=:id"), {"id": video}
        )
        return session

    session = client.portal.call(lock)
    waiting = Event()
    engine = client.app.state.resources.engine.sync_engine

    def capture(connection, cursor, statement, parameters, context, many):
        if "FOR UPDATE" in statement and "video_publications" in statement:
            waiting.set()

    event.listen(engine, "before_cursor_execute", capture)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(command, client, csrf, video, "dislike", True)
            try:
                assert waiting.wait(10)
            finally:
                client.portal.call(session.commit)
            response = pending.result(timeout=10)
            check(response, 404)
            assert not FIELDS & response.json().keys()
    finally:
        event.remove(engine, "before_cursor_execute", capture)
        client.portal.call(session.close)
    assert (
        sql(
            client,
            "SELECT reaction FROM video_likes WHERE account_id=:actor AND video_id=:video",
            {"actor": viewer.id, "video": video},
        )
        == "like"
    )
