"""Real seed account creation and role recovery, without repeating media processing."""

import hashlib
import os
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy import text

from app import seed
from user.public import GetRole, SetRole

pytestmark = pytest.mark.usefixtures("isolated_account_database")


def state_path(root):
    namespace = hashlib.sha256(os.environ["POSTGRES_DB"].encode()).hexdigest()[:16]
    return root / f"state-{namespace}.json"


def run_accounts(client, csrf, root):
    class LocalAPI:
        def call(self, method, path, body=None, *, allowed=()):
            response = client.request(method, "/v1" + path, json=body, headers=csrf)
            if response.status_code in allowed:
                return {"_status": response.status_code}
            if response.status_code >= 400:
                raise seed.SeedError(f"Test API returned HTTP {response.status_code}")
            return response.json() if response.content else {}

    with patch.object(seed, "CLIPS", ()), patch.object(seed, "SeedAPI", LocalAPI):
        seed.run_seed(root)


def role(client, email, value=None):
    async def operation():
        async with client.app.state.dishka_container() as scope:
            if value is not None:
                await (await scope.get(SetRole))(email, value)
            return await (await scope.get(GetRole))(email)

    return client.portal.call(operation).value


def test_seed_creates_admin_and_repeats_without_changing_authors(client, csrf, tmp_path):
    run_accounts(client, csrf, tmp_path)
    first = seed.read_state(state_path(tmp_path))
    assert len(first.accounts) == 5 and seed.ADMIN.key in first.accounts
    for person in seed.PEOPLE:
        assert role(client, person.email) == ("admin" if person == seed.ADMIN else "user")
    assert role(client, seed.PEOPLE[0].email, "moderator") == "moderator"
    assert role(client, seed.ADMIN.email, "user") == "user"
    run_accounts(client, csrf, tmp_path)
    assert seed.read_state(state_path(tmp_path)) == first
    assert role(client, seed.ADMIN.email) == "admin"
    assert role(client, seed.PEOPLE[0].email) == "moderator"

    async def account_count():
        async with client.app.state.resources.sessions() as session:
            return await session.scalar(text("SELECT count(*) FROM user_accounts"))

    assert client.portal.call(account_count) == 5
    response = client.post(
        "/v1/auth/login",
        json={"email": seed.ADMIN.email, "password": seed.DEMO_PASSWORD},
        headers=csrf,
    )
    assert response.status_code == 200 and response.json()["role"] == "admin"
    assert client.get("/v1/admin/job-errors").status_code == 200


@pytest.mark.parametrize("recorded", [False, True])
def test_existing_unverified_admin_account_is_never_promoted(client, csrf, tmp_path, recorded):
    person = seed.Person("admin_" + uuid4().hex[:8], "Existing ordinary account")
    response = client.post(
        "/v1/auth/register",
        json={
            "name": person.name,
            "tag": person.tag,
            "email": person.email,
            "password": seed.DEMO_PASSWORD,
            "password_confirmation": seed.DEMO_PASSWORD,
        },
        headers=csrf,
    )
    assert response.status_code == 201
    if recorded:
        seed.save_state(
            state_path(tmp_path),
            seed.State(
                accounts={
                    person.key: (
                        response.json()["account_id"] + 1000,
                        response.json()["channel_id"],
                    )
                }
            ),
        )
    with patch.object(seed, "ADMIN", person), patch.object(seed, "PEOPLE", (person,)):
        with pytest.raises(seed.SeedError, match="conflicts|already in use"):
            run_accounts(client, csrf, tmp_path)
    assert role(client, person.email) == "user"
