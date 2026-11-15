"""Attach subtitles only to verified video clocks; compatible packages share one job."""

from typing import cast

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from video.domain.encoding import PlannedTrack, mapping
from video.domain.subtitles import subtitle_inventory, subtitle_profile
from video.infrastructure.encoding_jobs import ensure_output
from video.infrastructure.models import (
    EncodedTrackRow,
    MediaPlanRow,
    PlanTrackRow,
    SourceProcessingRow,
    SourceTrackRow,
    SubtitleLinkRow,
)


async def admit_video_subtitles(session: AsyncSession, video_id: int) -> None:
    video = await session.get(EncodedTrackRow, video_id)
    if (
        video is None
        or video.status != "ready"
        or video.spec.get("kind") != "video"
        or video.artifact is None
    ):
        return
    # Separate two-int lock namespace; no collision with per-profile bigint locks.
    await session.execute(text("SELECT pg_advisory_xact_lock(73144,17)"))
    plans = await session.scalars(
        select(MediaPlanRow)
        .join(PlanTrackRow, PlanTrackRow.upload_id == MediaPlanRow.upload_id)
        .where(MediaPlanRow.status == "planned", PlanTrackRow.encoded_id == video_id)
    )
    for plan in plans:
        if plan.config is None or not any(
            variant["video"] == video_id
            for variant in cast(list[dict[str, object]], plan.config["variants"])
        ):
            continue
        source = await session.get(SourceProcessingRow, plan.upload_id)
        if source is None or source.inspection is None:
            continue
        primary, tracks = subtitle_inventory(source.inspection)
        for track in tracks:
            index = cast(int, track["stream_index"])
            await session.execute(
                insert(SubtitleLinkRow)
                .values(
                    upload_id=plan.upload_id,
                    stream_index=index,
                    video_encoded_id=video_id,
                    language=str(track.get("language", "und")),
                    disposition=mapping(track.get("disposition", {})),
                )
                .on_conflict_do_nothing()
            )
            link = await session.get(SubtitleLinkRow, (plan.upload_id, index, video_id))
            if link is None or link.status != "waiting":
                continue
            if "presentation_origin" not in video.artifact:
                clock = PlannedTrack(
                    0,
                    video.profile_key,
                    {
                        "profile": "video-clock-v1",
                        "kind": "subtitle_clock",
                        "video_id": video_id,
                        "video_artifact": video.artifact,
                    },
                    0,
                )
                link.clock_id = await ensure_output(
                    session, clock, video.track_id, upload_id=video.source_upload_id
                )
                calibration = await session.get(EncodedTrackRow, link.clock_id)
                if calibration is not None and calibration.status == "failed":
                    link.status, link.error_code = "failed", "invalid_subtitle_timeline"
                continue
            output = subtitle_profile(track, primary, video.artifact)
            track_id = await session.scalar(
                select(SourceTrackRow.track_id).where(
                    SourceTrackRow.upload_id == plan.upload_id, SourceTrackRow.stream_index == index
                )
            )
            if track_id is None:
                continue
            link.encoded_id = await ensure_output(
                session, output, track_id, upload_id=plan.upload_id
            )
            link.status = "queued"


async def admit_plan_subtitles(session: AsyncSession, plan: MediaPlanRow) -> None:
    if plan.config is not None:
        ids = {
            cast(int, variant["video"])
            for variant in cast(list[dict[str, object]], plan.config["variants"])
        }
        for video_id in sorted(ids):
            await admit_video_subtitles(session, video_id)


async def repair_subtitles(session: AsyncSession) -> int:
    # Only plans not yet inventoried, or references awaiting an older video clock.
    plans = (
        await session.scalars(
            select(MediaPlanRow)
            .where(
                MediaPlanRow.status == "planned",
                MediaPlanRow.config.is_not(None),
                ~MediaPlanRow.config.has_key("subtitles_inventoried"),
            )
            .limit(100)
            .with_for_update(skip_locked=True)
        )
    ).all()
    for plan in plans:
        await admit_plan_subtitles(session, plan)
        plan.config = {**cast(dict[str, object], plan.config), "subtitles_inventoried": True}
    waiting = (
        await session.scalars(
            select(SubtitleLinkRow.video_encoded_id)
            .where(SubtitleLinkRow.status == "waiting")
            .distinct()
            .limit(100)
        )
    ).all()
    for video_id in waiting:
        await admit_video_subtitles(session, video_id)
    return len(plans)
