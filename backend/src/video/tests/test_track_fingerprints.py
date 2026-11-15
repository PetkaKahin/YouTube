"""Exact compressed identities, complete reads, and shared SQL references."""

import asyncio
import json
import shutil
import subprocess
import tracemalloc
from contextlib import AsyncExitStack
from fractions import Fraction
from hashlib import sha256
from uuid import uuid4
from xml.etree.ElementTree import Element, SubElement, tostring

import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from video.application.processing import RepairSourceProcessing, SourceJobs
from video.domain.processing import SourceInspectionError
from video.infrastructure.models import (
    MediaContentRow,
    MediaTrackRow,
    SourceTrackRow,
)
from video.infrastructure.track_inspector import PROBE_ENTRIES, inspect_report
from video.public import CompleteUpload, GetVideoSettings
from video.tests.test_publications import invoke
from video.tests.test_source_processing import process, source_environment, stored

pytestmark = pytest.mark.usefixtures("video_media_cleanup")


def ffmpeg(*arguments):
    subprocess.run(  # noqa: S603 -- generated local fixture, no shell
        [
            shutil.which("ffmpeg"),
            "-v",
            "error",
            "-nostdin",
            *map(str, arguments[:-1]),
            "-write_btrt",
            "0",
            str(arguments[-1]),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )


async def scan(path):
    report = path.with_suffix(path.suffix + ".xml")
    subprocess.run(  # noqa: S603 -- fixed ffprobe and generated local file
        [
            shutil.which("ffprobe"),
            "-v",
            "error",
            "-show_streams",
            "-show_packets",
            "-show_format",
            "-show_data_hash",
            "sha256",
            "-show_entries",
            PROBE_ENTRIES,
            "-of",
            "xml",
            "-o",
            str(report),
            str(path),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    return await inspect_report(report, sha256(path.read_bytes()).hexdigest())


@pytest.fixture(scope="module")
def media_samples(tmp_path_factory):
    directory = tmp_path_factory.mktemp("track-fixtures")
    base = directory / "base.mp4"
    subtitle = directory / "source.srt"
    subtitle.write_text("1\n00:00:00,100 --> 00:00:00,400\nOriginal words\n")
    ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=64x96:rate=24:duration=0.5",
        "-f",
        "lavfi",
        "-i",
        "sine=sample_rate=48000:duration=0.5",
        "-i",
        subtitle,
        "-map",
        "0:v",
        "-map",
        "1:a",
        "-map",
        "2:s",
        "-c:v",
        "mpeg4",
        "-c:a",
        "aac",
        "-c:s",
        "mov_text",
        "-metadata",
        "title=private-title",
        base,
    )
    tagged = directory / "tagged.mp4"
    ffmpeg(
        "-i",
        base,
        "-map",
        "0",
        "-c",
        "copy",
        "-metadata",
        "title=other-private-title",
        "-metadata",
        "artist=somebody-else",
        tagged,
    )
    rotated = directory / "rotated.mp4"
    ffmpeg("-display_rotation", "90", "-i", base, "-map", "0", "-c", "copy", rotated)
    retimed = directory / "retimed.mp4"
    ffmpeg("-itsscale", "2", "-i", base, "-map", "0", "-c", "copy", retimed)
    audio = directory / "audio.mp4"
    ffmpeg(
        "-i",
        base,
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=900:sample_rate=48000:duration=0.5",
        "-map",
        "0:v",
        "-map",
        "1:a",
        "-map",
        "0:s",
        "-c",
        "copy",
        "-c:a",
        "aac",
        audio,
    )
    subtitle.write_text("1\n00:00:00,100 --> 00:00:00,400\nDifferent words\n")
    subtitles = directory / "subtitles.mp4"
    ffmpeg(
        "-i",
        base,
        "-i",
        subtitle,
        "-map",
        "0:v",
        "-map",
        "0:a",
        "-map",
        "1:s",
        "-c",
        "copy",
        "-c:s",
        "mov_text",
        subtitles,
    )
    changed = directory / "changed.mp4"
    ffmpeg("-i", base, "-map", "0", "-c", "copy", "-c:v", "mpeg4", "-q:v", "12", changed)
    color = directory / "color.mp4"
    ffmpeg(
        "-i",
        base,
        "-map",
        "0",
        "-c",
        "copy",
        "-color_primaries",
        "bt2020",
        "-color_trc",
        "smpte2084",
        "-colorspace",
        "bt2020nc",
        color,
    )
    return {
        name: directory / f"{name}.mp4"
        for name in (
            "base",
            "tagged",
            "rotated",
            "retimed",
            "audio",
            "subtitles",
            "changed",
            "color",
        )
    }


def test_container_descriptions_do_not_change_compressed_fingerprints(media_samples):
    async def exercise():
        before, after = await scan(media_samples["base"]), await scan(media_samples["tagged"])
        assert media_samples["base"].read_bytes() != media_samples["tagged"].read_bytes()
        assert before.fingerprint == after.fingerprint
        assert [track.fingerprint for track in before.tracks] == [
            track.fingerprint for track in after.tracks
        ]
        assert [track.properties["codec_type"] for track in before.tracks] == [
            "video",
            "audio",
            "subtitle",
        ]
        assert (before.width, before.height) == (64, 96)
        assert "private-title" not in json.dumps(before.payload())
        assert all("source_guard" not in track.properties for track in before.tracks)

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "variant", ["rotated", "retimed", "audio", "subtitles", "changed", "color"]
)
def test_technical_changes_never_match_whole_source(media_samples, variant):
    async def exercise():
        before, after = await scan(media_samples["base"]), await scan(media_samples[variant])
        assert before.fingerprint != after.fingerprint
        index = {"audio": 1, "subtitles": 2}.get(variant, 0)
        assert before.tracks[index].fingerprint != after.tracks[index].fingerprint
        if variant in {"audio", "subtitles"}:
            assert before.tracks[0].fingerprint == after.tracks[0].fingerprint
        if variant == "rotated":
            assert "Display Matrix" in json.dumps(after.tracks[0].properties)

    asyncio.run(exercise())


def report_file(path, *, scale=1, duration=1000, side=None, offset=0, second_offset=None):
    root = Element("ffprobe")
    packets, streams = SubElement(root, "packets"), SubElement(root, "streams")
    for index in range(2 if second_offset is not None else 1):
        start = offset if index == 0 else second_offset
        stream = SubElement(
            streams,
            "stream",
            {
                "index": str(index),
                "codec_name": "mpeg4" if index == 0 else "aac",
                "codec_type": "video" if index == 0 else "audio",
                "width": "64",
                "height": "96",
                "time_base": f"1/{1000 * scale}",
                "start_pts": str(start * scale),
                "duration_ts": str(duration * scale),
                "avg_frame_rate": "1/1",
            },
        )
        if side:
            SubElement(SubElement(stream, "side_data_list"), "side_data", side)
        SubElement(
            packets,
            "packet",
            {
                "stream_index": str(index),
                "pts": str(start * scale),
                "dts": str(start * scale),
                "duration": str(duration * scale),
                "size": "10",
                "flags": "K__",
                "data_hash": "SHA256:" + "a" * 64,
            },
        )
    path.write_bytes(tostring(root))
    return path


def test_rational_time_units_and_common_origin_are_normalized(tmp_path):
    async def exercise():
        first = await inspect_report(report_file(tmp_path / "a.xml", second_offset=100), "a" * 64)
        second = await inspect_report(
            report_file(tmp_path / "b.xml", scale=90, offset=500, second_offset=600), "b" * 64
        )
        assert first.fingerprint == second.fingerprint
        assert first.tracks[1].offset == Fraction(1, 10)
        shifted = await inspect_report(report_file(tmp_path / "c.xml", second_offset=200), "a" * 64)
        assert first.fingerprint != shifted.fingerprint
        assert first.tracks[1].fingerprint == shifted.tracks[1].fingerprint

    asyncio.run(exercise())


@pytest.mark.parametrize("duration,code", [(3600000, None), (3600001, "duration_limit")])
def test_actual_packet_duration_limit_is_exact(tmp_path, duration, code):
    async def exercise():
        path = report_file(tmp_path / "limit.xml", duration=duration)
        if code:
            with pytest.raises(SourceInspectionError, match=f"^{code}$"):
                await inspect_report(path, "a" * 64)
        else:
            assert (await inspect_report(path, "a" * 64)).duration == 3600

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "change", ["corrupt", "hash", "stream", "xml", "empty", "error", "missing_duration"]
)
def test_incomplete_reports_cannot_confirm_matches(tmp_path, change):
    path = report_file(tmp_path / "broken.xml")
    data = path.read_bytes()
    if change == "corrupt":
        data = data.replace(b'flags="K__"', b'flags="K_C"')
    if change == "hash":
        data = data.replace(b"SHA256:", b"BAD:")
    if change == "stream":
        data = data.replace(b'stream_index="0"', b'stream_index="9"')
    if change == "xml":
        data = data[:-20]
    if change == "empty":
        data = re_packet_removed(data)
    if change == "error":
        data = data.replace(b"</ffprobe>", b'<error code="1"/></ffprobe>')
    if change == "missing_duration":
        data = data.replace(b'duration="1000"', b'duration="0"').replace(
            b'avg_frame_rate="1/1"', b'avg_frame_rate="0/0"'
        )
    path.write_bytes(data)
    with pytest.raises(SourceInspectionError):
        asyncio.run(inspect_report(path, "a" * 64))


def re_packet_removed(data):
    import re

    return re.sub(rb"<packet .*?/>", b"", data)


def test_unknown_side_data_uses_conservative_source_guard(tmp_path):
    path = report_file(
        tmp_path / "unknown.xml",
        side={
            "side_data_type": "Unknown opaque data",
            "description": "private-description-never-store",
        },
    )
    first = asyncio.run(inspect_report(path, "a" * 64))
    second = asyncio.run(inspect_report(path, "b" * 64))
    assert first.fingerprint != second.fingerprint
    assert "private-description-never-store" not in json.dumps(first.payload())


@pytest.mark.parametrize("missing", ["duration", "timestamps"])
def test_inferred_timeline_cannot_merge_different_unrepresented_sources(tmp_path, missing):
    path = report_file(tmp_path / "inferred.xml")
    data = path.read_bytes()
    if missing == "duration":
        data = data.replace(b'duration="1000"', b'duration="0"')
    else:
        data = data.replace(b' pts="0"', b"").replace(b' dts="0"', b"")
    path.write_bytes(data)
    first = asyncio.run(inspect_report(path, "a" * 64))
    second = asyncio.run(inspect_report(path, "b" * 64))
    assert first.duration == second.duration == 1
    assert first.fingerprint != second.fingerprint


def test_shared_references_across_accounts_do_not_leak(
    backend_runtime,
    media_samples,
    tmp_path,
    source_queue,
):
    async def exercise():
        async with AsyncExitStack() as stack:
            contexts = [
                await stack.enter_async_context(
                    source_environment(backend_runtime, media_samples[name].read_bytes())
                )
                for name in ("base", "tagged", "audio")
            ]
            for container, _, owner, draft, plan in contexts:
                await invoke(container, CompleteUpload, owner.id, draft.id, plan.id)
            container, resources, *_ = contexts[0]
            await process(container, resources, tmp_path, source_queue)
            sources = [await stored(resources, context[-1].id) for context in contexts]
            assert sources[0].content_id == sources[1].content_id != sources[2].content_id
            async with resources.sessions() as session:
                refs = [
                    (
                        await session.scalars(
                            select(SourceTrackRow)
                            .where(SourceTrackRow.upload_id == context[-1].id)
                            .order_by(SourceTrackRow.stream_index)
                        )
                    ).all()
                    for context in contexts
                ]
                assert [ref.track_id for ref in refs[0]] == [ref.track_id for ref in refs[1]]
                assert refs[0][0].track_id == refs[2][0].track_id
                assert refs[0][1].track_id != refs[2][1].track_id
            for context in contexts:
                settings = await invoke(context[0], GetVideoSettings, context[2].id, context[3].id)
                assert settings.processing.value == "ready"
                assert not hasattr(settings, "content_id") and not hasattr(settings, "fingerprint")

    asyncio.run(exercise())


def test_concurrent_finish_rollback_and_stale_token_do_not_duplicate_shared_rows(
    backend_runtime,
    tmp_path,
):
    path = tmp_path / "unique.mp4"
    ffmpeg("-f", "lavfi", "-i", "color=green:size=80x80:rate=7:duration=0.5", "-c:v", "mpeg4", path)

    async def exercise():
        result = await scan(path)
        async with AsyncExitStack() as stack:
            contexts = [
                await stack.enter_async_context(
                    source_environment(backend_runtime, path.read_bytes())
                )
                for _ in range(2)
            ]
            claims = []
            for container, resources, owner, draft, plan in contexts:
                await invoke(container, CompleteUpload, owner.id, draft.id, plan.id)
                source = await stored(resources, plan.id)
                async with container() as operation:
                    session = await operation.get(AsyncSession)
                    async with session.begin():
                        await session.execute(
                            text("UPDATE procrastinate_jobs SET status='doing' WHERE id=:id"),
                            {"id": source.job_id},
                        )
                        claims.append(
                            await (await operation.get(SourceJobs)).claim(plan.id, source.job_id, 0)
                        )

            async def finish(index, token=None):
                async with contexts[index][0]() as operation:
                    session = await operation.get(AsyncSession)
                    async with session.begin():
                        await (await operation.get(SourceJobs)).finish(
                            contexts[index][-1].id, token or claims[index].token, result
                        )

            resources = contexts[0][1]

            async def count_rows():
                async with resources.sessions() as session:
                    return tuple(
                        [
                            await session.scalar(select(func.count()).select_from(model))
                            for model in (MediaTrackRow, MediaContentRow, SourceTrackRow)
                        ]
                    )

            before = await count_rows()
            await finish(0, uuid4())
            assert await count_rows() == before

            def fail(connection, cursor, statement, parameters, context, many):
                if statement.startswith("INSERT INTO video_media_contents"):
                    raise RuntimeError("synthetic identity write failure")

            event.listen(resources.engine.sync_engine, "before_cursor_execute", fail)
            try:
                with pytest.raises(RuntimeError, match="synthetic identity write failure"):
                    await finish(0)
            finally:
                event.remove(resources.engine.sync_engine, "before_cursor_execute", fail)
            assert await count_rows() == before
            assert (await stored(resources, contexts[0][-1].id)).status == "inspecting"
            await asyncio.wait_for(asyncio.gather(finish(0), finish(1)), timeout=15)
            after = await count_rows()
            assert after == (before[0] + 1, before[1] + 1, before[2] + 2)
            left, right = [await stored(resources, context[-1].id) for context in contexts]
            assert left.content_id == right.content_id
            await finish(0)
            assert await count_rows() == after

    asyncio.run(exercise())


def test_large_report_is_parsed_with_bounded_memory(tmp_path):
    path = report_file(tmp_path / "many.xml")
    packet = path.read_bytes().split(b"<packets>")[1].split(b"</packets>")[0]
    data = path.read_bytes()
    path.write_bytes(data.replace(packet, packet * 20000))
    assert path.stat().st_size > 3_000_000
    tracemalloc.start()
    try:
        result = asyncio.run(inspect_report(path, "a" * 64))
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert result.tracks[0].packet_count == 20000
    assert peak < 8_000_000


@pytest.mark.parametrize("terminal", [False, True])
def test_recovery_in_flight_blocks_and_fences_late_result(backend_runtime, tmp_path, terminal):
    path = tmp_path / "race.mp4"
    ffmpeg("-f", "lavfi", "-i", "color=blue:size=64x80:rate=6:duration=0.5", "-c:v", "mpeg4", path)

    async def exercise():
        result = await scan(path)
        async with source_environment(backend_runtime, path.read_bytes()) as (
            container,
            resources,
            owner,
            draft,
            plan,
        ):
            await invoke(container, CompleteUpload, owner.id, draft.id, plan.id)
            source = await stored(resources, plan.id)
            async with container() as operation:
                session = await operation.get(AsyncSession)
                async with session.begin():
                    await session.execute(
                        text("UPDATE procrastinate_jobs SET status='doing' WHERE id=:id"),
                        {"id": source.job_id},
                    )
                    claim = await (await operation.get(SourceJobs)).claim(plan.id, source.job_id, 0)
            entered = asyncio.Event()
            worker_pid = None

            async def late():
                nonlocal worker_pid
                async with container() as operation:
                    session = await operation.get(AsyncSession)
                    async with session.begin():
                        await session.execute(text("SET LOCAL statement_timeout='5s'"))
                        worker_pid = await session.scalar(text("SELECT pg_backend_pid()"))
                        entered.set()
                        jobs = await operation.get(SourceJobs)
                        if terminal:
                            await jobs.fail(plan.id, claim.token, "invalid_media", terminal=True)
                        else:
                            await jobs.finish(plan.id, claim.token, result)

            async with resources.sessions.begin() as recovering:
                await recovering.execute(
                    text("UPDATE procrastinate_jobs SET status='todo',attempts=1 WHERE id=:id"),
                    {"id": source.job_id},
                )
                task = asyncio.create_task(late())
                try:
                    await entered.wait()
                    blocked = False
                    async with resources.sessions() as observer:
                        for _ in range(100):
                            blocked = bool(
                                await observer.scalar(
                                    text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"),
                                    {"pid": worker_pid},
                                )
                            )
                            if blocked or task.done():
                                break
                            await asyncio.sleep(0.01)
                    assert blocked and not task.done()
                except BaseException:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    raise
            await asyncio.wait_for(task, timeout=5)
            after = await stored(resources, plan.id)
            assert after.status == "inspecting" and after.content_id is None
            assert after.token == claim.token and after.error_code is None
            async with resources.sessions() as session:
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(SourceTrackRow)
                        .where(SourceTrackRow.upload_id == plan.id)
                    )
                    == 0
                )

    asyncio.run(exercise())


def test_truncated_faststart_source_does_not_get_shared_identity(
    backend_runtime,
    media_samples,
    tmp_path,
    source_queue,
):
    path = tmp_path / "faststart.mp4"
    ffmpeg("-i", media_samples["base"], "-map", "0", "-c", "copy", "-movflags", "+faststart", path)
    data = path.read_bytes()
    data = data[: len(data) * 3 // 4]

    async def exercise():
        async with source_environment(backend_runtime, data) as (
            container,
            resources,
            owner,
            draft,
            plan,
        ):
            await invoke(container, CompleteUpload, owner.id, draft.id, plan.id)
            await process(container, resources, tmp_path / "spool", source_queue)
            source = await stored(resources, plan.id)
            assert source.status == "failed" and source.error_code == "invalid_media"
            assert source.content_id is None and source.inspection is None

    asyncio.run(exercise())


def test_old_header_success_is_requeued_once_then_upgraded(
    backend_runtime, media_samples, tmp_path, source_queue
):
    async def exercise():
        async with source_environment(backend_runtime, media_samples["base"].read_bytes()) as (
            container,
            resources,
            owner,
            draft,
            plan,
        ):
            await invoke(container, CompleteUpload, owner.id, draft.id, plan.id)
            old = await stored(resources, plan.id)
            async with resources.sessions.begin() as session:
                await session.execute(
                    text(
                        "UPDATE video_source_processing SET status='inspected',"
                        "inspection=jsonb_build_object('version',1),"
                        "inspected_at=now() WHERE upload_id=:id"
                    ),
                    {"id": plan.id},
                )
                await session.execute(
                    text("UPDATE procrastinate_jobs SET status='succeeded' WHERE id=:id"),
                    {"id": old.job_id},
                )
            await invoke(container, RepairSourceProcessing)
            source = await stored(resources, plan.id)
            assert (
                source.job_id != old.job_id
                and source.status == "pending"
                and source.inspection is None
            )
            await invoke(container, RepairSourceProcessing)
            assert (await stored(resources, plan.id)).job_id == source.job_id
            await process(container, resources, tmp_path, source_queue)
            assert (await stored(resources, plan.id)).content_id is not None

    asyncio.run(exercise())
