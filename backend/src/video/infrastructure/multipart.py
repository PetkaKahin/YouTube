"""S3 adapter: private server keys, bounded signed writes, no media work in HTTP."""

from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from anyio import to_thread
from botocore.exceptions import BotoCoreError, ClientError

from common.contracts.storage import ObjectBucket, ObjectStorageClient
from video.domain.uploads import (
    URL_SECONDS,
    StorageUnavailable,
    StoredObject,
    StoredPart,
    Transfer,
    UploadError,
)


async def sdk[T](call: Callable[[], T], *, allow: tuple[str, ...] = ()) -> T:
    try:
        return await to_thread.run_sync(call)
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") in allow:
            raise
        raise StorageUnavailable from None
    except BotoCoreError, OSError:
        raise StorageUnavailable from None


class S3Multipart:
    def __init__(self, objects: ObjectStorageClient, bucket: ObjectBucket) -> None:
        self.objects, self.bucket = objects, bucket.name

    def args(self, transfer: Transfer) -> dict[str, Any]:
        return {"Bucket": self.bucket, "Key": transfer.object_key}

    async def existing(self, transfer: Transfer) -> tuple[str, ...]:
        result: list[str] = []
        markers: dict[str, Any] = {}
        while True:
            page = await sdk(
                lambda markers=markers: self.objects.list_multipart_uploads(
                    Bucket=self.bucket, Prefix=transfer.object_key, **markers
                )
            )
            result.extend(
                upload["UploadId"]
                for upload in page.get("Uploads", [])
                if upload["Key"] == transfer.object_key
            )
            if not page.get("IsTruncated"):
                return tuple(sorted(result))
            markers = {
                "KeyMarker": page["NextKeyMarker"],
                "UploadIdMarker": page["NextUploadIdMarker"],
            }

    async def initiate(self, transfer: Transfer) -> str:
        existing = await self.existing(transfer)
        if existing:
            # A previous create succeeded before its response/DB commit was lost.
            for extra in existing[1:]:
                await self.abort_id(transfer, extra)
            return existing[0]
        result = await sdk(
            lambda: self.objects.create_multipart_upload(
                **self.args(transfer), ContentType="application/octet-stream"
            )
        )
        return str(result["UploadId"])

    async def parts(self, transfer: Transfer) -> tuple[StoredPart, ...]:
        if transfer.multipart_id is None:
            return ()
        result: list[StoredPart] = []
        marker = 0
        while True:
            try:
                page = await sdk(
                    lambda marker=marker: self.objects.list_parts(
                        **self.args(transfer),
                        UploadId=transfer.multipart_id,
                        PartNumberMarker=marker,
                    ),
                    allow=("NoSuchUpload",),
                )
            except ClientError as error:
                if error.response.get("Error", {}).get("Code") == "NoSuchUpload":
                    raise UploadError("upload_missing") from None
                raise StorageUnavailable from None
            result.extend(
                StoredPart(p["PartNumber"], p["Size"], p["ETag"], p["LastModified"])
                for p in page.get("Parts", [])
            )
            if not page.get("IsTruncated"):
                return tuple(result)
            marker = page["NextPartNumberMarker"]

    async def head(self, transfer: Transfer) -> StoredObject | None:
        try:
            value = await sdk(
                lambda: self.objects.head_object(**self.args(transfer)),
                allow=("404", "NoSuchKey", "NotFound"),
            )
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise StorageUnavailable from None
        return StoredObject(value["ContentLength"], value["LastModified"])

    async def sign(self, transfer: Transfer, number: int) -> str:
        url = await sdk(
            lambda: self.objects.generate_presigned_url(
                "upload_part",
                Params={
                    **self.args(transfer),
                    "UploadId": transfer.multipart_id,
                    "PartNumber": number,
                    "ContentLength": transfer.part_size(number),
                },
                ExpiresIn=URL_SECONDS,
            )
        )
        parsed = urlsplit(url)
        # Nginx forwards this same path/query with the exact signed upstream Host.
        return "/uploads" + parsed.path + "?" + parsed.query

    async def complete(self, transfer: Transfer, parts: tuple[StoredPart, ...]) -> None:
        try:
            await sdk(
                lambda: self.objects.complete_multipart_upload(
                    **self.args(transfer),
                    UploadId=transfer.multipart_id,
                    MultipartUpload={
                        "Parts": [
                            {"PartNumber": p.number, "ETag": p.etag}
                            for p in sorted(parts, key=lambda p: p.number)
                        ]
                    },
                ),
                allow=("InvalidPart", "InvalidPartOrder", "EntityTooSmall", "NoSuchUpload"),
            )
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") in {
                "InvalidPart",
                "InvalidPartOrder",
                "EntityTooSmall",
                "NoSuchUpload",
            }:
                raise UploadError("incomplete_upload") from None
            raise StorageUnavailable from None

    async def abort_id(self, transfer: Transfer, multipart_id: str) -> None:
        try:
            await sdk(
                lambda: self.objects.abort_multipart_upload(
                    **self.args(transfer), UploadId=multipart_id
                ),
                allow=("NoSuchUpload",),
            )
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") != "NoSuchUpload":
                raise StorageUnavailable from None

    async def discard(self, transfer: Transfer) -> None:
        for multipart_id in await self.existing(transfer):
            await self.abort_id(transfer, multipart_id)
        await sdk(lambda: self.objects.delete_object(**self.args(transfer)))
