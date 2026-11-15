"""Public name queries never fetch credential, role or email columns."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from user.infrastructure.models import AccountRow


class SqlPublicNames:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, account_ids: tuple[int, ...]) -> dict[int, str]:
        rows = await self.session.execute(
            select(AccountRow.id, AccountRow.name).where(AccountRow.id.in_(account_ids))
        )
        return {identifier: name for identifier, name in rows}
