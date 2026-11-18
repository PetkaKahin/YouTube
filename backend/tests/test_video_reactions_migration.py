"""R21 migration preserves existing likes and dates; downgrade cannot turn dislikes into likes."""

import os
import subprocess
import sys
from uuid import uuid4

import pytest
from psycopg import Connection, sql
from psycopg.errors import CheckViolation, UniqueViolation

from app.resources import database_url
from app.secrets import read_secret
from app.settings import Settings


def test_existing_likes_and_reaction_constraint_upgrade_downgrade():
    database = "reaction_migration_" + uuid4().hex
    admin = database_url(Settings(), read_secret("POSTGRES_PASSWORD")).set(drivername="postgresql")
    with Connection.connect(
        admin.render_as_string(hide_password=False), autocommit=True
    ) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
    target = admin.set(database=database)
    env = {**os.environ, "POSTGRES_DB": database}

    def migrate(direction, revision):
        subprocess.run(  # noqa: S603 -- fixed interpreter/module and literal test commands
            [sys.executable, "-m", "alembic", direction, revision],
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )

    try:
        migrate("upgrade", "0027")
        with Connection.connect(
            target.render_as_string(hide_password=False), autocommit=True
        ) as connection:
            actor = connection.execute(
                "INSERT INTO user_accounts (name,email,password_hash) "
                "VALUES ('Legacy author','reactions@example.com','fixture') RETURNING id"
            ).fetchone()[0]
            videos = connection.execute(
                "INSERT INTO video_publications (owner_id,title,visibility) "
                "SELECT %s,'Reaction video','public' FROM generate_series(1,2) RETURNING id",
                (actor,),
            ).fetchall()
            connection.execute(
                "INSERT INTO video_likes (video_id,account_id,created_at) "
                "VALUES (%s,%s,'2026-01-01'::timestamptz),(%s,%s,'2026-01-02'::timestamptz)",
                (videos[0][0], actor, videos[1][0], actor),
            )
            original = connection.execute(
                "SELECT video_id,account_id,created_at FROM video_likes ORDER BY video_id"
            ).fetchall()
        migrate("upgrade", "0028")
        with Connection.connect(
            target.render_as_string(hide_password=False), autocommit=True
        ) as connection:
            rows = connection.execute(
                "SELECT video_id,account_id,created_at,reaction FROM video_likes ORDER BY video_id"
            ).fetchall()
            assert [row[:3] for row in rows] == original
            assert all(row[3] == "like" for row in rows)
            with pytest.raises(CheckViolation):
                with connection.transaction():
                    connection.execute(
                        "UPDATE video_likes SET reaction='both' WHERE video_id=%s", (videos[0][0],)
                    )
            with pytest.raises(UniqueViolation):
                with connection.transaction():
                    connection.execute(
                        "INSERT INTO video_likes (video_id,account_id,reaction) "
                        "VALUES (%s,%s,'dislike')",
                        (videos[0][0], actor),
                    )
            connection.execute(
                "UPDATE video_likes SET reaction='dislike' WHERE video_id=%s", (videos[1][0],)
            )
        migrate("downgrade", "0027")
        with Connection.connect(
            target.render_as_string(hide_password=False), autocommit=True
        ) as connection:
            assert connection.execute(
                "SELECT video_id,account_id,created_at FROM video_likes ORDER BY video_id"
            ).fetchall() == [original[0]]
        migrate("upgrade", "0028")
        with Connection.connect(
            target.render_as_string(hide_password=False), autocommit=True
        ) as connection:
            assert connection.execute("SELECT reaction FROM video_likes").fetchall() == [("like",)]
    finally:
        with Connection.connect(
            admin.render_as_string(hide_password=False), autocommit=True
        ) as connection:
            connection.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database))
            )
