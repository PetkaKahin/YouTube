"""Application composition and the shared HTTP/session lifecycle."""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import timedelta
from functools import partial

from dishka.integrations.fastapi import FastapiProvider, setup_dishka
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from pwdlib import PasswordHash
from starlette.exceptions import HTTPException
from starsessions import SessionAutoloadMiddleware

from app.di import create_container
from app.http.routing import api_router
from app.resources import Resources, create_cache
from app.secrets import read_secret
from app.settings import Settings
from common.contracts.authorization import Authorization
from common.http.errors import http_error, validation_error
from common.http.middleware import ProblemCSRFMiddleware, RequestLifecycleMiddleware
from common.http.openapi import configure_openapi
from common.http.sessions import GuardedSessionMiddleware
from common.infrastructure.session_store import GuardedRedisStore

SESSION_LIFETIME = timedelta(days=30)


def create_app(settings: Settings | None = None) -> FastAPI:
    configuration = settings or Settings()
    cache = create_cache(configuration, read_secret("VALKEY_PASSWORD"))
    csrf_secret = read_secret("CSRF_SECRET")
    container = create_container(configuration, cache, FastapiProvider())

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncGenerator[None]:
        try:
            application.state.resources = await container.get(Resources)
            application.state.authorization = await container.get(Authorization)
            application.state.password_hash = await container.get(PasswordHash)
            yield
        finally:
            try:
                await container.close()
            finally:
                application.state.resources = None
                application.state.authorization = None
                application.state.password_hash = None

    application = FastAPI(
        title=configuration.app_name,
        version="0.0.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url=None,
        swagger_ui_parameters={"persistAuthorization": False},
    )
    application.state.resources = None
    application.state.authorization = None
    application.state.password_hash = None
    setup_dishka(container=container, app=application)
    application.include_router(api_router())
    application.add_exception_handler(HTTPException, http_error)
    application.add_exception_handler(RequestValidationError, validation_error)
    application.add_exception_handler(Exception, http_error)
    application.add_middleware(SessionAutoloadMiddleware)
    application.add_middleware(
        GuardedSessionMiddleware,
        store_factory=partial(
            GuardedRedisStore, cache, lifetime=int(SESSION_LIFETIME.total_seconds())
        ),
        lifetime=SESSION_LIFETIME,
        cookie_name="session",
        cookie_https_only=configuration.cookie_secure,
    )
    application.add_middleware(
        ProblemCSRFMiddleware,
        secret=csrf_secret,
        cookie_name="csrftoken",
        cookie_path="/",
        cookie_secure=configuration.cookie_secure,
        cookie_httponly=False,
        cookie_samesite="lax",
        header_name="x-csrftoken",
    )
    application.add_middleware(RequestLifecycleMiddleware)
    configure_openapi(application)
    return application


logging.basicConfig(level=logging.INFO, format="%(message)s")
app = create_app()
