"""Request-local starsessions adapter; only live records may be renewed."""

from typing import cast

from redis.asyncio import Redis
from starsessions import SessionStore

ROTATE = """
if redis.call('EXISTS', KEYS[1]) == 0 then return 0 end
if redis.call('EXISTS', KEYS[2]) == 1 then return 0 end
redis.call('RENAME', KEYS[1], KEYS[2])
return 1
"""


class GuardedRedisStore(SessionStore):
    def __init__(self, connection: Redis, *, lifetime: int, prefix: str = "sessions.") -> None:
        if lifetime <= 0:
            raise ValueError("Guarded sessions require a positive lifetime")
        self._connection = connection
        self._lifetime = lifetime
        self._prefix = prefix
        self._read_id: str | None = None
        self._current_id: str | None = None
        self._invalidated = False
        self._removed: set[str] = set()
        self.cookie_allowed = True

    async def read(self, session_id: str, lifetime: int) -> bytes:
        self._read_id = session_id
        data = cast(bytes | None, await self._connection.get(self._prefix + session_id))
        self._current_id = session_id if data is not None else None
        self.cookie_allowed = data is not None
        return data if data is not None else b""

    async def write(self, session_id: str, data: bytes, lifetime: int, ttl: int) -> str:
        if (
            self._invalidated
            or (self._current_id is None and self._read_id is not None)
            or (self._current_id is not None and session_id != self._current_id)
        ):
            self._reject()
            return session_id
        accepted = await self._connection.set(
            self._prefix + session_id,
            data,
            ex=max(1, ttl),
            nx=self._current_id is None,
            xx=self._current_id is not None,
        )
        if accepted:
            self._current_id = session_id
            self.cookie_allowed = True
        else:
            self._reject()
        return session_id

    async def remove(self, session_id: str) -> None:
        if session_id in self._removed:
            return
        self._invalidated = True
        self.cookie_allowed = False
        removed = await self._connection.delete(self._prefix + session_id)
        self._removed.add(session_id)
        self._current_id = None
        self.cookie_allowed = bool(removed)

    async def rotate(self, new_id: str) -> bool:
        if self._invalidated or new_id == self._read_id:
            self._reject()
            return False
        if self._current_id is None:
            # Anonymous reservation; starsessions will serialize data at response start.
            accepted = await self._connection.set(
                self._prefix + new_id, b"", nx=True, ex=self._lifetime
            )
        else:
            accepted = await self._connection.eval(
                ROTATE, 2, self._prefix + self._current_id, self._prefix + new_id
            )
        if not accepted:
            self._reject()
            return False
        self._current_id = new_id
        self.cookie_allowed = True
        return True

    def _reject(self) -> None:
        self._invalidated = True
        self.cookie_allowed = False

    def discard(self) -> None:
        self._reject()
