"""WebVTT timeline, boundary cues, empty periods and safe HLS metadata."""

from fractions import Fraction

import pytest

from video.infrastructure.hls import (
    HlsMetadataError,
    SubtitleRendition,
    VideoRendition,
    render_master,
)
from video.infrastructure.subtitles import SubtitlePreparationError, prepare_webvtt
from video.tests.test_hls import track, video


def prepare(payload, **changes):
    options = {
        "segment_durations": (Fraction(6), Fraction(6), Fraction(1)),
        "source_origin": Fraction(),
        "presentation_origin": Fraction(8, 375),  # actual AV1 start after AAC priming
    }
    return prepare_webvtt(
        payload.encode() if isinstance(payload, str) else payload, **(options | changes)
    )


def test_crossing_cue_is_repeated_with_full_timestamps_and_empty_tail_is_present():
    package = prepare("WEBVTT\n\n00:05.500 --> 00:06.500\nПривет, <i>мир!</i>\n\n")
    assert package is not None
    assert len(package.segments) == 3
    for segment in package.segments[:2]:
        assert "00:00:05.500 --> 00:00:06.500\nПривет, <i>мир!</i>" in segment.data.decode()
        assert b"X-TIMESTAMP-MAP=LOCAL:00:00:00.000,MPEGTS:1920\n" in segment.data
    assert b"-->" not in package.segments[2].data
    assert "#EXTINF:1.000000,\nsegment_000002.vtt" in package.playlist
    assert package.playlist.endswith("#EXT-X-ENDLIST\n")
    assert package.as_track("ru/index.m3u8").segments[0].size_bytes == len(package.segments[0].data)


def test_cues_on_exact_boundary_are_not_duplicated_or_lost():
    package = prepare(
        "WEBVTT\n\n00:05.000 --> 00:06.000\nfirst\n\n00:06.000 --> 00:07.000\nsecond\n\n"
    )
    assert package is not None
    assert b"first" in package.segments[0].data and b"second" not in package.segments[0].data
    assert b"second" in package.segments[1].data and b"first" not in package.segments[1].data


def test_fractional_video_boundaries_keep_all_cue_intersections():
    package = prepare(
        "WEBVTT\n\n00:06.005 --> 00:06.007\nedge\n\n",
        segment_durations=(Fraction(3003, 500), Fraction(3003, 500)),
    )
    assert package is not None
    assert all(b"00:00:06.005 --> 00:00:06.007" in segment.data for segment in package.segments)
    assert "#EXT-X-TARGETDURATION:6" in package.playlist
    assert package.playlist.count("#EXTINF:6.006000,") == 2


def test_source_origin_is_subtracted_and_only_video_endpoints_clip_cues():
    package = prepare(
        "WEBVTT\n\n00:03.000 --> 00:05.000\nstart\n\n00:16.000 --> 00:20.000\nend\n\n",
        source_origin=Fraction(4),
    )
    assert package is not None
    assert b"00:00:00.000 --> 00:00:01.000\nstart" in package.segments[0].data
    assert b"00:00:12.000 --> 00:00:13.000\nend" in package.segments[-1].data


def test_timestamp_map_wraps_mpegts_at_33_bits():
    package = prepare(
        "WEBVTT\n\n00:00.000 --> 00:01.000\nwrapped\n\n",
        presentation_origin=Fraction((1 << 33) + 1920, 90000),
    )
    assert package is not None
    assert b"MPEGTS:1920\n" in package.segments[0].data


@pytest.mark.parametrize(
    "payload",
    ["WEBVTT\n", "WEBVTT\n\nNOTE comment\n", "WEBVTT\n\n00:30.000 --> 00:31.000\noutside\n"],
)
def test_empty_or_outside_track_creates_no_ready_subtitle_package(payload):
    assert prepare(payload) is None


def test_utf8_bom_crlf_ids_settings_and_overlapping_cues_are_normalized():
    package = prepare(
        "\ufeffWEBVTT\r\n\r\nidentifier\r\n00:02.000 --> 00:03.000 align:start\r\n"
        "Русский &amp; English\r\n\r\n00:01.000 --> 00:03.000\r\noverlap\r\n\r\n"
    )
    assert package is not None
    text = package.segments[0].data.decode()
    assert text.index("overlap") < text.index("Русский")
    assert "Русский &amp; English" in text
    assert "identifier" not in text and "align:start" not in text
    assert "\r" not in text and "\ufeff" not in text


def test_raw_html_attributes_and_encoded_tags_cannot_become_executable_markup():
    package = prepare(
        "WEBVTT\n\n00:01.000 --> 00:02.000\n<script>alert(1)</script>"
        '<img src="https://example.com" onerror="x"><b onclick="x">bad</b>'
        "<b><i>safe</b>&lt;u&gt;literal&lt;/u&gt;\n\n"
    )
    assert package is not None
    text = package.segments[0].data.decode()
    assert "<script>" not in text and "<img " not in text and "<b onclick" not in text
    assert "<b><i>safe</i></b>" in text
    assert "&lt;u&gt;literal&lt;/u&gt;" in text


def test_numeric_html_entities_cannot_inject_a_new_cue_or_control_character():
    package = prepare(
        "WEBVTT\n\n00:01.000 --> 00:02.000\n"
        "&#10;&#10;00:03.000 --&gt; 00:04.000&#10;injected&#27;\n\n"
    )
    assert package is not None
    data = package.segments[0].data
    assert data.count(b"-->") == 1
    assert b"&amp;#10;" in data and b"&amp;#27;" in data
    assert b"\x1b" not in data


def test_unicode_separators_in_text_do_not_create_structural_blank_lines():
    package = prepare(
        "WEBVTT\n\n00:01.000 --> 00:02.000\none\u2028\u2028two\u0085three\u2029four\n\n"
    )
    assert package is not None
    text = package.segments[0].data.decode()
    assert "one\u2028\u2028two\u0085three\u2029four\n\n" in text
    assert "\n\ntwo" not in text


@pytest.mark.parametrize(
    "payload",
    [
        b"\xff",
        b"not WebVTT",
        b"WEBVTT\nX-TIMESTAMP-MAP=LOCAL:00:00:00.000,MPEGTS:123\n\n",
        b"WEBVTT\n\n00:00.000 --> 00:01.000\n\x00\n",
        b"WEBVTT\n\n00:00.000 --> 00:01.000\n\x1b[31m\n",
        b"WEBVTT\n\n00:01.000 --> 00:00.000\nbackward\n",
        b"WEBVTT\n\n00:01.000 --> 00:01.000\nzero\n",
        b"WEBVTT\n\n00:60.000 --> 01:01.000\ninvalid\n",
        b"WEBVTT\n\n00:01.000 --> 00:02.000\n",
        b"WEBVTT\n\nincomplete\n",
    ],
)
def test_damaged_output_has_an_explicit_track_error_without_echoing_content(payload):
    with pytest.raises(SubtitlePreparationError) as raised:
        prepare(payload)
    assert payload.decode(errors="replace") not in str(raised.value)


@pytest.mark.parametrize(
    "changes",
    [
        {"segment_durations": ()},
        {"segment_durations": (Fraction(0),)},
        {"source_origin": 1.5},
        {"presentation_origin": 0},
    ],
)
def test_unknown_or_inexact_video_timeline_is_not_guessed(changes):
    with pytest.raises(SubtitlePreparationError):
        prepare("WEBVTT\n", **changes)


def test_master_reuses_subtitle_group_and_accounts_for_only_one_selected_language():
    variants = [VideoRendition(track(), video()), VideoRendition(track("sdr.m3u8"), video())]
    package = prepare("WEBVTT\n\n00:01.000 --> 00:02.000\nРусский\n\n")
    assert package is not None
    subtitles = [
        SubtitleRendition(package.as_track("ru/index.m3u8"), "Русский", "ru"),
        SubtitleRendition(package.as_track("en/index.m3u8"), "English", "en"),
    ]
    master = render_master(variants, subtitles=subtitles)
    assert master.count("TYPE=SUBTITLES,") == 2
    assert master.count('SUBTITLES="subtitles"') == 2
    assert 'NAME="Русский",DEFAULT=NO,AUTOSELECT=NO,URI="ru/index.m3u8",LANGUAGE="ru"' in master
    # Adding a second selectable language doesn't double the required bandwidth.
    for line in master.splitlines():
        if line.startswith("#EXT-X-STREAM-INF:"):
            assert line in render_master(variants, subtitles=subtitles[:1])
    assert "wvtt" not in master  # external WebVTT, not an MP4 WebVTT sample entry


@pytest.mark.parametrize(
    "name,language",
    [('bad"name', "ru"), ("bad\nname", "en"), ("", None), ("name", 'en"'), ("name", "en\n#EXTM3U")],
)
def test_subtitle_metadata_cannot_inject_playlist_lines(name, language):
    with pytest.raises(HlsMetadataError):
        SubtitleRendition(track("subs.m3u8"), name, language)


def test_duplicate_names_and_colliding_playlist_uris_are_rejected():
    variant = VideoRendition(track(), video())
    with pytest.raises(HlsMetadataError):
        render_master([variant], subtitles=[SubtitleRendition(track(), "same URI")])
    with pytest.raises(HlsMetadataError):
        render_master(
            [variant],
            subtitles=[
                SubtitleRendition(track("ru.m3u8"), "same name"),
                SubtitleRendition(track("en.m3u8"), "same name"),
            ],
        )
