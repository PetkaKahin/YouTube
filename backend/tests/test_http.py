"""Behavior shared by every future module's HTTP contract."""

import json
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

PAYLOAD = {"count": 2, "enabled": True, "text": "hello", "nested": {"count": 3}}


def test_strict_json_discards_unknown_fields(client: TestClient, csrf: dict[str, str]):
    response = client.post(
        "/v1/_test/echo",
        headers=csrf,
        json={**PAYLOAD, "role": "admin", "nested": {"count": 3, "unknown": "discard"}},
    )
    assert response.status_code == 200
    assert response.json() == PAYLOAD


@pytest.mark.parametrize(
    "field,value",
    [
        ("count", "2"),
        ("count", True),
        ("count", 2.0),
        ("enabled", 1),
        ("enabled", "true"),
        ("text", 123),
        ("text", None),
        ("nested", {"count": "3"}),
    ],
)
def test_json_does_not_coerce_types(client, csrf, field, value):
    response = client.post("/v1/_test/echo", headers=csrf, json={**PAYLOAD, field: value})
    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["code"] == "validation_error"
    assert body["errors"][0]["location"][:2] == ["body", field]
    assert "input" not in body["errors"][0]


def test_malformed_json_is_problem_without_echo(client, csrf):
    response = client.post(
        "/v1/_test/echo",
        headers={**csrf, "Content-Type": "application/json"},
        content='{"password":"sensitive-input-marker",',
    )
    assert response.status_code == 422
    assert "sensitive-input-marker" not in response.text
    assert response.json()["errors"][0]["code"] == "json_invalid"


@pytest.mark.parametrize(
    "path,status,code",
    [
        ("/v1/missing", 404, "not_found"),
        ("/v1/_test/forbidden", 403, "forbidden"),
        ("/v1/_test/error", 500, "internal_error"),
    ],
)
def test_errors_have_one_safe_shape(client, path, status, code):
    response = client.get(path, headers={"X-Request-ID": "untrusted-client-value"})
    assert response.status_code == status
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["type"] == "about:blank"
    assert body["code"] == code
    assert body["status"] == status
    assert body["request_id"] == response.headers["x-request-id"]
    assert UUID(body["request_id"]).version == 4
    assert "sensitive-exception-marker" not in response.text


def test_method_error_preserves_allow_header(client):
    response = client.put("/v1/health/ready", headers={"X-CSRFToken": client.cookies["csrftoken"]})
    assert response.status_code == 405
    assert response.headers["allow"] == "GET"
    assert response.json()["code"] == "method_not_allowed"


def test_csrf_missing_wrong_and_unsigned_tokens_are_rejected(client):
    for headers in ({}, {"X-CSRFToken": "bad-token"}):
        response = client.post("/v1/_test/echo", json=PAYLOAD, headers=headers)
        assert response.status_code == 403
        assert response.json()["code"] == "csrf_failed"
    client.cookies.set("csrftoken", "unsigned-token")
    response = client.post(
        "/v1/_test/echo", json=PAYLOAD, headers={"X-CSRFToken": "unsigned-token"}
    )
    assert response.status_code == 403


def test_csrf_success_and_streaming(client, csrf):
    assert client.post("/v1/_test/echo", json=PAYLOAD, headers=csrf).status_code == 200
    response = client.get("/v1/_test/stream")
    assert response.content == b"firstsecond"
    assert response.headers["x-request-id"]


def test_completion_log_has_no_raw_path_query_or_input(client, caplog):
    caplog.set_level("INFO", logger="app.requests")
    response = client.get("/sensitive-path-marker?password=sensitive-query-marker")
    records = [item for item in caplog.records if item.name == "app.requests"]
    event = json.loads(records[-1].message)
    assert event["request_id"] == response.headers["x-request-id"]
    assert event["status"] == 404
    assert event["route"] == "<unmatched>"
    assert event["duration_ms"] >= 0
    assert "sensitive-" not in records[-1].message


def test_swagger_and_openapi_references(client):
    response = client.get("/docs")
    assert response.status_code == 200
    assert "SwaggerUIBundle" in response.text
    assert "/api/openapi.json" in response.text
    schema = client.get("/openapi.json").json()
    assert {"url": "/api"} in schema["servers"]
    ready = schema["paths"]["/v1/health/ready"]["get"]
    assert ready["responses"]["503"]["content"]["application/problem+json"]["schema"] == {
        "$ref": "#/components/schemas/Problem",
    }
    assert "FieldError" in schema["components"]["schemas"]
    assert schema["components"]["schemas"]["Problem"]["properties"]["errors"]["items"]["$ref"] == (
        "#/components/schemas/FieldError"
    )
    assert schema["paths"]["/v1/_test/echo"]["post"]["security"] == [{"CSRFToken": []}]


def test_real_dependency_readiness(client):
    response = client.get("/v1/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
