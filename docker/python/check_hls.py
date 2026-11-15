"""Qualify local AV1/fMP4 segmentation; this is not a browser/player test."""

import json
import os
import re
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from video.infrastructure.hls import (
    AacAudio,
    Av1Video,
    MediaTrack,
    Segment,
    SubtitleRendition,
    VideoRendition,
    render_master,
)
from video.infrastructure.subtitles import prepare_webvtt


def main() -> None:
    check = TestCase()
    check.assertNotEqual(os.geteuid(), 0, "Run as the image's app user.")
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if ffmpeg is None or ffprobe is None:
        raise SystemExit("FFmpeg and ffprobe are required.")

    def invoke(arguments: list[str], *, success: bool = True):
        result = subprocess.run(  # noqa: S603 -- fixed tools and synthetic inputs, temporary paths
            arguments, capture_output=True, timeout=60, check=False
        )
        if success:
            check.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace")[-4000:])
        return result

    def probe(path: Path):
        result = invoke(
            [
                ffprobe,
                "-v",
                "error",
                "-show_streams",
                "-show_data",
                "-show_frames",
                "-of",
                "json",
                str(path),
            ]
        )
        check.assertFalse(result.stderr, result.stderr.decode(errors="replace"))
        return json.loads(result.stdout)

    def configuration(stream) -> bytes:
        # ffprobe's hex dump has an offset, hex bytes, then an ASCII column.
        data = "".join(
            line.split(": ", 1)[1].split("  ", 1)[0]
            for line in stream["extradata"].splitlines()
            if line.strip()
        )
        result = bytes.fromhex(data)
        check.assertEqual(len(result), stream["extradata_size"])
        return result

    def prepare_embedded_subtitles(directory: Path, durations, presentation_origin):
        srt = directory / "source.srt"
        srt.write_text(
            "1\n00:00:00,500 --> 00:00:01,500\nПривет, мир!\nSecond line\n\n"
            "2\n00:00:05,500 --> 00:00:06,500\nНа границе\n\n"
        )
        ass = directory / "source.ass"
        ass.write_text(
            "[Script Info]\nScriptType: v4.00+\nPlayResX: 640\nPlayResY: 360\n"
            "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
            "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, "
            "Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, "
            "MarginV, Encoding\n"
            "Style: Default,Arial,24,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,"
            "100,100,0,0,1,1,0,2,10,10,10,1\n"
            "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, "
            "MarginV, Effect, Text\n"
            "Dialogue: 0,0:00:00.50,0:00:01.50,Default,,0,0,0,,"
            "{\\pos(100,100)\\fs40}Привет, {\\i1}мир!{\\i0}\\NSecond line\n"
            "Dialogue: 0,0:00:05.50,0:00:06.50,Default,,0,0,0,,На границе\n"
        )
        vtt = directory / "source.vtt"
        vtt.write_text(
            "WEBVTT\n\n00:00.500 --> 00:01.500\nHello <b>world</b>\n\n"
            "00:05.500 --> 00:06.500\nBoundary\n\n"
        )
        mkv = directory / "embedded.mkv"
        invoke(
            [
                ffmpeg,
                "-v",
                "error",
                "-nostdin",
                "-protocol_whitelist",
                "file",
                "-i",
                str(directory / "stream_0.m3u8"),
                "-i",
                str(srt),
                "-i",
                str(ass),
                "-i",
                str(vtt),
                "-map",
                "0:v:0",
                "-map",
                "1:s:0",
                "-map",
                "2:s:0",
                "-map",
                "3:s:0",
                "-c:v",
                "copy",
                "-c:s:0",
                "srt",
                "-c:s:1",
                "ass",
                "-c:s:2",
                "webvtt",
                "-metadata:s:s:0",
                "language=rus",
                "-metadata:s:s:1",
                "language=rus",
                "-metadata:s:s:2",
                "language=eng",
                str(mkv),
            ]
        )
        mp4 = directory / "embedded.mp4"
        invoke(
            [
                ffmpeg,
                "-v",
                "error",
                "-nostdin",
                "-i",
                str(mkv),
                "-map",
                "0:v:0",
                "-map",
                "0:s:0",
                "-c:v",
                "copy",
                "-c:s",
                "mov_text",
                "-metadata:s:s:0",
                "language=rus",
                str(mp4),
            ]
        )
        prepared = []
        for number, (source, subtitle_index, codec, language) in enumerate(
            (
                (mkv, 0, "subrip", "rus"),
                (mkv, 1, "ass", "rus"),
                (mkv, 2, "webvtt", "eng"),
                (mp4, 0, "mov_text", "rus"),
            )
        ):
            metadata = json.loads(
                invoke([ffprobe, "-v", "error", "-show_streams", "-of", "json", str(source)]).stdout
            )["streams"]
            subtitle = [stream for stream in metadata if stream["codec_type"] == "subtitle"][
                subtitle_index
            ]
            check.assertEqual(subtitle["codec_name"], codec)
            check.assertEqual(subtitle["tags"]["language"], language)
            origin = Fraction(
                next(stream for stream in metadata if stream["codec_type"] == "video")["start_time"]
            )
            converted = invoke(
                [
                    ffmpeg,
                    "-v",
                    "error",
                    "-nostdin",
                    "-copyts",
                    "-i",
                    str(source),
                    "-map",
                    f"0:s:{subtitle_index}",
                    "-c:s",
                    "webvtt",
                    "-f",
                    "webvtt",
                    "-",
                ]
            ).stdout
            package = prepare_webvtt(
                converted,
                segment_durations=durations,
                source_origin=origin,
                presentation_origin=presentation_origin,
            )
            check.assertIsNotNone(package)
            if package is None:
                raise AssertionError("Expected embedded cues.")
            for segment in package.segments[:2]:
                check.assertIn(b"00:00:05.500 --> 00:00:06.500", segment.data)
                timestamp_map = re.search(rb"MPEGTS:([0-9]+)", segment.data)
                check.assertIsNotNone(timestamp_map)
                if timestamp_map is None:
                    raise AssertionError("Missing WebVTT timestamp map.")
                mapped_origin = Fraction(int(timestamp_map[1]), 90000)
                check.assertLessEqual(abs(mapped_origin - presentation_origin), Fraction(1, 180000))
            check.assertNotIn(b"-->", package.segments[-1].data)
            if codec == "ass":
                check.assertIn("<i>мир!</i>", package.segments[0].data.decode())
                check.assertNotIn(b"\\pos", package.segments[0].data)
            path = directory / f"subtitles_{number}"
            path.mkdir()
            for segment in package.segments:
                (path / segment.name).write_bytes(segment.data)
                if b"-->" in segment.data:
                    packets = json.loads(
                        invoke(
                            [
                                ffprobe,
                                "-v",
                                "error",
                                "-f",
                                "webvtt",
                                "-show_packets",
                                "-show_entries",
                                "packet=pts_time,duration_time",
                                "-of",
                                "json",
                                str(path / segment.name),
                            ]
                        ).stdout
                    )["packets"]
                    check.assertTrue(
                        any(
                            Fraction(packet["pts_time"]) == Fraction(11, 2)
                            and Fraction(packet["duration_time"]) == 1
                            for packet in packets
                        )
                    )
            (path / "index.m3u8").write_text(package.playlist)
            prepared.append(
                SubtitleRendition(
                    package.as_track(f"{path.name}/index.m3u8"), f"Track {number} {codec}", language
                )
            )
        return prepared

    with TemporaryDirectory(prefix="youtube-hls-") as work:
        for fps, gop, channels, bitrate in (("24", 144, 1, 32000), ("30000/1001", 180, 2, 64000)):
            directory = Path(work) / str(gop)
            directory.mkdir()
            invoke(
                [
                    ffmpeg,
                    "-hide_banner",
                    "-nostdin",
                    "-loglevel",
                    "error",
                    "-filter_threads",
                    "1",
                    "-f",
                    "lavfi",
                    "-i",
                    f"testsrc2=size=128x72:rate={fps}:duration=13",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=440:sample_rate=48000:duration=13",
                    "-map",
                    "0:v",
                    "-map",
                    "0:v",
                    "-map",
                    "1:a",
                    "-filter:v:0",
                    "scale=64:64,setparams=range=limited:color_primaries=bt709:"
                    "color_trc=bt709:colorspace=bt709",
                    "-filter:v:1",
                    "setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709"
                    if fps == "24"
                    else "setparams=range=limited:color_primaries=bt2020:"
                    "color_trc=smpte2084:colorspace=bt2020nc",
                    "-pix_fmt:v",
                    "yuv420p" if fps == "24" else "yuv420p10le",
                    "-color_primaries:v:0",
                    "bt709",
                    "-color_trc:v:0",
                    "bt709",
                    "-colorspace:v:0",
                    "bt709",
                    "-color_primaries:v:1",
                    "bt709" if fps == "24" else "bt2020",
                    "-color_trc:v:1",
                    "bt709" if fps == "24" else "smpte2084",
                    "-colorspace:v:1",
                    "bt709" if fps == "24" else "bt2020nc",
                    "-color_range:v",
                    "tv",
                    "-c:v",
                    "libsvtav1",
                    "-preset",
                    "6",
                    "-crf",
                    "28",
                    "-svtav1-params",
                    "tune=0:lp=2:irefresh-type=2",
                    "-g",
                    str(gop),
                    "-c:a",
                    "aac",
                    "-profile:a",
                    "aac_low",
                    "-b:a",
                    str(bitrate),
                    "-ac",
                    str(channels),
                    "-f",
                    "hls",
                    "-hls_time",
                    "6",
                    "-hls_segment_type",
                    "fmp4",
                    "-hls_playlist_type",
                    "vod",
                    "-hls_flags",
                    "independent_segments",
                    "-hls_fmp4_init_filename",
                    "init_%v.mp4",
                    "-hls_segment_filename",
                    str(directory / "segment_%v_%03d.m4s"),
                    "-master_pl_name",
                    "master.m3u8",
                    "-var_stream_map",
                    "v:0,agroup:sound v:1,agroup:sound a:0,agroup:sound,default:yes",
                    str(directory / "stream_%v.m3u8"),
                ]
            )
            master = (directory / "master.m3u8").read_text()
            check.assertEqual(master.count("#EXT-X-MEDIA:TYPE=AUDIO,"), 1)
            check.assertIn('URI="stream_2.m3u8"', master)
            check.assertEqual(master.count('AUDIO="group_sound"'), 2)
            check.assertEqual(master.count("#EXT-X-STREAM-INF:"), 2)
            boundaries = []
            durations_by_track = []
            tracks = []
            streams = []
            for track in range(3):
                playlist = directory / f"stream_{track}.m3u8"
                text = playlist.read_text()
                check.assertIn("#EXT-X-PLAYLIST-TYPE:VOD", text)
                check.assertIn("#EXT-X-ENDLIST", text)
                check.assertIn(f'#EXT-X-MAP:URI="init_{track}.mp4"', text)
                segments = [line for line in text.splitlines() if line and not line.startswith("#")]
                durations = [float(value) for value in re.findall(r"#EXTINF:([\d.]+),", text)]
                check.assertEqual(len(segments), 3)
                check.assertEqual(len(durations), 3)
                durations_by_track.append(sum(durations))
                tracks.append(
                    MediaTrack(
                        playlist.name,
                        int(re.search(r"#EXT-X-TARGETDURATION:(\d+)", text)[1]),
                        tuple(
                            Segment((directory / segment).stat().st_size, Fraction(str(duration)))
                            for segment, duration in zip(segments, durations, strict=True)
                        ),
                    )
                )
                if track < 2:
                    check.assertIn("#EXT-X-INDEPENDENT-SEGMENTS", text)
                    nominal = float(Fraction(gop, 1) / Fraction(fps))
                    for duration in durations[:2]:
                        check.assertAlmostEqual(duration, nominal, places=5)
                    check.assertLess(durations[-1], nominal)

                starts = []
                frames_total = 0
                for number, segment in enumerate(segments):
                    fragment = directory / segment
                    check.assertEqual(fragment.suffix, ".m4s")
                    # Only init + this segment: previous media is unavailable.
                    isolated = directory / "isolated.mp4"
                    isolated.write_bytes(
                        (directory / f"init_{track}.mp4").read_bytes() + fragment.read_bytes()
                    )
                    decoded = probe(isolated)
                    check.assertEqual(len(decoded["streams"]), 1)
                    stream, frames = decoded["streams"][0], decoded["frames"]
                    if number == 0:
                        streams.append(stream)
                    check.assertTrue(frames)
                    if track < 2:
                        check.assertEqual(stream["codec_name"], "av1")
                        check.assertEqual(Fraction(stream["r_frame_rate"]), Fraction(fps))
                        check.assertEqual(frames[0]["key_frame"], 1)
                        starts.append(float(frames[0]["best_effort_timestamp_time"]))
                        if number < 2:
                            check.assertEqual(len(frames), gop)
                        frames_total += len(frames)
                    else:
                        check.assertEqual(stream["codec_name"], "aac")
                        check.assertEqual(stream["profile"], "LC")
                        check.assertEqual(stream["sample_rate"], "48000")
                        check.assertEqual(stream["channels"], channels)
                if track < 2:
                    check.assertEqual(frames_total, 312 if fps == "24" else 390)
                    boundaries.append(starts)
                    for left, right in zip(starts, starts[1:], strict=False):
                        check.assertAlmostEqual(right - left, nominal, places=5)

            check.assertEqual(boundaries[0], boundaries[1])
            check.assertAlmostEqual(durations_by_track[0], durations_by_track[1], places=5)
            check.assertLess(abs(durations_by_track[2] - durations_by_track[0]), 0.05)
            check.assertEqual(len(list(directory.glob("segment_2_*.m4s"))), 3)
            audio = AacAudio(tracks[2], configuration(streams[2]))
            check.assertEqual(audio.channels, channels)
            variants = []
            color_values = {"bt709": 1, "bt2020": 9, "bt2020nc": 9, "smpte2084": 16}
            for index, stream in enumerate(streams[:2]):
                video = Av1Video(
                    configuration(stream),
                    stream["width"],
                    stream["height"],
                    Fraction(stream["r_frame_rate"]),
                    color_values[stream["color_primaries"]],
                    color_values[stream["color_transfer"]],
                    color_values[stream["color_space"]],
                    stream["color_range"] == "pc",
                )
                check.assertEqual(int(video.codec.split(".")[2][:2]), stream["level"])
                check.assertIn(".08." if stream["pix_fmt"] == "yuv420p" else ".10.", video.codec)
                variants.append(VideoRendition(tracks[index], video, audio))

            subtitles = prepare_embedded_subtitles(
                directory,
                tuple(segment.duration for segment in tracks[0].segments),
                Fraction(streams[0]["start_pts"]) * Fraction(streams[0]["time_base"]),
            )
            completed = render_master(variants, subtitles=subtitles)
            check.assertEqual(completed.count('CODECS="av01.'), 2)
            check.assertEqual(completed.count(',mp4a.40.2"'), 2)
            check.assertEqual(completed.count("#EXT-X-MEDIA:TYPE=AUDIO,"), 1)
            check.assertEqual(completed.count('AUDIO="audio-0"'), 2)
            check.assertEqual(completed.count("TYPE=SUBTITLES,"), 4)
            check.assertEqual(completed.count('SUBTITLES="subtitles"'), 2)
            for variant in variants:
                check.assertIn(f"FRAME-RATE={float(Fraction(fps)):.3f}", completed)
                check.assertIn(f"VIDEO-RANGE={variant.video.video_range}", completed)
            fixed_master = directory / "completed.m3u8"
            fixed_master.write_text(completed)
            # Exercise the actual master, local variant/init/media references,
            # and all video/audio decoders as one consumer path.
            invoke(
                [
                    ffmpeg,
                    "-v",
                    "error",
                    "-nostdin",
                    "-xerror",
                    "-protocol_whitelist",
                    "file",
                    "-i",
                    str(fixed_master),
                    "-map",
                    "0:v",
                    "-map",
                    "0:a",
                    "-f",
                    "null",
                    "-",
                ]
            )
            playlist_result = invoke(
                [ffprobe, "-v", "warning", "-show_streams", "-of", "json", str(fixed_master)]
            )
            playlist_probe = json.loads(playlist_result.stdout)
            subtitle_streams = [
                stream for stream in playlist_probe["streams"] if stream["codec_type"] == "subtitle"
            ]
            if subtitle_streams:
                check.assertEqual(len(subtitle_streams), 4)
                check.assertTrue(
                    all(stream["codec_name"] == "webvtt" for stream in subtitle_streams)
                )
            else:
                warning = playlist_result.stderr.decode(errors="replace")
                check.assertGreaterEqual(warning.count("Can't support the subtitle(uri:"), 4)
                for subtitle in subtitles:
                    check.assertIn(subtitle.track.uri, warning)
                print("FFmpeg HLS skips WebVTT; each nonempty VTT segment was probed directly.")
            silent = render_master([VideoRendition(tracks[0], variants[0].video)])
            check.assertNotIn("mp4a", silent)
            check.assertNotIn("AUDIO=", silent)
            # A media fragment without its initialization must not be accepted.
            missing_init = invoke(
                [ffprobe, "-v", "error", str(directory / "segment_0_001.m4s")], success=False
            )
            check.assertNotEqual(missing_init.returncode, 0)
            print(
                f"HLS {fps}: aligned video, independently decoded segments, "
                f"shared AAC {channels}ch."
            )

    print(
        "Packaging probe passed: 18 independent fMP4 segments; complete masters with "
        "measured bandwidth, actual AV1/AAC CODECS, SDR/PQ, FPS and shared audio."
    )
    print(
        "Embedded SRT/ASS/WebVTT/mov_text: UTF-8, cue boundaries, empty tail and shared HLS group."
    )


if __name__ == "__main__":
    main()
