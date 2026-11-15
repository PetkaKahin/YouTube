"""Codec descriptors, bandwidth measurement and safe master construction."""

from dataclasses import replace
from fractions import Fraction

import pytest

from video.infrastructure.hls import (
    AacAudio,
    Av1Video,
    HlsMetadataError,
    MediaTrack,
    Segment,
    VideoRendition,
    render_master,
)


def video():
    # AOM ISOBMFF §5 example: Main, level 3.0, 10-bit, co-located 4:2:0, PQ.
    return Av1Video(bytes.fromhex("81044e00"), 1920, 1080, Fraction(30000, 1001), 9, 16, 9, False)


def track(uri="video/index.m3u8"):
    return MediaTrack(uri, 6, (Segment(60000, Fraction(6)), Segment(120000, Fraction(6))))


def test_aom_normative_codec_string_and_no_sdr_defaults_for_hdr():
    assert video().codec == "av01.0.04M.10.0.112.09.16.09.0"
    assert video().video_range == "PQ"
    sdr = replace(
        video(),
        configuration=bytes.fromhex("81000c00"),
        color_primaries=1,
        transfer_characteristics=1,
        matrix_coefficients=1,
        full_range=True,
    )
    assert sdr.codec == "av01.0.00M.08.0.110.01.01.01.1"
    assert sdr.video_range == "SDR"
    assert replace(video(), configuration=bytes.fromhex("8108ce00")).codec.startswith("av01.0.08H")


@pytest.mark.parametrize(
    "configuration",
    [
        b"",
        bytes.fromhex("81044e"),  # truncated
        bytes.fromhex("01044e00"),  # missing marker
        bytes.fromhex("82044e00"),  # unsupported version
        bytes.fromhex("81244e00"),  # unsupported profile
        bytes.fromhex("811f4e00"),  # unknown level
        bytes.fromhex("8104ce00"),  # high tier unavailable at this level
        bytes.fromhex("81046e00"),  # 12-bit
        bytes.fromhex("81045e00"),  # monochrome
        bytes.fromhex("81044000"),  # 4:4:4
        bytes.fromhex("81044f00"),  # reserved chroma position
        bytes.fromhex("81044e80"),  # reserved bits
        bytes.fromhex("81044e01"),  # reserved presentation delay
    ],
)
def test_incomplete_or_unsupported_av1_configuration_is_rejected(configuration):
    with pytest.raises(HlsMetadataError):
        replace(video(), configuration=configuration)


@pytest.mark.parametrize(
    "changes",
    [
        {"width": 0},
        {"height": True},
        {"frame_rate": Fraction(0)},
        {"frame_rate": 29.97},
        {"color_primaries": 2},
        {"color_primaries": 255},
        {"color_primaries": 3},
        {"transfer_characteristics": 2},
        {"transfer_characteristics": 18},  # HLG must be normalized before output
        {"matrix_coefficients": 2},
        {"matrix_coefficients": 255},
        {"full_range": None},
    ],
)
def test_missing_required_video_metadata_never_yields_a_master(changes):
    with pytest.raises(HlsMetadataError):
        replace(video(), **changes)


def test_peak_bandwidth_uses_rfc_windows_instead_of_short_tail_rate():
    measured = MediaTrack(
        "video/index.m3u8",
        6,
        (*track().segments, Segment(50000, Fraction(1))),
    )
    # The one-second tail alone isn't an eligible 3–9s window. Last 7s is peak.
    assert measured.bitrates() == (194286, 141539)
    assert MediaTrack("short.m3u8", 6, (Segment(125, Fraction(1)),)).bitrates() == (1000, 1000)


def test_target_duration_allows_ntsc_fraction_but_rejects_incorrect_playlist_metadata():
    assert MediaTrack("ntsc.m3u8", 6, (Segment(60000, Fraction(6006, 1000)),))
    with pytest.raises(HlsMetadataError):
        MediaTrack("invalid.m3u8", 6, (Segment(60000, Fraction(13, 2)),))


def test_master_has_actual_codecs_rate_size_range_and_one_reused_audio_profile():
    audio = AacAudio(track("audio/index.m3u8"), bytes.fromhex("1190"))
    variants = [
        VideoRendition(track(), video(), audio),
        VideoRendition(track("other/index.m3u8"), replace(video(), width=1280, height=720), audio),
    ]
    master = render_master(variants)
    assert master.startswith("#EXTM3U\n#EXT-X-VERSION:7\n")
    assert master.count("#EXT-X-MEDIA:TYPE=AUDIO,") == 1
    assert master.count('AUDIO="audio-0"') == 2
    assert master.count('CODECS="av01.0.04M.10.0.112.09.16.09.0,mp4a.40.2"') == 2
    assert master.count("BANDWIDTH=320000,AVERAGE-BANDWIDTH=240000,") == 2
    assert "RESOLUTION=1920x1080,FRAME-RATE=29.970,VIDEO-RANGE=PQ" in master
    assert master.endswith("other/index.m3u8\n")
    assert video().frame_rate == Fraction(30000, 1001)


def test_silent_output_needs_no_audio_track_or_dummy_codec():
    master = render_master([VideoRendition(track(), video())])
    assert "#EXT-X-MEDIA" not in master
    assert "mp4a" not in master
    assert "AUDIO=" not in master
    assert "BANDWIDTH=160000,AVERAGE-BANDWIDTH=120000," in master


@pytest.mark.parametrize("configuration", [b"", b"\x11", b"\x29\x90", b"\x12\x10", b"\x11\xb8"])
def test_audio_codec_cannot_be_assumed_from_a_filename(configuration):
    with pytest.raises(HlsMetadataError):
        AacAudio(track("audio.m3u8"), configuration)


def test_mono_is_read_from_audio_configuration():
    assert AacAudio(track("audio.m3u8"), bytes.fromhex("1188")).channels == 1


@pytest.mark.parametrize(
    "uri",
    [
        "../secret",
        "a/../secret",
        "/etc/passwd",
        "https://example.com/track.m3u8",
        "a%2f..%2fsecret",
        "a\\secret",
        "//host/file",
        'a"bad',
        "a\n#EXT-X-MEDIA:injected",
        "a\rtrailer",
        "a?query=value",
        "",
    ],
)
def test_uri_injection_and_external_paths_are_rejected(uri):
    with pytest.raises(HlsMetadataError):
        track(uri)


@pytest.mark.parametrize("size,duration", [(0, Fraction(1)), (True, Fraction(1)), (1, Fraction(0))])
def test_empty_segments_and_invalid_durations_are_rejected(size, duration):
    with pytest.raises(HlsMetadataError):
        Segment(size, duration)


def test_empty_duplicate_and_conflicting_tracks_are_rejected():
    with pytest.raises(HlsMetadataError):
        render_master([])
    with pytest.raises(HlsMetadataError):
        MediaTrack("empty.m3u8", 6, ())
    variant = VideoRendition(track(), video())
    with pytest.raises(HlsMetadataError):
        render_master([variant, variant])
    with pytest.raises(HlsMetadataError):
        render_master([replace(variant, audio=AacAudio(track(), bytes.fromhex("1190")))])
    stereo = AacAudio(track("audio.m3u8"), bytes.fromhex("1190"))
    mono = replace(stereo, configuration=bytes.fromhex("1188"))
    with pytest.raises(HlsMetadataError):
        render_master(
            [
                replace(variant, audio=stereo),
                VideoRendition(track("other.m3u8"), video(), mono),
            ]
        )
