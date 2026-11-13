"""Metadata boundaries and identity validation also apply outside HTTP."""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from video.application.publications import CreateDraft, EditVideoSettings, GetVideoSettings
from video.domain.publications import AuthenticationRequired, VideoError, VideoMetadata


@pytest.mark.parametrize("title", ["a", "я" * 100, "🙂" * 100, " \t Видео \n", "<b>data</b>"])
def test_title_is_trimmed_and_unicode_character_lengths_are_used(title):
    data = VideoMetadata(title, " строки\r\nи переносы\n ", "private").normalized()
    assert data.title == title.strip()
    assert data.description == " строки\r\nи переносы\n "
    assert data.visibility == "private"


@pytest.mark.parametrize(
    "data,field",
    [
        (VideoMetadata("", "", "private"), "title"),
        (VideoMetadata(" \t\n\u2003", "", "public"), "title"),
        (VideoMetadata("я" * 101, "", "public"), "title"),
        (VideoMetadata("valid", "🙂" * 5001, "public"), "description"),
        (VideoMetadata("valid", "", "unlisted"), "visibility"),
        (VideoMetadata("a\x00b", "", "public"), "title"),
        (VideoMetadata("a", "b\x00c", "public"), "description"),
        (VideoMetadata("a\ud800", "", "public"), "title"),
        (VideoMetadata("a", "\udfff", "public"), "description"),
    ],
)
def test_metadata_rejects_invalid_values_without_echoing_them(data, field):
    with pytest.raises(VideoError) as raised:
        data.normalized()
    assert raised.value.field == field
    assert str(raised.value) in {"invalid_length", "invalid_characters", "invalid_visibility"}


@pytest.mark.parametrize("identity", [None, True, False, "1", 0, -1, 2**63, 1.1])
@pytest.mark.parametrize("scenario_type", [CreateDraft, EditVideoSettings, GetVideoSettings])
def test_all_public_scenarios_reject_invalid_identity_before_database_calls(
    identity, scenario_type
):
    async def exercise():
        actors = Mock(require=AsyncMock())
        publications = Mock(create=AsyncMock(), owned=AsyncMock(), update_metadata=AsyncMock())
        transaction = Mock()
        scenario = scenario_type(actors, publications, transaction)
        args = {
            CreateDraft: (VideoMetadata("v", "", "public"),),
            EditVideoSettings: (1, VideoMetadata("v", "", "public")),
            GetVideoSettings: (1,),
        }[scenario_type]
        with pytest.raises(AuthenticationRequired):
            await scenario(identity, *args)
        actors.require.assert_not_awaited()
        publications.create.assert_not_awaited()
        publications.owned.assert_not_awaited()
        transaction.begin.assert_not_called()

    asyncio.run(exercise())
