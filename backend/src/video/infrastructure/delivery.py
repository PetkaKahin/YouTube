"""Rebuild safe playlists and sign internal storage requests without reading media bytes."""

import re
from typing import cast
from urllib.parse import urlsplit

from common.contracts.storage import ObjectBucket, ObjectStorageClient
from video.domain.delivery import MediaDelivery, MediaPackage, StoredMedia
from video.domain.uploads import URL_SECONDS, StorageUnavailable
from video.infrastructure.multipart import sdk
from video.infrastructure.readiness import (
    media_track,
    number,
    object_value,
    sequence,
    valid_artifact_prefix,
)


def package(output: dict[str, object]) -> MediaPackage:
    artifact = object_value(output["artifact"])
    kind = str(artifact["kind"])
    track = media_track(output, kind)
    if kind not in {"video", "audio", "subtitle"}:
        raise ValueError("Unsupported delivery package")
    prefix = str(artifact["prefix"])
    media_type = (
        "text/vtt; charset=utf-8"
        if kind == "subtitle"
        else "audio/mp4"
        if kind == "audio"
        else "video/mp4"
    )
    files = {
        str(item["name"]): StoredMedia(prefix + str(item["name"]), number(item["size"]), media_type)
        for raw in sequence(artifact["files"])
        if (item := object_value(raw))["name"] != "index.m3u8"
    }
    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:7",
        f"#EXT-X-TARGETDURATION:{track.target_duration}",
        "#EXT-X-MEDIA-SEQUENCE:0",
        "#EXT-X-PLAYLIST-TYPE:VOD",
    ]
    if kind != "subtitle":
        lines.append('#EXT-X-MAP:URI="init.mp4"')
    extension = "vtt" if kind == "subtitle" else "m4s"
    for index, segment in enumerate(track.segments):
        lines.extend(
            (f"#EXTINF:{float(segment.duration):.6f},", f"segment_{index:06d}.{extension}")
        )
    lines.append("#EXT-X-ENDLIST")
    return MediaPackage("\n".join(lines) + "\n", files)


def selected_preview(
    snapshot: dict[str, object], cover: object, slot: object
) -> StoredMedia | None:
    try:
        if cover is not None:
            output = object_value(cover)
            name = "image.webp"
        else:
            config = object_value(object_value(snapshot["plan"])["config"])
            output = next(
                object_value(item)
                for item in sequence(snapshot["outputs"] or [])
                if object_value(item)["id"] == config.get("thumbnail")
            )
            names = sequence(object_value(output["artifact"])["candidates"])
            selected = slot if type(slot) is int and 0 <= slot <= 2 else 0
            name = str(names[selected])
        if output["status"] != "ready":
            return None
        artifact = object_value(output["artifact"])
        prefix = str(artifact["prefix"])
        if not valid_artifact_prefix(output):
            return None
        entry = next(
            object_value(item)
            for item in sequence(artifact["files"])
            if object_value(item)["name"] == name
        )
        return StoredMedia(prefix + name, number(entry["size"]), "image/webp")
    except KeyError, ValueError, TypeError, IndexError, StopIteration:
        return None


def delivery_manifest(
    snapshot: dict[str, object], master: str | None, cover: object, slot: object
) -> MediaDelivery | None:
    if master is None:
        return None
    config = object_value(object_value(snapshot["plan"])["config"])
    aliases: dict[int, str] = {}
    video_indexes: dict[int, int] = {}
    for index, raw in enumerate(sequence(config["variants"]), 1):
        variant = object_value(raw)
        video_id = number(variant["video"])
        video_indexes[video_id] = index
        aliases.setdefault(video_id, f"v{index}")
        if variant["audio"] is not None:
            aliases.setdefault(number(variant["audio"]), f"a{index}")
    links = [object_value(item) for item in sequence(snapshot["subtitles"] or [])]
    for link in sorted(
        links,
        key=lambda item: (
            video_indexes.get(cast(int, item["video_encoded_id"]), 0),
            cast(int, item["stream_index"]),
        ),
    ):
        if type(link["encoded_id"]) is int and cast(int, link["video_encoded_id"]) in video_indexes:
            aliases.setdefault(
                number(link["encoded_id"]),
                f"s{video_indexes[cast(int, link['video_encoded_id'])]}-{link['stream_index']}",
            )
    outputs = {
        number(item["id"]): item
        for raw in sequence(snapshot["outputs"])
        if (item := object_value(raw))
    }
    pattern = r"tracks/([1-9][0-9]*)/index\.m3u8"
    ids = {int(item) for item in re.findall(pattern, master)}
    packages = {aliases[encoded_id]: package(outputs[encoded_id]) for encoded_id in ids}
    renamed = re.sub(pattern, lambda match: f"tracks/{aliases[int(match[1])]}/index.m3u8", master)
    return MediaDelivery(renamed, packages, selected_preview(snapshot, cover, slot))


class S3MediaRedirects:
    def __init__(self, objects: ObjectStorageClient, bucket: ObjectBucket) -> None:
        self.objects, self.bucket = objects, bucket.name

    async def sign(self, media: StoredMedia, method: str) -> str:
        if method not in {"GET", "HEAD"}:
            raise StorageUnavailable
        signed = await sdk(
            lambda: self.objects.generate_presigned_url(
                "head_object" if method == "HEAD" else "get_object",
                Params={"Bucket": self.bucket, "Key": media.key},
                ExpiresIn=URL_SECONDS,
                HttpMethod=method,
            )
        )
        parsed = urlsplit(signed)
        if (
            parsed.scheme != "http"
            or parsed.netloc != "rustfs:9000"
            or parsed.fragment
            or parsed.path != f"/{self.bucket}/{media.key}"
            or not parsed.query
        ):
            raise StorageUnavailable
        return "/_media_storage" + parsed.path + "?" + parsed.query
