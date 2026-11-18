"""Frozen initial schema, database guarantees and a complete downgrade/upgrade cycle."""

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from psycopg import Connection
from psycopg.errors import CheckViolation, UniqueViolation

from app.orm import application_metadata
from app.resources import database_url
from app.secrets import read_secret
from app.settings import Settings

pytestmark = pytest.mark.usefixtures("isolated_account_database")


def target_url():
    return database_url(Settings(), read_secret("POSTGRES_PASSWORD"))


def schema_signature(connection):
    """Catalog-only comparison: no application rows, owners, OIDs or sequence values."""
    queries = {
        "columns": """
            SELECT c.relname,a.attname,format_type(a.atttypid,a.atttypmod),a.attnotnull,
                   a.attidentity,pg_get_expr(d.adbin,d.adrelid),a.attgenerated
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            JOIN pg_attribute a ON a.attrelid=c.oid
            LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum
            WHERE n.nspname='public' AND c.relkind IN ('r','p') AND a.attnum>0
              AND NOT a.attisdropped AND c.relname<>'alembic_version'
            ORDER BY c.relname,a.attname
        """,
        "constraints": """
            SELECT c.relname,k.conname,k.contype,pg_get_constraintdef(k.oid),k.convalidated
            FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND c.relname<>'alembic_version'
            ORDER BY c.relname,k.conname
        """,
        "indexes": """
            SELECT tablename,indexname,indexdef FROM pg_indexes
            WHERE schemaname='public' AND tablename<>'alembic_version'
            ORDER BY tablename,indexname
        """,
        "sequences": """
            SELECT c.relname,s.seqstart,s.seqincrement,s.seqmin,s.seqmax,s.seqcache,s.seqcycle
            FROM pg_sequence s JOIN pg_class c ON c.oid=s.seqrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' ORDER BY c.relname
        """,
        "functions": """
            SELECT p.proname,pg_get_functiondef(p.oid)
            FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname='public' AND p.prokind IN ('f','p')
            ORDER BY p.proname,pg_get_function_identity_arguments(p.oid)
        """,
    }
    # JSON normalises tuples for comparison with a saved pre-refactor signature.
    return json.loads(
        json.dumps({key: connection.execute(query).fetchall() for key, query in queries.items()})
    )


def test_each_table_has_one_static_initial_definition():
    versions = Path(__file__).resolve().parents[1] / "migrations/versions"
    tables = []
    for path in sorted(versions.glob("*.py")):
        tree = ast.parse(path.read_text())
        definitions = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if not isinstance(node.func.value, ast.Name) or node.func.value.id != "op":
                continue
            assert node.func.attr not in {"add_column", "alter_column", "drop_column"}
            if node.func.attr == "create_table":
                definitions.append(ast.literal_eval(node.args[0]))
        if path.name.endswith("_queue.py"):
            assert not definitions
        elif path.name.endswith("_video_encoding_and_thumbnails.py"):
            assert set(definitions) == {"video_encoded_tracks", "video_thumbnail_uploads"}
        else:
            assert len(definitions) == 1, path.name
        tables.extend(definitions)
    assert len(tables) == len(set(tables)) == 30
    assert set(tables) == set(application_metadata().tables)


def test_initial_schema_matches_runtime_models():
    engine = sa.create_engine(target_url())
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(
                connection,
                opts={
                    "compare_type": True,
                    "compare_server_default": True,
                    "include_object": lambda obj, name, kind, reflected, compared: (
                        not (kind == "table" and name.startswith("procrastinate_"))
                    ),
                },
            )
            assert compare_metadata(context, application_metadata()) == []
    finally:
        engine.dispose()


def test_new_likes_have_dates_and_one_valid_reaction():
    url = target_url().set(drivername="postgresql").render_as_string(hide_password=False)
    with Connection.connect(url, autocommit=True) as connection:
        actor = connection.execute(
            "INSERT INTO user_accounts (name,email,password_hash) "
            "VALUES ('Initial user','initial@example.com','fixture') RETURNING id"
        ).fetchone()[0]
        video = connection.execute(
            "INSERT INTO video_publications (owner_id,title,visibility) "
            "VALUES (%s,'Initial video','public') RETURNING id",
            (actor,),
        ).fetchone()[0]
        before = connection.execute("SELECT clock_timestamp()").fetchone()[0]
        created, reaction = connection.execute(
            "INSERT INTO video_likes (video_id,account_id) VALUES (%s,%s) "
            "RETURNING created_at,reaction",
            (video, actor),
        ).fetchone()
        after = connection.execute("SELECT clock_timestamp()").fetchone()[0]
        assert before <= created <= after and reaction == "like"
        with pytest.raises(CheckViolation):
            with connection.transaction():
                connection.execute(
                    "UPDATE video_likes SET reaction='both' WHERE video_id=%s", (video,)
                )
        with pytest.raises(UniqueViolation):
            with connection.transaction():
                connection.execute(
                    (
                        "INSERT INTO video_likes (video_id,account_id,reaction) VALUES (%"
                        "s,%s,'dislike')"
                    ),
                    (video, actor),
                )
        assert (
            connection.execute(
                "SELECT count(*) FROM pg_indexes WHERE indexname IN ('ix_video_li"
                "kes_account_order','ix_video_watch_progress_account_order')"
            ).fetchone()[0]
            == 2
        )


def test_upgrade_downgrade_upgrade_preserves_the_schema():
    url = target_url().set(drivername="postgresql").render_as_string(hide_password=False)
    with Connection.connect(url, autocommit=True) as connection:
        before = schema_signature(connection)
    subprocess.run(  # noqa: S603 -- fixed interpreter and migration command
        [sys.executable, "-m", "alembic", "downgrade", "base"],
        env=os.environ.copy(),
        check=True,
        capture_output=True,
    )
    with Connection.connect(url, autocommit=True) as connection:
        remaining = connection.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' AND ta"
            "blename<>'alembic_version'"
        ).fetchall()
        assert remaining and all(row[0].startswith("procrastinate_") for row in remaining)
    subprocess.run(  # noqa: S603 -- fixed interpreter and migration command
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env=os.environ.copy(),
        check=True,
        capture_output=True,
    )
    with Connection.connect(url, autocommit=True) as connection:
        assert schema_signature(connection) == before
