"""Text boundaries and exact, scoped cursor validation without HTTP or storage."""

import base64
import struct
from datetime import UTC, datetime

import pytest

from comment.domain.comments import (
    CommentCursor,
    CommentSort,
    InvalidComment,
    InvalidCommentCursor,
    validate_text,
)

DATE = datetime(2026, 1, 2, 3, 4, 5, 123456, tzinfo=UTC)


@pytest.mark.parametrize(
    "value",
    [
        "а",
        "🙂" * 2000,
        "я" * 1999,
        "я" * 2000,
        " \n текст\t ",
        "<script>code</script>",
        "';DROP TABLE comment_entries;--",
    ],
)
def test_valid_text_preserves_exact_data(value):
    assert validate_text(value) == value


@pytest.mark.parametrize(
    "value",
    [
        None,
        False,
        1,
        [],
        {},
        "",
        " \t\r\n",
        "\u2003\u00a0",
        "а" * 2001,
        "a\0b",
        "\ud800",
        "a\udfff",
    ],
)
def test_invalid_text_never_becomes_a_database_value(value):
    with pytest.raises(InvalidComment):
        validate_text(value)


@pytest.mark.parametrize("sort", list(CommentSort))
@pytest.mark.parametrize(
    "date",
    [DATE, datetime(1, 1, 1, tzinfo=UTC), datetime(9999, 12, 31, 23, 59, 59, 999999, tzinfo=UTC)],
)
@pytest.mark.parametrize("identity", [1, 2**63 - 1])
def test_cursor_preserves_microseconds_ids_and_sort_scope(sort, date, identity):
    cursor = CommentCursor(2**63 - 1, sort, 7 if sort == CommentSort.POPULAR else 0, date, identity)
    token = cursor.encode()
    assert len(token) == 46 and "=" not in token
    assert CommentCursor.decode(token, cursor.video_id, sort) == cursor
    with pytest.raises(InvalidCommentCursor):
        CommentCursor.decode(token, 1, sort)
    with pytest.raises(InvalidCommentCursor):
        CommentCursor.decode(
            token,
            cursor.video_id,
            CommentSort.NEWEST if sort == CommentSort.POPULAR else CommentSort.POPULAR,
        )
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    alias = token[:-1] + alphabet[alphabet.index(token[-1]) + 1]
    with pytest.raises(InvalidCommentCursor):
        CommentCursor.decode(alias, cursor.video_id, sort)


@pytest.mark.parametrize(
    "token",
    [
        None,
        1,
        True,
        "",
        "x" * 45,
        "x" * 47,
        "x" * 10000,
        "../etc/passwd",
        "<script>",
        "\0",
        "\ud800",
    ],
)
def test_bad_cursor_is_not_interpreted(token):
    with pytest.raises(InvalidCommentCursor):
        CommentCursor.decode(token, 1, CommentSort.NEWEST)


@pytest.mark.parametrize(
    "version,kind,video,count,micros,identity",
    [
        (0, 0, 1, 0, 0, 1),
        (1, 2, 1, 0, 0, 1),
        (1, 0, 2, 0, 0, 1),
        (1, 0, 1, 1, 0, 1),
        (1, 0, 1, 2**63, 0, 1),
        (1, 0, 1, 0, 0, 0),
        (1, 0, 1, 0, 0, 2**63),
        (1, 0, 1, 0, 2**63 - 1, 1),
        (1, 0, 1, 0, -(2**63), 1),
    ],
)
def test_forged_cursor_fields_are_rejected(version, kind, video, count, micros, identity):
    token = (
        base64.urlsafe_b64encode(
            struct.pack(">BBQQqQ", version, kind, video, count, micros, identity)
        )
        .decode()
        .rstrip("=")
    )
    with pytest.raises(InvalidCommentCursor):
        CommentCursor.decode(token, 1, CommentSort.NEWEST)
