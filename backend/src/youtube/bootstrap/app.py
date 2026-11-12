"""ASGI entry point. Business modules are added separately."""

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from youtube.bootstrap.secrets import ApplicationSecrets


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncGenerator[None]:
    application.state.secrets = ApplicationSecrets.load()
    try:
        yield
    finally:
        application.state.secrets = None


app = FastAPI(
    title=os.environ["APP_NAME"],
    version="0.0.0",
    root_path=os.environ["API_ROOT_PATH"],
    lifespan=lifespan,
)
