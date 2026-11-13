"""Video's identity port uses User.public, without querying account tables."""

from user.public import AccountNotFound, GetAccount
from video.domain.publications import AuthenticationRequired


class AccountActors:
    def __init__(self, get_account: GetAccount) -> None:
        self.get_account = get_account

    async def require(self, account_id: int) -> None:
        try:
            await self.get_account(account_id)
        except AccountNotFound:
            raise AuthenticationRequired from None
