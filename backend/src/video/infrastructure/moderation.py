"""Staff primitives bypass ordinary visibility only inside report orchestration."""

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from user.public import AccountNotFound, GetStaffIdentity, StaffForbidden
from video.domain.access import ViewingSnapshot
from video.domain.publications import AuthenticationRequired, VideoNotFound
from video.infrastructure.access import VIEW_SELECT, viewing_snapshot
from video.infrastructure.models import PublicationRow

REVIEW_SNAPSHOT = text(VIEW_SELECT + " WHERE p.id=:video_id AND p.publication='published'")


class AccountVideoStaff:
    def __init__(self, staff: GetStaffIdentity) -> None:
        self.staff = staff

    async def require(self, account_id: int) -> None:
        try:
            await self.staff(account_id)
        except AccountNotFound:
            raise AuthenticationRequired from None
        except StaffForbidden:
            raise VideoNotFound from None


class SqlModerationViews:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def lock(self, video_id: int, *, exclusive: bool = False) -> bool:
        return (
            await self.session.scalar(
                select(PublicationRow.id)
                .where(PublicationRow.id == video_id)
                .with_for_update(read=not exclusive)
            )
            is not None
        )

    async def snapshot(self, video_id: int, *, delivery: bool = False) -> ViewingSnapshot | None:
        row = (
            (await self.session.execute(REVIEW_SNAPSHOT, {"video_id": video_id, "email": None}))
            .mappings()
            .one_or_none()
        )
        return viewing_snapshot(row, delivery=delivery) if row is not None else None

    async def hide(self, video_id: int) -> None:
        await self.session.execute(
            update(PublicationRow)
            .where(PublicationRow.id == video_id, PublicationRow.moderation != "hidden")
            .values(moderation="hidden")
        )
