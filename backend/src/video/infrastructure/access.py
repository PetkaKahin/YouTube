"""Email-addressed grants and a fresh, owner-safe PostgreSQL viewing snapshot."""

from datetime import datetime
from typing import cast

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession

from user.public import AccountError, AccountNotFound, GetAccount, NormalizeEmail
from video.domain.access import ReadyQuality, VideoView, ViewConditions, Viewer, ViewingSnapshot
from video.domain.permissions import SIZE, PermissionGrant
from video.domain.publications import AuthenticationRequired, VideoError
from video.infrastructure.delivery import delivery_manifest
from video.infrastructure.models import PrivateGrantRow, PublicationRow
from video.infrastructure.readiness import MEDIA_JOINS, MEDIA_SNAPSHOT, object_value, prepare


class AccountViewers:
    def __init__(self, get_account: GetAccount) -> None:
        self.get_account = get_account

    async def get(self, account_id: int) -> Viewer:
        try:
            account = await self.get_account(account_id)
        except AccountNotFound:
            raise AuthenticationRequired from None
        return Viewer(account.id, account.email)


class AccountPermissionEmails:
    def __init__(self, normalize_email: NormalizeEmail) -> None:
        self.normalize_email = normalize_email

    def normalize(self, email: str) -> str:
        try:
            return self.normalize_email(email)
        except AccountError:
            raise VideoError("email", "invalid_email") from None


class SqlPrivateGrants:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def lock_owned(self, video_id: int, owner_id: int) -> bool:
        return (
            await self.session.scalar(
                select(PublicationRow.id)
                .where(PublicationRow.id == video_id, PublicationRow.owner_id == owner_id)
                .with_for_update()
            )
            is not None
        )

    async def page(self, video_id: int, before: int | None) -> tuple[PermissionGrant, ...]:
        statement = select(PrivateGrantRow.id, PrivateGrantRow.email).where(
            PrivateGrantRow.video_id == video_id
        )
        if before is not None:
            statement = statement.where(PrivateGrantRow.id < before)
        rows = (
            await self.session.execute(
                statement.order_by(PrivateGrantRow.id.desc()).limit(SIZE + 1)
            )
        ).all()
        return tuple(PermissionGrant(identity, email) for identity, email in rows)

    async def set(self, video_id: int, email: str, allowed: bool) -> None:
        if allowed:
            changed = await self.session.scalar(
                insert(PrivateGrantRow)
                .values(video_id=video_id, email=email)
                .on_conflict_do_nothing(
                    index_elements=[PrivateGrantRow.video_id, PrivateGrantRow.email]
                )
                .returning(PrivateGrantRow.email)
            )
        else:
            changed = await self.session.scalar(
                delete(PrivateGrantRow)
                .where(PrivateGrantRow.video_id == video_id, PrivateGrantRow.email == email)
                .returning(PrivateGrantRow.email)
            )
        if changed is not None:
            await self.session.execute(
                update(PublicationRow)
                .where(PublicationRow.id == video_id)
                .values(updated_at=func.clock_timestamp())
            )


PERMITTED = "EXISTS (SELECT 1 FROM video_private_grants g WHERE g.video_id=p.id AND g.email=:email)"
# All interpolated SQL fragments are module constants. IDs and email are bound.
PREVIEW_JOINS = """
LEFT JOIN video_thumbnail_choices c ON c.video_id=p.id
LEFT JOIN video_thumbnail_uploads ci ON ci.id=c.requested_upload_id AND ci.video_id=p.id
  AND ci.generation=c.generation AND ci.selection_confirmed
LEFT JOIN video_encoded_tracks new_cover ON new_cover.id=ci.encoded_id
  AND new_cover.status='ready' AND new_cover.spec->>'kind'='thumbnail_image'
LEFT JOIN video_encoded_tracks old_cover ON old_cover.id=c.custom_encoded_id
  AND old_cover.status='ready' AND old_cover.spec->>'kind'='thumbnail_image'
"""
VIEW_SELECT = f"""
SELECT p.id,p.owner_id,p.title,p.description,p.visibility,p.publication,p.moderation,
       p.added_at,p.updated_at, {MEDIA_SNAPSHOT} AS media, {PERMITTED} AS permitted,
       coalesce(to_jsonb(new_cover),to_jsonb(old_cover)) AS cover, c.slot AS cover_slot
FROM video_publications p {MEDIA_JOINS} {PREVIEW_JOINS}
"""  # noqa: S608 -- module constants only; every input is bound
VIEW_SNAPSHOT = text(
    VIEW_SELECT
    + f"""
WHERE p.id=:video_id AND p.publication='published' AND p.moderation='visible'
  AND (p.visibility='public' OR p.owner_id=:account_id OR {PERMITTED})
"""
)  # noqa: S608 -- constant SQL fragments; every input is bound


def viewing_snapshot(result: RowMapping, *, delivery: bool = False) -> ViewingSnapshot:
    media = object_value(result["media"])
    prepared = prepare(media)
    processing = prepared.status
    qualities = tuple(
        ReadyQuality(item.quality, item.range) for item in processing.available_qualities
    )
    return ViewingSnapshot(
        ViewConditions(
            cast(int, result["owner_id"]),
            result["publication"] == "published",
            result["moderation"] == "visible",
            bool(qualities),
            result["visibility"] == "public",
            bool(result["permitted"]),
        ),
        VideoView(
            cast(int, result["id"]),
            str(result["title"]),
            str(result["description"]),
            str(result["visibility"]),
            cast(datetime | None, result["added_at"]),
            cast(datetime, result["updated_at"]),
            qualities,
            processing.hd,
            processing.uhd,
            processing.hdr,
            processing.uhd_hdr,
        ),
        delivery_manifest(media, prepared.master, result["cover"], result["cover_slot"])
        if delivery
        else None,
    )


class SqlVideoViews:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def snapshot(
        self, video_id: int, viewer: Viewer, *, delivery: bool = False
    ) -> ViewingSnapshot | None:
        result = (
            (
                await self.session.execute(
                    VIEW_SNAPSHOT,
                    {"video_id": video_id, "account_id": viewer.account_id, "email": viewer.email},
                )
            )
            .mappings()
            .one_or_none()
        )
        if result is None:
            return None
        return viewing_snapshot(result, delivery=delivery)
