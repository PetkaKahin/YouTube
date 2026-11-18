"""Repeatable demo accounts and ready media imports without running media workers."""

import argparse
import asyncio
import fcntl
import hashlib
import json
import logging
import os
import sys
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from http.cookiejar import Cookie, CookieJar, DefaultCookiePolicy
from pathlib import Path
from typing import Any, cast
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, Request, build_opener

from dishka import AsyncContainer
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from app.di import create_container
from app.resources import create_cache
from app.settings import Settings
from user.public import AccountError, AccountNotFound, SetRole
from video.public import (
    CheckPreparedVideo,
    ExportPreparedVideo,
    ImportPreparedVideo,
    PreparedMediaError,
    VideoMetadata,
)

API = "http://nginx:8080/api/v1"
ROOT = Path("/var/lib/youtube/media/demo-seed")
ASSETS = Path("/app/seed-media")
# Public credentials of disposable demo accounts, never service configuration.
DEMO_PASSWORD = "Demo-YouTube-2026"  # noqa: S105


class SeedError(Exception):
    pass


@dataclass(frozen=True)
class Person:
    key: str
    name: str

    @property
    def email(self) -> str:
        return f"{self.key}.demo@example.com"

    @property
    def tag(self) -> str:
        return f"demo_{self.key}"


ADMIN = Person("admin", "Демо Администратор")

PEOPLE = (
    Person("alice", "Алиса Природа"),
    Person("bob", "Борис Путешествия"),
    Person("charlie", "Чарли Лесные тропы"),
    Person("diana", "Диана Дикие места"),
    ADMIN,
)


@dataclass(frozen=True)
class Clip:
    person: Person
    key: str
    title: str

    @property
    def marker(self) -> str:
        return f"nature-seed-v1:{self.key}"

    @property
    def description(self) -> str:
        page = (
            "https://www.nps.gov/deva/learn/photosmultimedia/b-roll-footage.htm"
            if self.key in {"mosaic-canyon", "panamint-mountains", "sand-dunes"}
            else "https://www.nps.gov/redw/learn/photosmultimedia/broll.htm"
        )
        return (
            f"Природный футаж [{self.marker}].\n"
            f"{self.title}. HD 720p.\n"
            f"Courtesy of the National Park Service.\n"
            f"Источник и условия свободного использования: {page}"
        )


CLIPS = tuple(
    Clip(PEOPLE[index // 3], key, title)
    for index, (key, title) in enumerate(
        (
            ("fern-canyon-1", "Каньон папоротников — прогулка по лесу"),
            ("fern-canyon-2", "Зелёные стены каньона Ферн"),
            ("forest-trail", "Тропа среди гигантских секвой"),
            ("lost-man-creek", "Лесной ручей Лост Мэн"),
            ("sun-in-canopy", "Солнечные лучи в кронах секвой"),
            ("wood-in-creek", "Вода и поваленные деревья"),
            ("redwood-tree", "Гигантская секвойя у ручья"),
            ("redwood-trunk", "От ручья к вершине секвойи"),
            ("prairie-creek", "Звуки природы у Прери Крик"),
            ("mosaic-canyon", "Мозаичный каньон — каменные узоры"),
            ("panamint-mountains", "Горные пейзажи Панаминт"),
            ("sand-dunes", "Песчаные дюны Долины Смерти"),
        )
    )
)


class State(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: StrictInt = 1
    accounts: dict[str, tuple[StrictInt, StrictInt]] = Field(default_factory=dict)
    videos: dict[str, StrictInt] = Field(default_factory=dict)


class InternalCookies(DefaultCookiePolicy):
    def return_ok_secure(self, cookie: Cookie, request: Request) -> bool:
        # The operator CLI talks only to Nginx on the trusted Compose network.
        return request.host == "nginx:8080" or super().return_ok_secure(cookie, request)


class SeedAPI:
    def __init__(self) -> None:
        self.cookies = CookieJar(policy=InternalCookies())
        self.opener = build_opener(HTTPCookieProcessor(self.cookies))

    def call(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        allowed: tuple[int, ...] = (),
    ) -> dict[str, Any]:
        if not path.startswith("/") or path.startswith("//"):
            raise SeedError("Invalid API path")
        headers = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if method != "GET":
            token = next(
                (cookie.value for cookie in self.cookies if cookie.name == "csrftoken"), None
            )
            if token is None:
                raise SeedError("API did not provide a CSRF cookie; start dev with make up")
            headers["X-CSRFToken"] = token
        request = Request(  # noqa: S310 -- fixed internal API, paths cannot select another host
            API + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers=headers,
            method=method,
        )
        try:
            with self.opener.open(request, timeout=30) as response:
                raw = response.read(1024 * 1024)
        except HTTPError as error:
            if error.code in allowed:
                error.close()
                return {"_status": error.code}
            status = error.code
            error.close()
            raise SeedError(f"API {method} {path}: HTTP {status}") from None
        except OSError, URLError:
            raise SeedError("Development API is unavailable; start it with make up") from None
        if not raw:
            return {}
        result: object = json.loads(raw)
        if not isinstance(result, dict):
            raise SeedError("Invalid API response")
        return cast(dict[str, Any], result)


def identifier(value: object) -> int:
    if type(value) is not int or not 0 < value < 2**63:
        raise SeedError("Invalid identifier in seed data")
    return value


def save_state(path: Path, state: State) -> None:
    temporary = path.with_suffix(".tmp")
    with temporary.open("w") as output:
        output.write(state.model_dump_json(indent=2))
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(path)


def read_state(path: Path) -> State:
    if not path.exists():
        return State()
    state = State.model_validate_json(path.read_text())
    if (
        state.version != 1
        or not state.videos.keys() <= {clip.key for clip in CLIPS}
        or not state.accounts.keys() <= {person.key for person in PEOPLE}
    ):
        raise SeedError("Unsupported seed manifest")
    for value in state.videos.values():
        identifier(value)
    for pair in state.accounts.values():
        for value in pair:
            identifier(value)
    return state


@contextmanager
def seed_lock(root: Path) -> Generator[None]:
    root.mkdir(parents=True, exist_ok=True)
    with (root / "seed.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SeedError("Another make seed is already running") from None
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def media_file(clip: Clip, assets: Path = ASSETS) -> Path:
    path = assets / clip.key / "bundle.json"
    try:
        manifest = json.loads((assets / "manifest.json").read_text())
        if manifest.get("version") != 2 or path.is_symlink() or path.parent.is_symlink():
            raise SeedError("Invalid prepared seed manifest")
        expected = manifest["clips"][clip.key]["bundle_sha256"]
        with path.open("rb") as content:
            actual = hashlib.file_digest(content, "sha256").hexdigest()
    except OSError, KeyError, ValueError, TypeError:
        raise SeedError("Prepared seed media is missing or invalid; rebuild the image") from None
    if actual != expected:
        raise SeedError(f"Prepared clip checksum mismatch: {clip.key}")
    return path.parent


def login_person(api: SeedAPI, person: Person, state: State, state_path: Path) -> int:
    api.call("GET", "/channels/" + person.tag, allowed=(404,))
    registered = api.call(
        "POST",
        "/auth/register",
        {
            "name": person.name,
            "email": person.email,
            "tag": person.tag,
            "password": DEMO_PASSWORD,
            "password_confirmation": DEMO_PASSWORD,
        },
        allowed=(409,),
    )
    if registered.get("_status") != 409:
        state.accounts[person.key] = (
            identifier(registered["account_id"]),
            identifier(registered["channel_id"]),
        )
        save_state(state_path, state)
    elif person.key not in state.accounts:
        raise SeedError("Demo email/tag is already in use without a seed manifest")
    account = api.call("POST", "/auth/login", {"email": person.email, "password": DEMO_PASSWORD})
    channel = api.call("GET", "/channels/" + person.tag)
    if (
        account.get("name") != person.name
        or channel.get("name") != person.name
        or (account["id"], channel["id"]) != state.accounts[person.key]
    ):
        raise SeedError("Demo account conflicts with existing data")
    # A duplicate email/tag is never resolved by resetting someone else's password or role.
    return identifier(account["id"])


async def ensure_video(
    api: SeedAPI, clip: Clip, actor: int, state: State, state_path: Path, app: AsyncContainer
) -> int:
    video_id = state.videos.get(clip.key)
    if video_id is not None:
        current = api.call("GET", f"/videos/{video_id}/settings", allowed=(404,))
        if current.get("_status") == 404:
            video_id = None
        elif clip.marker not in str(current.get("description", "")):
            raise SeedError("Seed manifest refers to a different video; no data was changed")
    async with app() as operation:
        result = await (await operation.get(ImportPreparedVideo))(
            actor, VideoMetadata(clip.title, clip.description, "public"), media_file(clip), video_id
        )
    state.videos[clip.key] = result.id
    save_state(state_path, state)
    return result.id


async def populate(root: Path, *, export_ready: bool) -> None:
    namespace = hashlib.sha256(os.environ.get("POSTGRES_DB", "youtube").encode()).hexdigest()[:16]
    settings = Settings()
    app = create_container(settings, create_cache(settings, ""), require_cache=False)
    try:
        with seed_lock(root):
            state_path = root / f"state-{namespace}.json"
            state = read_state(state_path)
            if not export_ready:
                for clip in CLIPS:
                    async with app() as operation:
                        await (await operation.get(CheckPreparedVideo))(media_file(clip))
            api = SeedAPI()
            for person in PEOPLE:
                actor = login_person(api, person, state, state_path)
                try:
                    if person == ADMIN:
                        async with app() as operation:
                            await (await operation.get(SetRole))(person.email, "admin")
                    for clip in CLIPS:
                        if clip.person != person:
                            continue
                        if export_ready:
                            if clip.key not in state.videos:
                                raise SeedError("No prepared publication to export")
                            async with app() as operation:
                                await (await operation.get(ExportPreparedVideo))(
                                    actor, state.videos[clip.key], ASSETS / clip.key
                                )
                            print(f"Сохранён HLS-пакет: {clip.key}", flush=True)
                        else:
                            video_id = await ensure_video(api, clip, actor, state, state_path, app)
                            print(f"Готово без кодирования: /watch?v={video_id}", flush=True)
                finally:
                    api.call("POST", "/auth/logout")
            if export_ready:
                old = json.loads((ASSETS / "manifest.json").read_text())
                previous = old["clips"] if old.get("version") == 2 else old
                clips = {}
                for clip in CLIPS:
                    record = dict(previous.get(clip.key, previous.get(clip.key + ".mp4", {})))
                    record["bundle_sha256"] = hashlib.sha256(
                        (ASSETS / clip.key / "bundle.json").read_bytes()
                    ).hexdigest()
                    record["bundle"] = clip.key + "/bundle.json"
                    clips[clip.key] = record
                temporary = ASSETS / "manifest.tmp"
                temporary.write_text(
                    json.dumps({"version": 2, "clips": clips}, ensure_ascii=False, indent=2) + "\n"
                )
                temporary.replace(ASSETS / "manifest.json")
                for clip in CLIPS:
                    (ASSETS / f"{clip.key}.mp4").unlink(missing_ok=True)
                print("Готовые пакеты сохранены, исходные MP4 удалены.")
            else:
                print(
                    f"Готово: {len(PEOPLE)} аккаунтов с каналами, включая администратора, "
                    f"{len(CLIPS)} готовых видео."
                )
                for person in PEOPLE:
                    role = "  [admin]" if person == ADMIN else ""
                    print(f"  {person.email}  /@{person.tag}{role}")
                print(f"Учебный пароль всех аккаунтов: {DEMO_PASSWORD}")
    finally:
        await app.close()


def run_seed(root: Path = ROOT, *, export_ready: bool = False) -> None:
    if not export_ready:
        for clip in CLIPS:
            media_file(clip)
    asyncio.run(populate(root, export_ready=export_ready))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create demo accounts and import prepared nature videos"
    )
    parser.add_argument(
        "--export-ready",
        action="store_true",
        help="Export already rendered videos and replace source MP4s",
    )
    args = parser.parse_args()
    logging.disable(logging.INFO)
    try:
        run_seed(export_ready=args.export_ready)
    except (
        SeedError,
        PreparedMediaError,
        AccountError,
        AccountNotFound,
        OSError,
        ValueError,
        KeyError,
    ) as error:
        message = (
            str(error)
            if isinstance(error, (SeedError, PreparedMediaError))
            else "Invalid seed state or response"
        )
        sys.exit(f"Seed: {message}")


if __name__ == "__main__":
    main()
