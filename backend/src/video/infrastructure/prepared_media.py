"""Export verified seed packages and atomically restore their metadata without encoding."""

import copy
import hashlib
import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from botocore.exceptions import ClientError
from botocore.response import StreamingBody
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from common.contracts.storage import ObjectBucket, ObjectStorageClient
from video.application.prepared_media import PreparedMediaError
from video.domain.encoding import signature
from video.domain.publications import ProcessingState, VideoMetadata, VideoNotFound, VideoSettings
from video.infrastructure.models import (
    EncodedTrackRow,
    MediaContentRow,
    MediaPlanRow,
    MediaTrackRow,
    PlanTrackRow,
    PublicationRow,
    SourceProcessingRow,
    SourceTrackRow,
    SubtitleLinkRow,
    ThumbnailChoiceRow,
    UploadRow,
)
from video.infrastructure.multipart import sdk
from video.infrastructure.prepared_bundle import file_path, prepared_prefix, read_bundle
from video.infrastructure.publications import settings_value
from video.infrastructure.readiness import SqlProcessingViews, prepare


def plan_config(bundle: dict[str, Any], identities: dict[str, int]) -> dict[str, Any]:
    config = copy.deepcopy(bundle["plan"])
    for variant in config["variants"]:
        variant["video"] = identities[variant["video"]]
        if variant["audio"] is not None:
            variant["audio"] = identities[variant["audio"]]
    config["thumbnail"] = identities[config["thumbnail"]]
    return config


def validate_bundle(directory: Path) -> dict[str, Any]:
    bundle = read_bundle(directory)
    try:
        if (
            type(bundle["source"]["inspection"]["version"]) is not int
            or bundle["source"]["inspection"]["version"] != 3
            or bundle["source"]["inspection"]["fingerprint"] != bundle["source"]["fingerprint"]
            or re.fullmatch(r"[0-9a-f]{64}", bundle["source"]["fingerprint"]) is None
            or type(bundle["source"]["size"]) is not int
            or not 0 < bundle["source"]["size"] <= 5_000_000_000
            or type(bundle["slot"]) is not int
            or not 0 <= bundle["slot"] <= 2
        ):
            raise PreparedMediaError("Invalid prepared source")
        tracks = bundle["source"]["tracks"]
        if not isinstance(tracks, list) or not 1 <= len(cast(list[Any], tracks)) <= 128:
            raise PreparedMediaError("Invalid prepared tracks")
        fingerprints: set[str] = set()
        indexes: set[int] = set()
        for track in cast(list[dict[str, Any]], tracks):
            if (
                re.fullmatch(r"[0-9a-f]{64}", track["fingerprint"]) is None
                or type(track["stream_index"]) is not int
                or track["stream_index"] < 0
                or track["stream_index"] in indexes
                or not isinstance(track["properties"], dict)
                or type(track["packet_count"]) is not int
                or track["packet_count"] < 0
            ):
                raise PreparedMediaError("Invalid prepared track")
            fingerprints.add(track["fingerprint"])
            indexes.add(track["stream_index"])
        if any(row["track_fingerprint"] not in fingerprints for row in bundle["outputs"]):
            raise PreparedMediaError("Missing prepared track")
        if any(
            row["profile_key"] != signature(row["track_fingerprint"], row["spec"])
            for row in bundle["outputs"]
        ):
            raise PreparedMediaError("Prepared profile does not match its source and settings")
        identities = {row["profile_key"]: index for index, row in enumerate(bundle["outputs"], 1)}
        value = prepare(
            {
                "upload": "uploaded",
                "processing": "ready",
                "source": {"status": "inspected"},
                "plan": {"status": "planned", "config": plan_config(bundle, identities)},
                "outputs": [
                    {**row, "id": identities[row["profile_key"]]} for row in bundle["outputs"]
                ],
                "subtitles": [],
            }
        )
        if (
            value.status.processing is not ProcessingState.READY
            or value.status.thumbnail.status != "ready"
        ):
            raise PreparedMediaError("Prepared media is incomplete")
        return bundle
    except KeyError, TypeError, ValueError, ZeroDivisionError:
        raise PreparedMediaError("Invalid prepared media metadata") from None


class SqlPreparedMedia:
    def __init__(
        self, session: AsyncSession, objects: ObjectStorageClient, bucket: ObjectBucket
    ) -> None:
        self.session, self.objects, self.bucket = session, objects, bucket.name

    async def validate(self, directory: Path) -> None:
        validate_bundle(directory)

    async def export(self, actor: int, video_id: int, directory: Path) -> None:
        publication = await self.session.get(PublicationRow, video_id)
        if publication is None or publication.owner_id != actor:
            raise VideoNotFound
        prepared = await SqlProcessingViews(self.session).owned(video_id, actor)
        if (
            prepared is None
            or prepared.processing is not ProcessingState.READY
            or prepared.thumbnail.status != "ready"
        ):
            raise PreparedMediaError("Video is not completely prepared")
        upload = await self.session.scalar(
            select(UploadRow).where(UploadRow.video_id == video_id, UploadRow.status == "uploaded")
        )
        if upload is None:
            raise PreparedMediaError("Missing prepared upload")
        source = await self.session.get(SourceProcessingRow, upload.id)
        plan = await self.session.get(MediaPlanRow, upload.id)
        if source is None or plan is None or plan.config is None or source.content_id is None:
            raise PreparedMediaError("Missing prepared metadata")
        if await self.session.scalar(
            select(SubtitleLinkRow.upload_id).where(SubtitleLinkRow.upload_id == upload.id).limit(1)
        ):
            raise PreparedMediaError("Seed bundle v1 does not support embedded subtitles")
        choice = await self.session.get(ThumbnailChoiceRow, video_id)
        if choice is not None and (choice.custom_encoded_id or choice.requested_upload_id):
            raise PreparedMediaError("Seed bundle v1 requires generated previews")
        content = await self.session.get(MediaContentRow, source.content_id)
        if content is None:
            raise PreparedMediaError("Missing prepared content")
        rows = (
            await self.session.execute(
                select(SourceTrackRow.stream_index, MediaTrackRow)
                .join(MediaTrackRow, MediaTrackRow.id == SourceTrackRow.track_id)
                .where(SourceTrackRow.upload_id == upload.id)
            )
        ).all()
        tracks = [
            {
                "stream_index": index,
                "fingerprint": row.fingerprint,
                "properties": row.properties,
                "packet_count": row.packet_count,
            }
            for index, row in rows
        ]
        ids = set(
            (
                await self.session.scalars(
                    select(PlanTrackRow.encoded_id).where(PlanTrackRow.upload_id == upload.id)
                )
            ).all()
        )
        ids.add(cast(int, plan.config["thumbnail"]))
        outputs = (
            await self.session.scalars(
                select(EncodedTrackRow)
                .where(EncodedTrackRow.id.in_(ids))
                .order_by(EncodedTrackRow.id)
            )
        ).all()
        keys = {row.id: row.profile_key for row in outputs}
        config = copy.deepcopy(plan.config)
        for variant in cast(list[dict[str, Any]], config["variants"]):
            variant["video"] = keys[variant["video"]]
            if variant["audio"] is not None:
                variant["audio"] = keys[variant["audio"]]
        config["thumbnail"] = keys[cast(int, config["thumbnail"])]
        result: dict[str, Any] = {
            "version": 1,
            "source": {
                "size": upload.size,
                "inspection": source.inspection,
                "fingerprint": content.fingerprint,
                "manifest": content.manifest,
                "tracks": tracks,
            },
            "plan": config,
            "slot": choice.slot if choice else 0,
            "outputs": [],
        }
        directory.mkdir(parents=True, exist_ok=True)
        for row in outputs:
            if row.status != "ready" or row.artifact is None:
                raise PreparedMediaError("Unfinished prepared output")
            track = await self.session.get(MediaTrackRow, row.track_id)
            if track is None:
                raise PreparedMediaError("Missing prepared track")
            artifact = copy.deepcopy(row.artifact)
            original_prefix = str(artifact["prefix"])
            artifact["prefix"] = prepared_prefix(row.profile_key, artifact)
            for entry in cast(list[dict[str, Any]], artifact["files"]):
                destination = file_path(directory, row.profile_key, entry["name"])
                destination.parent.mkdir(exist_ok=True)
                response = await sdk(
                    lambda original_prefix=original_prefix, entry=entry: self.objects.get_object(
                        Bucket=self.bucket, Key=original_prefix + entry["name"]
                    )
                )
                with (
                    cast(StreamingBody, response["Body"]) as body,
                    destination.open("wb") as output,
                ):
                    while chunk := body.read(128 * 1024):
                        output.write(chunk)
            result["outputs"].append(
                {
                    "profile_key": row.profile_key,
                    "track_fingerprint": track.fingerprint,
                    "stream_index": row.stream_index,
                    "spec": row.spec,
                    "status": "ready",
                    "artifact": artifact,
                }
            )
        (directory / "bundle.json").write_text(
            json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        )
        validate_bundle(directory)

    async def completed_job(self, video_id: int) -> int:
        # Audit of the completed import, never a deferred or runnable job.
        value = await self.session.scalar(
            text(
                "INSERT INTO procrastinate_jobs (queue_name,task_name,args,status,attempts) "
                "VALUES ('media','video.import_prepared',"
                "jsonb_build_object('video_id',CAST(:id AS bigint)),'succeeded',1) RETURNING id"
            ),
            {"id": video_id},
        )
        if type(value) is not int:
            raise PreparedMediaError("Could not record prepared import")
        return value

    async def copy_package(self, directory: Path, output: dict[str, Any]) -> None:
        artifact, profile = output["artifact"], output["profile_key"]
        for entry in artifact["files"]:
            key = artifact["prefix"] + entry["name"]
            head: Mapping[str, Any]
            try:
                head = await sdk(
                    lambda key=key: self.objects.head_object(Bucket=self.bucket, Key=key),
                    allow=("404", "NoSuchKey", "NotFound"),
                )
            except ClientError:
                head = {}
            metadata = cast(Mapping[str, Any], head.get("Metadata", {}))
            if (
                head.get("ContentLength") == entry["size"]
                and metadata.get("sha256") == entry["sha256"]
            ):
                continue
            with file_path(directory, profile, entry["name"]).open("rb") as body:
                content_type = (
                    "application/vnd.apple.mpegurl"
                    if entry["name"].endswith(".m3u8")
                    else "image/webp"
                    if entry["name"].endswith(".webp")
                    else "audio/mp4"
                    if artifact["kind"] == "audio"
                    else "video/mp4"
                )
                await sdk(
                    lambda key=key, entry=entry, body=body, content_type=content_type: (
                        self.objects.put_object(
                            Bucket=self.bucket,
                            Key=key,
                            Body=body,
                            Metadata={"sha256": entry["sha256"]},
                            ContentLength=entry["size"],
                            ContentType=content_type,
                        )
                    )
                )

    async def import_video(
        self, actor: int, metadata: VideoMetadata, directory: Path, video_id: int | None = None
    ) -> VideoSettings:
        bundle = validate_bundle(directory)
        bundle_key = hashlib.sha256((directory / "bundle.json").read_bytes()).hexdigest()
        marker = f"prepared-import/{actor}/{bundle_key}"
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"), {"key": marker}
        )
        current = (
            await self.session.scalar(
                select(PublicationRow)
                .where(PublicationRow.id == video_id, PublicationRow.owner_id == actor)
                .with_for_update()
            )
            if video_id is not None
            else await self.session.scalar(
                select(PublicationRow)
                .join(UploadRow, UploadRow.video_id == PublicationRow.id)
                .where(UploadRow.object_key == marker, PublicationRow.owner_id == actor)
            )
        )
        if video_id is not None and current is None:
            raise VideoNotFound
        if current is not None:
            fingerprint = await self.session.scalar(
                select(MediaContentRow.fingerprint)
                .join(SourceProcessingRow, SourceProcessingRow.content_id == MediaContentRow.id)
                .join(UploadRow, UploadRow.id == SourceProcessingRow.upload_id)
                .where(UploadRow.video_id == current.id, UploadRow.status == "uploaded")
            )
            if fingerprint != bundle["source"]["fingerprint"]:
                raise PreparedMediaError("Existing publication does not match prepared media")
        for output in sorted(bundle["outputs"], key=lambda row: row["profile_key"]):
            await self.session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key,1))"),
                {"key": output["profile_key"]},
            )
            existing = await self.session.scalar(
                select(EncodedTrackRow).where(EncodedTrackRow.profile_key == output["profile_key"])
            )
            target = output
            if existing is not None:
                if (
                    existing.status != "ready"
                    or existing.spec != output["spec"]
                    or existing.artifact is None
                    or existing.artifact.get("files") != output["artifact"]["files"]
                ):
                    raise PreparedMediaError("Existing output conflicts with prepared import")
                target = {**output, "artifact": existing.artifact}
            await self.copy_package(directory, target)
        if current is not None:
            return settings_value(current)
        now = datetime.now(UTC)
        publication = PublicationRow(
            owner_id=actor,
            title=metadata.title,
            description=metadata.description,
            visibility=metadata.visibility,
            publication="published",
            upload="uploaded",
            processing="ready",
            added_at=now,
        )
        self.session.add(publication)
        await self.session.flush()
        upload = UploadRow(
            video_id=publication.id,
            object_key=marker,
            size=bundle["source"]["size"],
            status="uploaded",
            original_cleanup_started_at=now,
            original_deleted_at=now,
        )
        self.session.add(upload)
        await self.session.flush()
        source_data = bundle["source"]
        await self.session.execute(
            insert(MediaContentRow)
            .values(fingerprint=source_data["fingerprint"], manifest=source_data["manifest"])
            .on_conflict_do_nothing(index_elements=[MediaContentRow.fingerprint])
        )
        content_id = await self.session.scalar(
            select(MediaContentRow.id).where(
                MediaContentRow.fingerprint == source_data["fingerprint"]
            )
        )
        self.session.add(
            SourceProcessingRow(
                upload_id=upload.id,
                job_id=await self.completed_job(publication.id),
                content_id=content_id,
                status="inspected",
                inspection=source_data["inspection"],
                inspected_at=now,
            )
        )
        await self.session.flush()
        tracks: dict[str, int] = {}
        for track in sorted(source_data["tracks"], key=lambda row: row["fingerprint"]):
            await self.session.execute(
                insert(MediaTrackRow)
                .values(
                    fingerprint=track["fingerprint"],
                    properties=track["properties"],
                    packet_count=track["packet_count"],
                )
                .on_conflict_do_nothing(index_elements=[MediaTrackRow.fingerprint])
            )
            track_id = await self.session.scalar(
                select(MediaTrackRow.id).where(MediaTrackRow.fingerprint == track["fingerprint"])
            )
            if track_id is None:
                raise PreparedMediaError("Missing imported track")
            tracks[track["fingerprint"]] = track_id
            self.session.add(
                SourceTrackRow(
                    upload_id=upload.id, stream_index=track["stream_index"], track_id=track_id
                )
            )
        identities: dict[str, int] = {}
        for output in sorted(bundle["outputs"], key=lambda row: row["profile_key"]):
            profile = output["profile_key"]
            await self.session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key,1))"), {"key": profile}
            )
            row = await self.session.scalar(
                select(EncodedTrackRow).where(EncodedTrackRow.profile_key == profile)
            )
            if row is None:
                row = EncodedTrackRow(
                    track_id=tracks[output["track_fingerprint"]],
                    profile_key=profile,
                    spec=output["spec"],
                    source_upload_id=upload.id,
                    stream_index=output["stream_index"],
                    job_id=await self.completed_job(publication.id),
                    status="ready",
                    artifact=output["artifact"],
                )
                self.session.add(row)
                await self.session.flush()
            elif row.status != "ready" or row.spec != output["spec"]:
                raise PreparedMediaError("Existing output conflicts with prepared import")
            identities[profile] = row.id
        config = plan_config(bundle, identities)
        self.session.add(
            MediaPlanRow(
                upload_id=upload.id,
                job_id=await self.completed_job(publication.id),
                status="planned",
                config=config,
            )
        )
        await self.session.flush()
        for profile in identities:
            if profile != bundle["plan"]["thumbnail"]:
                self.session.add(PlanTrackRow(upload_id=upload.id, encoded_id=identities[profile]))
        self.session.add(ThumbnailChoiceRow(video_id=publication.id, slot=bundle["slot"]))
        await self.session.flush()
        prepared = await SqlProcessingViews(self.session).owned(publication.id, actor)
        if prepared is None or prepared.processing is not ProcessingState.READY:
            raise PreparedMediaError("Imported media did not become ready")
        return settings_value(publication)
