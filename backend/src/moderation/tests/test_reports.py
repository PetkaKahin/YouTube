"""Closed reasons, decision revisions and exact queue boundaries."""

import base64
import struct
from datetime import UTC, datetime

import pytest

from moderation.domain.reports import (
    DecisionCommand,
    InvalidReport,
    InvalidReportCursor,
    Reason,
    ReportCursor,
)


@pytest.mark.parametrize("reason", list(Reason))
def test_reason_and_hide_command_use_only_ready_values(reason):
    assert Reason.parse(reason.value) == reason
    assert DecisionCommand(1, "hide", reason.value).validated() == DecisionCommand(
        1, "hide", reason
    )


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        1,
        [],
        {},
        "",
        "spam ",
        "SPAM",
        "free text",
        "';DROP TABLE moderation_reports;--",
        "<script>",
        "\0",
        "../etc/passwd",
    ],
)
def test_reason_input_never_becomes_code_or_free_text(value):
    with pytest.raises(InvalidReport):
        Reason.parse(value)


@pytest.mark.parametrize(
    "command",
    [
        DecisionCommand(0, "dismiss"),
        DecisionCommand(True, "hide", "other"),
        DecisionCommand(2**63, "hide", "other"),
        DecisionCommand(1, "restore"),
        DecisionCommand(1, "hide"),
        DecisionCommand(1, "hide", "arbitrary"),
        DecisionCommand(1, "dismiss", "other"),
    ],
)
def test_invalid_commands_are_rejected(command):
    with pytest.raises(InvalidReport):
        command.validated()


def test_dismiss_and_maximum_revision_are_valid():
    assert DecisionCommand(2**63 - 1, "dismiss").validated() == DecisionCommand(
        2**63 - 1, "dismiss"
    )


@pytest.mark.parametrize(
    "date",
    [
        datetime(1, 1, 1, tzinfo=UTC),
        datetime(2026, 1, 2, 3, 4, 5, 123456, tzinfo=UTC),
        datetime(9999, 12, 31, 23, 59, 59, 999999, tzinfo=UTC),
    ],
)
@pytest.mark.parametrize("identity", [1, 2**63 - 1])
def test_queue_cursor_preserves_dates_microseconds_and_bigint(date, identity):
    value = ReportCursor(date, identity)
    token = value.encode()
    assert len(token) == 24 and "=" not in token
    assert ReportCursor.decode(token) == value


@pytest.mark.parametrize(
    "value",
    [
        None,
        1,
        True,
        "",
        "x" * 23,
        "x" * 25,
        "x" * 10000,
        "';DROP--",
        "<script>",
        "../etc/passwd",
        "\ud800",
        "\0",
    ],
)
def test_bad_queue_cursor_is_not_interpreted(value):
    with pytest.raises(InvalidReportCursor):
        ReportCursor.decode(value)


@pytest.mark.parametrize(
    "version,kind,micros,identity",
    [
        (0, 1, 0, 1),
        (1, 0, 0, 1),
        (1, 1, 0, 0),
        (1, 1, 0, 2**63),
        (1, 1, 2**63 - 1, 1),
        (1, 1, -(2**63), 1),
    ],
)
def test_cursor_version_kind_and_boundaries_are_checked(version, kind, micros, identity):
    value = base64.urlsafe_b64encode(struct.pack(">BBqQ", version, kind, micros, identity)).decode()
    with pytest.raises(InvalidReportCursor):
        ReportCursor.decode(value)
