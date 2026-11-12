"""Secret loading must fail closed and keep values out of diagnostics."""

import os
from pathlib import Path

import pytest

from youtube.bootstrap.secrets import ApplicationSecrets, read_secret

NAMES = ("POSTGRES_PASSWORD", "VALKEY_PASSWORD", "RUSTFS_ACCESS_KEY", "RUSTFS_SECRET_KEY")


@pytest.fixture
def credentials(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, str]:
    values = {}
    for index, name in enumerate(NAMES):
        value = f"value-for-{index}"
        path = tmp_path / name.lower()
        path.write_text(value + "\n", encoding="utf-8")
        monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv(f"{name}_FILE", str(path))
        values[name] = value
    return values


def test_loads_files_without_exporting_values(credentials: dict[str, str]) -> None:
    configuration = ApplicationSecrets.load()
    assert configuration.postgres_password == credentials["POSTGRES_PASSWORD"]
    assert configuration.valkey_password == credentials["VALKEY_PASSWORD"]
    assert configuration.rustfs_access_key == credentials["RUSTFS_ACCESS_KEY"]
    assert configuration.rustfs_secret_key == credentials["RUSTFS_SECRET_KEY"]
    assert all(name not in os.environ for name in NAMES)
    assert all(value not in repr(configuration) for value in credentials.values())


def test_rejects_environment_values(
    credentials: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    value = credentials["POSTGRES_PASSWORD"]
    monkeypatch.setenv("POSTGRES_PASSWORD", value)
    with pytest.raises(RuntimeError, match="_FILE") as error:
        read_secret("POSTGRES_PASSWORD")
    assert value not in str(error.value)


def test_rejects_missing_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    monkeypatch.delenv("POSTGRES_PASSWORD_FILE", raising=False)
    with pytest.raises(RuntimeError, match="Missing secret file setting"):
        read_secret("POSTGRES_PASSWORD")


def test_rejects_missing_file(credentials: dict[str, str]) -> None:
    Path(os.environ["POSTGRES_PASSWORD_FILE"]).unlink()
    with pytest.raises(RuntimeError, match="Cannot read secret file"):
        read_secret("POSTGRES_PASSWORD")


@pytest.mark.parametrize("content", [b"", b"\n", b"one\ntwo\n", b"value\r\n", b"\0", b"\xff"])
def test_rejects_invalid_file_without_echoing_contents(
    credentials: dict[str, str], content: bytes
) -> None:
    Path(os.environ["POSTGRES_PASSWORD_FILE"]).write_bytes(content)
    with pytest.raises(RuntimeError) as error:
        read_secret("POSTGRES_PASSWORD")
    assert str(error.value).startswith(("Secret file for", "Cannot read secret file"))


def test_accepts_file_without_trailing_newline(credentials: dict[str, str]) -> None:
    value = credentials["POSTGRES_PASSWORD"]
    Path(os.environ["POSTGRES_PASSWORD_FILE"]).write_text(value, encoding="utf-8")
    assert read_secret("POSTGRES_PASSWORD") == value
