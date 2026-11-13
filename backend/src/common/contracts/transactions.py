"""An application-owned transaction, independent of storage libraries."""

from contextlib import AbstractAsyncContextManager
from typing import Protocol


class Transactions(Protocol):
    def begin(self) -> AbstractAsyncContextManager[None]: ...
