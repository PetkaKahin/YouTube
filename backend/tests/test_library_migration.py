"""Legacy pairs retain data and gain an explicitly synthetic migration timestamp."""

import os
import subprocess
import sys
from uuid import uuid4

from psycopg import Connection, sql

from app.resources import database_url
from app.secrets import read_secret
from app.settings import Settings


def test_existing_likes_backfill_and_new_pair_default():
    database = "library_migration_" + uuid4().hex
    admin = database_url(Settings(), read_secret("POSTGRES_PASSWORD")).set(drivername="postgresql")
    with Connection.connect(
        admin.render_as_string(hide_password=False), autocommit=True
    ) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
    target = admin.set(database=database)
    env = {**os.environ, "POSTGRES_DB": database}

    def upgrade(revision):
        subprocess.run(  # noqa: S603 -- fixed interpreter/module and literal test revisions
            [sys.executable, "-m", "alembic", "upgrade", revision],
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )

    try:
        upgrade("0026")
        with Connection.connect(
            target.render_as_string(hide_password=False), autocommit=True
        ) as connection:
            actor = connection.execute(
                "INSERT INTO user_accounts (name,email,password_hash) "
                "VALUES ('Legacy user','legacy@example.com','fixture') RETURNING id"
            ).fetchone()[0]
            videos = connection.execute(
                "INSERT INTO video_publications (owner_id,title,visibility) "
                "SELECT %s,'Legacy video','public' FROM generate_series(1,3) RETURNING id",
                (actor,),
            ).fetchall()
            connection.execute(
                "INSERT INTO video_likes (video_id,account_id) VALUES (%s,%s),(%s,%s)",
                (videos[0][0], actor, videos[1][0], actor),
            )
            metadata = connection.execute(
                "SELECT id,created_at,updated_at FROM video_publications ORDER BY id"
            ).fetchall()
            before = connection.execute("SELECT clock_timestamp()").fetchone()[0]
        upgrade("0027")
        with Connection.connect(
            target.render_as_string(hide_password=False), autocommit=True
        ) as connection:
            after = connection.execute("SELECT clock_timestamp()").fetchone()[0]
            legacy = connection.execute(
                "SELECT video_id,account_id,created_at FROM video_likes ORDER BY video_id"
            ).fetchall()
            assert len(legacy) == 2
            assert [row[:2] for row in legacy] == [(videos[0][0], actor), (videos[1][0], actor)]
            assert legacy[0][2] == legacy[1][2] and before <= legacy[0][2] <= after
            current = connection.execute(
                "INSERT INTO video_likes (video_id,account_id) VALUES (%s,%s) RETURNING created_at",
                (videos[2][0], actor),
            ).fetchone()[0]
            assert current >= after
            assert (
                connection.execute(
                    "SELECT count(*) FROM pg_indexes WHERE indexname IN "
                    "('ix_video_likes_account_order','ix_video_watch_progress_account_order')"
                ).fetchone()[0]
                == 2
            )
            assert (
                connection.execute(
                    "SELECT id,created_at,updated_at FROM video_publications ORDER BY id"
                ).fetchall()
                == metadata
            )
    finally:
        with Connection.connect(
            admin.render_as_string(hide_password=False), autocommit=True
        ) as connection:
            connection.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database))
            )
