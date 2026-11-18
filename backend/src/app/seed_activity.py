"""Repeatable demo interactions through the existing authenticated HTTP API."""

from collections.abc import Iterator
from typing import Any, Protocol, cast
from urllib.parse import urlencode


class ActivityAPI(Protocol):
    def call(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        allowed: tuple[int, ...] = (),
    ) -> dict[str, Any]: ...


class ActivityPerson(Protocol):
    @property
    def key(self) -> str: ...

    @property
    def name(self) -> str: ...

    @property
    def tag(self) -> str: ...


COMMENTS = (
    "Очень красивое место! Хочется пройти по этой тропе самому.",
    "Спасибо за спокойные кадры природы. Отличная пауза посреди рабочего дня.",
    "Какой мягкий свет! Добавлю эту прогулку в любимую подборку.",
    "Здорово передана атмосфера места. Смотрю и будто сам оказался рядом.",
    "Приятно иногда остановиться и просто посмотреть на природу.",
    "Красивые цвета и столько деталей. Обязательно пересмотрю на большом экране.",
)
PLAYLISTS = (
    ("public", "Любимые места", (0, 1, 3)),
    ("unlisted", "Поделиться природой", (2, 4, 5)),
    ("private", "Посмотреть вечером", (1, 3, 5)),
)
POSITIONS = (0.18, 0.42, 0.67, 0.86)


def page_items(
    api: ActivityAPI, path: str, *, allow_missing: bool = False
) -> Iterator[dict[str, Any]]:
    cursor = None
    while True:
        query = "?" + urlencode({"cursor": cursor}) if cursor is not None else ""
        page = api.call("GET", path + query, allowed=(404,) if allow_missing else ())
        if page.get("_status") == 404:
            return
        yield from page["items"]
        cursor = page["next_cursor"]
        if cursor is None:
            return


def public_durations(api: ActivityAPI, videos: tuple[int, ...]) -> dict[int, float]:
    """Read trusted durations without choosing other users' publications for seeding."""
    wanted = set(videos)
    durations: dict[int, float] = {}
    if not wanted:
        return durations
    for card in page_items(api, "/videos/feed"):
        video_id = cast(int, card["id"])
        if video_id in wanted:
            durations[video_id] = float(card["duration_seconds"])
            if wanted == durations.keys():
                break
    return durations


def populate_activity(
    api: ActivityAPI,
    person: ActivityPerson,
    index: int,
    videos: tuple[int, ...],
    channels: tuple[str, ...],
    durations: dict[int, float],
) -> None:
    """Fill deterministic examples, retaining existing progress and unrelated data."""
    start = (index * 3 + 3) % len(videos)
    targets = (videos[start:] + videos[:start])[:6]
    rotated = channels[index % len(channels) :] + channels[: index % len(channels)]
    others = tuple(tag for tag in rotated if tag != person.tag)
    for tag in others[: 2 + index % 2]:
        api.call("PUT", f"/channels/{tag}/subscription", {"subscribed": True})
    for offset, video_id in enumerate(targets):
        if video_id not in durations:
            continue
        base = f"/videos/{video_id}"
        if offset < len(POSITIONS):
            view = api.call("GET", base, allowed=(404,))
            if view.get("_status") == 404:
                continue
            if view["watch_progress"] is None:
                api.call(
                    "PUT",
                    base + "/progress",
                    {"position_seconds": round(durations[video_id] * POSITIONS[offset], 3)},
                    allowed=(404,),
                )
        if offset < 5:
            reaction = api.call("GET", base + "/like", allowed=(404,))
            if reaction.get("_status") != 404 and not reaction["disliked"]:
                api.call("PUT", base + "/like", {"liked": True}, allowed=(404,))
        if offset < 3:
            text = COMMENTS[(index + offset) % len(COMMENTS)]
            comments = page_items(api, base + "/comments", allow_missing=True)
            if not any(
                row["author_name"] == person.name and row["text"] == text for row in comments
            ):
                api.call("POST", base + "/comments", {"text": text}, allowed=(404,))
    playlists = list(page_items(api, "/playlists/mine"))
    for visibility, title, positions in PLAYLISTS:
        chosen = tuple(
            targets[position]
            for position in positions
            if position < len(targets) and targets[position] in durations
        )
        if not chosen:
            continue
        marker = f"[demo-activity-v1:{person.key}:{visibility}]"
        existing = next((row for row in playlists if marker in row["description"]), None)
        if existing is None:
            existing = api.call(
                "POST",
                "/playlists",
                {
                    "title": title,
                    "description": f"Демонстрационная подборка природных роликов. {marker}",
                    "visibility": visibility,
                    "video_id": chosen[0],
                },
                allowed=(404,),
            )
            if existing.get("_status") == 404:
                continue
        playlist_id = existing["id"]
        for video_id in chosen:
            api.call(
                "PUT",
                f"/playlists/{playlist_id}/videos/{video_id}",
                {"selected": True},
                allowed=(404,),
            )
