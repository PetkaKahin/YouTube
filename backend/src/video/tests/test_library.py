"""Library cursors preserve exact dates/IDs and never carry personal authority."""

import base64
from datetime import UTC, datetime

import pytest

from video.domain.library import TOKEN, InvalidLibraryCursor, LibraryBoundary, LibraryKind


@pytest.mark.parametrize("kind", list(LibraryKind))
@pytest.mark.parametrize(
    "date",
    [
        datetime(1, 1, 1, tzinfo=UTC),
        datetime(2026, 11, 18, 12, 1, 2, 123456, tzinfo=UTC),
        datetime(9999, 12, 31, 23, 59, 59, 999999, tzinfo=UTC),
    ],
)
def test_library_cursor_exact_and_scoped(date, kind):
    boundary = LibraryBoundary(date, 2**63 - 1)
    actor = 2**63 - 2
    token = boundary.encode(actor, kind)
    assert len(token) == 36 and LibraryBoundary.decode(token, actor, kind) == boundary
    for malformed in ("", token + "=", token[:-1], "x" * 10000, "' OR TRUE; --", "../etc/passwd"):
        with pytest.raises(InvalidLibraryCursor):
            LibraryBoundary.decode(malformed, actor, kind)
    with pytest.raises(InvalidLibraryCursor):
        LibraryBoundary.decode(token, actor - 1, kind)
    for other in LibraryKind:
        if other != kind:
            with pytest.raises(InvalidLibraryCursor):
                LibraryBoundary.decode(token, actor, other)


@pytest.mark.parametrize(
    "parts",
    [
        (0, 76, 2, 1, 0, 1),
        (1, 86, 2, 1, 0, 1),
        (1, 76, 1, 1, 0, 1),
        (1, 76, 2, 1, 2**63 - 1, 1),
        (1, 76, 2, 1, 0, 0),
        (1, 76, 2, 1, 0, 2**63),
    ],
)
def test_library_cursor_rejects_forged_fields(parts):
    token = base64.urlsafe_b64encode(TOKEN.pack(*parts)).decode()
    with pytest.raises(InvalidLibraryCursor):
        LibraryBoundary.decode(token, 1, LibraryKind.LIKED)
