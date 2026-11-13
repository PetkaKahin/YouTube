"""Integration fixtures run against this Compose project's real dependencies."""

from collections.abc import Iterator

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings
from common.http.schemas import JSONModel
from common.http.sessions import end_session, rotate_session


class Nested(JSONModel):
    count: int


class Payload(JSONModel):
    count: int
    enabled: bool
    text: str
    nested: Nested


@pytest.fixture
def application() -> FastAPI:
    app = create_app(Settings(cookie_secure=False))

    @app.post("/v1/_test/echo", response_model=Payload)
    async def echo(payload: Payload) -> Payload:
        return payload

    @app.get("/v1/_test/error")
    async def error():
        raise RuntimeError("sensitive-exception-marker")

    @app.get("/v1/_test/forbidden")
    async def forbidden():
        raise HTTPException(403, "sensitive-exception-marker")

    @app.get("/v1/_test/stream")
    async def stream():
        return StreamingResponse(iter([b"first", b"second"]), media_type="text/plain")

    @app.post("/v1/_test/session/{action}")
    async def session_action(action: str, request: Request):
        if action == "start":
            await rotate_session(request)
            request.session["user_id"] = "test-user"
        elif action == "rotate":
            await rotate_session(request)
        elif action == "end":
            await end_session(request)
        return {"user_id": request.session.get("user_id")}

    @app.get("/v1/_test/session")
    async def session(request: Request):
        return {"user_id": request.session.get("user_id")}

    return app


@pytest.fixture
def client(application: FastAPI) -> Iterator[TestClient]:
    with TestClient(application, root_path="/api") as connection:
        connection.get("/docs")
        yield connection


@pytest.fixture
def csrf(client: TestClient) -> dict[str, str]:
    return {"X-CSRFToken": client.cookies["csrftoken"]}
