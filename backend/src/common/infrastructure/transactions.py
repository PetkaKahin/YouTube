"""SQLAlchemy implementation shared by adapters in one request/CLI operation."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession


class SqlTransactions:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @asynccontextmanager
    async def begin(self) -> AsyncGenerator[None]:
        async with self.session.begin():
            yield
