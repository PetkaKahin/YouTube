"""Read application credentials from Compose Secret files without logging values."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Self


def read_secret(name: str) -> str:
    if name in os.environ:
        raise RuntimeError(f"Secret {name} must be provided through its _FILE setting")
    filename = os.environ.get(f"{name}_FILE")
    if not filename:
        raise RuntimeError(f"Missing secret file setting for {name}")
    try:
        value = Path(filename).read_bytes().decode("utf-8").removesuffix("\n")
    except OSError, UnicodeError:
        raise RuntimeError(f"Cannot read secret file for {name}") from None
    if not value or any(character in value for character in "\r\n\0"):
        raise RuntimeError(f"Secret file for {name} must contain one nonempty UTF-8 line")
    return value


@dataclass(frozen=True)
class ApplicationSecrets:
    postgres_password: str = field(repr=False)
    valkey_password: str = field(repr=False)
    rustfs_access_key: str = field(repr=False)
    rustfs_secret_key: str = field(repr=False)
    csrf_secret: str = field(repr=False)

    @classmethod
    def load(cls) -> Self:
        return cls(
            postgres_password=read_secret("POSTGRES_PASSWORD"),
            valkey_password=read_secret("VALKEY_PASSWORD"),
            rustfs_access_key=read_secret("RUSTFS_ACCESS_KEY"),
            rustfs_secret_key=read_secret("RUSTFS_SECRET_KEY"),
            csrf_secret=read_secret("CSRF_SECRET"),
        )


@dataclass(frozen=True)
class StorageSecrets:
    postgres_password: str = field(repr=False)
    rustfs_access_key: str = field(repr=False)
    rustfs_secret_key: str = field(repr=False)

    @classmethod
    def load(cls) -> Self:
        return cls(
            postgres_password=read_secret("POSTGRES_PASSWORD"),
            rustfs_access_key=read_secret("RUSTFS_ACCESS_KEY"),
            rustfs_secret_key=read_secret("RUSTFS_SECRET_KEY"),
        )
