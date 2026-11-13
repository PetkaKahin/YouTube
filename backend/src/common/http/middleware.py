"""ASGI middleware preserves streaming and records one sanitized completion event."""

import json
import logging
from time import perf_counter
from uuid import uuid4

from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from starlette_csrf.middleware import CSRFMiddleware

from common.http.errors import problem

logger = logging.getLogger("app.requests")


class ProblemCSRFMiddleware(CSRFMiddleware):
    def _get_error_response(self, request: Request):
        return problem(request, 403, "csrf_failed", "CSRF token verification failed")


class RequestLifecycleMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = perf_counter()
        request_id = uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        status = 500
        response_started = False

        async def send_response(message: Message) -> None:
            nonlocal status, response_started
            if message["type"] == "http.response.start":
                status = message["status"]
                response_started = True
                MutableHeaders(scope=message)["X-Request-ID"] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_response)
        except Exception:
            status = 500
            if response_started:
                raise RuntimeError("Response interrupted; see request completion event") from None
            await problem(Request(scope), 500, "internal_error", "An unexpected error occurred")(
                scope, receive, send_response
            )
        finally:
            route = getattr(scope.get("route"), "path", "<unmatched>")
            logger.info(
                json.dumps(
                    {
                        "request_id": request_id,
                        "method": scope["method"],
                        "route": route,
                        "status": status,
                        "duration_ms": round((perf_counter() - started) * 1000, 3),
                    }
                )
            )
