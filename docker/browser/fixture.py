"""Serve the real API on an isolated database for browser checks; Ctrl-C cleans up."""

import asyncio
import json
import os
import subprocess
import sys
from functools import partial
from pathlib import Path
from uuid import uuid4

import uvicorn
from psycopg import AsyncConnection, sql

from app.main import create_app
from app.resources import create_cache, database_url
from app.secrets import read_secret
from app.settings import Settings
from common.http.sessions import GuardedSessionMiddleware


def main():
    pid_file = Path("/tmp/frontend-browser-fixture.pid")  # noqa: S108 - Container IPC, exclusive create.
    pid_written = False
    media = "--media" in sys.argv
    upload = "--upload" in sys.argv
    worker = None
    if media:
        Path("/work/watch-fixture.json").unlink(missing_ok=True)
    if pid_file.exists():
        raise RuntimeError("Stop the existing browser fixture before starting another")
    database = "frontend_test_" + uuid4().hex
    session_prefix = database + ".sessions."
    admin_url = database_url(Settings(), read_secret("POSTGRES_PASSWORD")).set(
        drivername="postgresql"
    )

    async def manage(create):
        async with await AsyncConnection.connect(
            admin_url.render_as_string(hide_password=False), autocommit=True
        ) as connection:
            command = "CREATE DATABASE {}" if create else "DROP DATABASE {} WITH (FORCE)"
            await connection.execute(sql.SQL(command).format(sql.Identifier(database)))

    asyncio.run(manage(True))

    async def clean_sessions():
        cache = create_cache(Settings(), read_secret("VALKEY_PASSWORD"))
        try:
            async for key in cache.scan_iter(match=session_prefix + "*"):
                await cache.delete(key)
        finally:
            await cache.aclose()

    try:
        os.environ["POSTGRES_DB"] = database
        os.environ["COOKIE_SECURE"] = "false"
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True)

        async def large_identifiers():
            url = admin_url.set(database=database).render_as_string(hide_password=False)
            async with await AsyncConnection.connect(url, autocommit=True) as connection:
                for table in (
                    "user_accounts",
                    "channel_profiles",
                    "channel_playlists",
                    "video_publications",
                ):
                    if table == "video_publications" and not upload:
                        continue
                    await connection.execute(
                        "SELECT setval(pg_get_serial_sequence(%s, 'id'), %s, false)",
                        (
                            table,
                            9007199254740993
                            if table in {"channel_playlists", "video_publications"}
                            else 9007199254740992,
                        ),
                    )

        asyncio.run(large_identifiers())
        if media:
            from watch_seed import seed

            manifest = asyncio.run(
                seed(
                    catalogue="--channel" in sys.argv,
                    links="--links" in sys.argv,
                    quality="--quality" in sys.argv,
                )
            )
            if "--moderation" in sys.argv:
                from moderation_seed import seed_moderation

                asyncio.run(seed_moderation(manifest))
            if "--jobs" in sys.argv:
                from jobs_seed import seed_jobs

                asyncio.run(seed_jobs(manifest))
            Path("/work/watch-fixture.json").write_text(json.dumps(manifest))
        application = create_app()
        if "--session" in sys.argv:

            async def session_fixture_control(action: str):
                if action not in ("lost", "expired"):
                    raise ValueError("Invalid fixture command")
                cache = create_cache(Settings(), read_secret("VALKEY_PASSWORD"))
                affected = 0
                try:
                    async for key in cache.scan_iter(match=session_prefix + "*"):
                        if action == "lost":
                            await cache.delete(key)
                        else:
                            await cache.expire(key, 1)
                        affected += 1
                    if action == "expired":
                        await asyncio.sleep(1.1)
                finally:
                    await cache.aclose()
                return {"done": True, "affected": affected}

            application.add_api_route(
                "/__session_fixture/{action}", session_fixture_control, methods=["POST"]
            )
        if "--moderation" in sys.argv:
            from moderation_seed import control

            async def fixture_control(action: str):
                return await control(manifest, action)

            application.add_api_route("/__fixture/{action}", fixture_control, methods=["POST"])
        if "--jobs" in sys.argv:
            from jobs_seed import control as jobs_control

            async def jobs_fixture_control(action: str):
                return await jobs_control(manifest, action)

            application.add_api_route(
                "/__jobs_fixture/{action}", jobs_fixture_control, methods=["POST"]
            )
        if upload:
            worker = subprocess.Popen([sys.executable, "-m", "app.media_worker"])
        for middleware in application.user_middleware:
            if middleware.cls is GuardedSessionMiddleware:
                middleware.kwargs["store_factory"] = partial(
                    middleware.kwargs["store_factory"], prefix=session_prefix
                )
                break
        else:
            raise RuntimeError("Session middleware was not found")
        with pid_file.open("x") as record:
            record.write(str(os.getpid()))
        pid_written = True
        print("Browser fixture: isolated database ready, API port 8181", flush=True)
        uvicorn.run(
            application,
            host="0.0.0.0",  # noqa: S104 - Compose network only; port 8181 is not published.
            port=8181,
            access_log=False,
            log_level="warning",
        )
    finally:
        if worker is not None:
            worker.terminate()
            worker.wait()
        try:
            try:
                if media or upload:
                    from watch_seed import cleanup

                    asyncio.run(cleanup())
            finally:
                asyncio.run(clean_sessions())
        finally:
            asyncio.run(manage(False))
        if pid_written:
            pid_file.unlink(missing_ok=True)
        if media:
            Path("/work/watch-fixture.json").unlink(missing_ok=True)
        print("Browser fixture: isolated sessions and database removed", flush=True)


if __name__ == "__main__":
    main()
