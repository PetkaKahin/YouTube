"""Minimal SDK capabilities needed by application resource management."""

from typing import Protocol


class ObjectStorageClient(Protocol):
    def head_bucket(self, *, Bucket: str) -> object:
        """Check bucket availability without exposing SDK response types."""
        ...

    def close(self) -> None: ...
