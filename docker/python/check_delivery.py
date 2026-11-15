"""Qualify actual gateway bytes and live permissions without pytest or production data."""

import asyncio
import json
import re
import sys
import traceback
from http.cookiejar import CookieJar
from http.client import IncompleteRead
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urljoin
from urllib.request import HTTPCookieProcessor, Request, build_opener

import uvicorn
from sqlalchemy import text

from app.main import create_app
from app.settings import Settings
from check_thumbnails import qualify
from channel.public import GetPublicChannels
from user.public import GetAccount
from video.public import GetMedia
from video.infrastructure.delivery import S3MediaRedirects


class Browser:
    def __init__(self, gateway):
        self.gateway = gateway
        self.cookies = CookieJar()
        self.http = build_opener(HTTPCookieProcessor(self.cookies))

    def request(self, path, *, method="GET", data=None, headers=None):
        extra = dict(headers or {})
        if data is not None:
            extra["Content-Type"] = "application/json"
        if method not in {"GET", "HEAD"}:
            extra["x-csrftoken"] = next(c.value for c in self.cookies if c.name == "csrftoken")
        request = Request(
            urljoin(self.gateway, path),
            method=method,
            headers=extra,
            data=None if data is None else json.dumps(data).encode(),
        )
        try:
            response = self.http.open(request, timeout=30)
        except HTTPError as error:
            response = error
        with response:
            return (
                response.code,
                {k.lower(): v for k, v in response.headers.items()},
                response.read(),
            )

    def login(self, email):
        assert self.request("/api/docs")[0] == 200
        assert (
            self.request(
                "/api/v1/auth/login", method="POST", data={"email": email, "password": "abcdef"}
            )[0]
            == 200
        )


def safe(response, status=200):
    code, headers, body = response
    assert code == status, "Unexpected gateway status"
    assert headers.get("cache-control") == "no-store"
    assert "cookie" in headers.get("vary", "").lower()
    assert not any(name in headers for name in ("x-accel-redirect", "location"))
    if "set-cookie" in headers:
        assert headers["set-cookie"].split("=", 1)[0] in {"session", "csrftoken"}
    assert not any(value in str(headers).lower() for value in ("x-amz-", "encoded/", "rustfs:9000"))
    if status >= 400:
        assert not any(value in body for value in (b"<Error", b"encoded/", b"X-Amz-", b"rustfs"))
    return body


async def gateway_check(container, resources, settings, pairs):
    gateway = sys.argv[1]
    loop = asyncio.get_running_loop()
    browsers = []
    faults = []

    async def storage_proxy(reader, writer):
        """Transparent TCP forwarding, with a one-shot EOF after real S3 headers."""
        upstream = None
        tasks = []
        try:
            request = await reader.readuntil(b"\r\n\r\n")
            remote, upstream = await asyncio.open_connection("rustfs", 9000)
            upstream.write(request)
            await upstream.drain()
            if faults:
                faults.pop()
                writer.write(await remote.readuntil(b"\r\n\r\n"))
                writer.write(await remote.readexactly(1))
                await writer.drain()
                return

            async def pipe(source, destination):
                while data := await source.read(8192):
                    destination.write(data)
                    await destination.drain()

            tasks = [
                asyncio.create_task(pipe(reader, upstream)),
                asyncio.create_task(pipe(remote, writer)),
            ]
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        except ConnectionError, asyncio.IncompleteReadError:
            pass
        finally:
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            for connection in (writer, upstream):
                if connection is not None:
                    connection.close()
                    await connection.wait_closed()

    proxy = await asyncio.start_server(storage_proxy, "0.0.0.0", 9000)

    async def invoke(scenario, *args):
        async with container() as operation:
            return await (await operation.get(scenario))(*args)

    first, second = pairs
    accounts = [await invoke(GetAccount, owner) for owner, _ in pairs]
    profiles = await invoke(GetPublicChannels, tuple(owner for owner, _ in pairs))
    channel_path = "/api/v1/channels/" + profiles[first[0]].tag
    async with resources.sessions.begin() as session:
        await session.execute(
            text("UPDATE video_publications SET moderation='visible', visibility='private'")
        )
    application = create_app(Settings(cookie_secure=False))
    server = uvicorn.Server(
        uvicorn.Config(
            application,
            host="0.0.0.0",
            port=8000,
            root_path="/api",
            access_log=False,
            log_level="warning",
        )
    )
    serving = asyncio.create_task(server.serve())
    try:
        for _ in range(100):
            if server.started:
                break
            if serving.done():
                await serving
                raise AssertionError("Qualification API did not start")
            await asyncio.sleep(0.1)
        assert server.started

        def complete(coroutine):
            return asyncio.run_coroutine_threadsafe(coroutine, loop).result(timeout=60)

        def browser_check():
            owner, recipient, guest = [Browser(gateway) for _ in range(3)]
            browsers.extend((owner, recipient, guest))
            owner.login(accounts[0].email)
            recipient.login(accounts[1].email)
            channel = json.loads(safe(guest.request(channel_path)))
            assert channel["id"] == profiles[first[0]].id and channel["name"] == "Author"
            subscription_path = channel_path + "/subscription"
            assert json.loads(safe(owner.request(subscription_path))) == {"subscribed": False, "can_subscribe": False}
            safe(owner.request(subscription_path, method="PUT", data={"subscribed": True}), 422)
            safe(guest.request(subscription_path), 401)
            assert json.loads(safe(recipient.request(subscription_path))) == {"subscribed": False, "can_subscribe": True}
            assert json.loads(safe(recipient.request(subscription_path, method="PUT", data={"subscribed": True}))) == {"subscribed": True, "can_subscribe": True}
            assert json.loads(safe(guest.request("/api/v1/channels/@" + profiles[first[0]].tag.upper()))) == channel
            for browser in (owner, recipient, guest):
                assert json.loads(safe(browser.request(channel_path + "/videos"))) == {
                    "items": [], "next_cursor": None,
                }
            base = f"/api/v1/videos/{first[1]}"
            second_base = f"/api/v1/videos/{second[1]}"
            assert json.loads(safe(owner.request(base + "/like"))) == {"liked": False, "like_count": 0, "can_like": True}
            safe(guest.request(base + "/like"), 404)
            safe(recipient.request(base + "/like"), 404)
            playlist_urls = []
            for visibility in ("public", "unlisted", "private"):
                created = owner.request(
                    "/api/v1/playlists",
                    method="POST",
                    data={"title": "Playlist " + visibility, "visibility": visibility},
                )
                assert created[0] == 201
                playlist_urls.append("/api/v1/playlists/" + str(json.loads(created[2])["id"]))
            identities = [int(uri.rsplit("/", 1)[1]) for uri in playlist_urls]
            assert (
                owner.request(base + "/playlists", method="PUT", data={"playlist_ids": identities})[
                    0
                ]
                == 200
            )
            choices = json.loads(safe(owner.request(base + "/playlists")))
            assert len(choices["items"]) == 3 and all(item["selected"] for item in choices["items"])
            for uri in playlist_urls:
                assert [item["id"] for item in json.loads(safe(owner.request(uri)))["items"]] == [
                    first[1]
                ]
            for uri in playlist_urls[:2]:
                assert json.loads(safe(guest.request(uri)))["items"] == []
            safe(recipient.request(playlist_urls[2]), 404)
            playlist_search_path = "/api/v1/search?q=Playlist&type=playlist&duration=long&feature=4k"
            playlist_search = json.loads(safe(guest.request(playlist_search_path)))
            assert [item["playlist"]["id"] for item in playlist_search["items"]] == [identities[0]]
            assert playlist_search["next_cursor"] is None
            channel_playlists = json.loads(safe(guest.request(channel_path + "/playlists")))
            assert [item["id"] for item in channel_playlists["items"]] == [identities[0]]
            assert json.loads(safe(owner.request(channel_path + "/playlists"))) == channel_playlists
            assert json.loads(safe(owner.request(playlist_search_path))) == playlist_search
            assert json.loads(safe(guest.request("/api/v1/search?q=Playlist"))) == playlist_search
            assert not any(
                marker in json.dumps(playlist_search)
                for marker in ("@example.com", "owner_id", "preview_url", "video_count", "encoded/")
            )
            before = json.loads(owner.request(base + "/settings")[2])
            for suffix in ("/image", "/generated/0", "/generated/1", "/generated/2"):
                uri = base + "/thumbnail" + suffix
                preview = safe(owner.request(uri))
                assert preview[8:12] == b"WEBP"
                head = owner.request(uri, method="HEAD")
                assert safe(head) == b"" and int(head[1]["content-length"]) == len(preview)
                safe(recipient.request(uri), 404)
            # Already cleaned originals cannot be touched by repeat publication.
            repeat = owner.request(base + "/publish", method="POST")
            assert repeat[0] == 200 and json.loads(repeat[2]) == before
            assert recipient.request(base + "/publish", method="POST")[0] == 404

            master_path = base + "/hls/master.m3u8"
            master = safe(owner.request(master_path)).decode()
            assert "TYPE=AUDIO" in master and "TYPE=SUBTITLES" in master
            assert not any(value in master for value in ("encoded/", "X-Amz", "rustfs"))
            tracks = re.findall(r'URI="([^"]+)"', master)
            tracks += [line for line in master.splitlines() if line and not line.startswith("#")]
            assert len(set(tracks)) >= 5  # 360/480/720, reusable audio and captions
            checked = [master_path, base + "/preview.webp"]
            types = set()
            sample = None
            downloaded = {}
            for relative in dict.fromkeys(tracks):
                playlist_path = urljoin(master_path, relative)
                playlist = safe(owner.request(playlist_path))
                downloaded[playlist_path] = playlist
                checked.append(playlist_path)
                lines = playlist.decode().splitlines()
                files = re.findall(r'URI="([^"]+)"', playlist.decode())
                files += [line for line in lines if line and not line.startswith("#")]
                for filename in files:
                    path = urljoin(playlist_path, filename)
                    response = owner.request(path)
                    body = safe(response)
                    downloaded[path] = body
                    checked.append(path)
                    types.add(response[1]["content-type"].split(";")[0])
                    assert body
                    head = owner.request(path, method="HEAD")
                    assert safe(head) == b"" and int(head[1]["content-length"]) == len(body)
                    sliced = owner.request(path, headers={"Range": "bytes=0-15"})
                    assert safe(sliced, 206) == body[:16]
                    assert sliced[1]["content-range"] == f"bytes 0-15/{len(body)}"
                    if filename.endswith("m4s"):
                        sample = path, body, response[1]
                    if filename.endswith("vtt"):
                        assert b"WEBVTT" in body and "Привет" in body.decode()
            assert types == {"video/mp4", "audio/mp4", "text/vtt"}
            assert sample is not None
            path, body, headers = sample
            head = owner.request(master_path, method="HEAD")
            assert safe(head) == b"" and int(head[1]["content-length"]) == len(master.encode())
            assert safe(owner.request(base + "/preview.webp"))[8:12] == b"WEBP"
            safe(owner.request(path, headers={"Range": f"bytes={len(body) + 10}-"}), 416)
            assert (
                safe(owner.request(path, headers={"Range": "bytes=0-15", "If-Range": '"wrong"'}))
                == body
            )
            assert (
                safe(
                    owner.request(
                        path, headers={"Range": "bytes=0-15", "If-Range": headers["etag"]}
                    ),
                    206,
                )
                == body[:16]
            )
            multipart = owner.request(path, headers={"Range": "bytes=0-1,4-5"})
            # Streaming Nginx may ignore multi-range and return the full object.
            # Both are valid; it must never return an incorrect partial body.
            if multipart[0] == 206:
                safe(multipart, 206)
                assert multipart[1]["content-type"].startswith("multipart/byteranges")
                assert body[:2] in multipart[2] and body[4:6] in multipart[2]
            else:
                assert safe(multipart) == body
            # Decode the exact public manifests and bytes downloaded via Nginx.
            with TemporaryDirectory() as scratch:
                root = Path(scratch)
                master_file = root / master_path.lstrip("/")
                master_file.parent.mkdir(parents=True, exist_ok=True)
                master_file.write_text(master)
                for uri, data in downloaded.items():
                    file = root / uri.lstrip("/")
                    file.parent.mkdir(parents=True, exist_ok=True)
                    file.write_bytes(data)
                import subprocess

                subprocess.run(
                    [
                        "ffmpeg",
                        "-v",
                        "error",
                        "-nostdin",
                        "-protocol_whitelist",
                        "file",
                        "-allowed_extensions",
                        "ALL",
                        "-i",
                        str(master_file),
                        "-map",
                        "0:v",
                        "-map",
                        "0:a",
                        "-f",
                        "null",
                        "-",
                    ],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=60,
                )

            for browser in (guest, recipient):
                for uri in checked:
                    safe(browser.request(uri), 404)
            permissions = base + "/permissions"
            addresses = [f"cursor{index:03d}@example.com" for index in range(21)]
            for address in addresses:
                assert owner.request(permissions, method="PUT", data={"email": address, "allowed": True})[0] == 200
            first_permissions = json.loads(safe(owner.request(permissions)))
            assert first_permissions["emails"] == list(reversed(addresses))[:20]
            assert first_permissions["next_cursor"]
            final_permissions = json.loads(safe(owner.request(permissions + "?cursor=" + first_permissions["next_cursor"])))
            assert final_permissions == {"emails": [addresses[0]], "next_cursor": None}
            safe(guest.request(permissions), 401)
            safe(recipient.request(permissions), 404)
            for address in addresses:
                assert owner.request(permissions, method="PUT", data={"email": address, "allowed": False})[0] == 200
            assert json.loads(safe(owner.request(permissions))) == {"emails": [], "next_cursor": None}
            assert (
                owner.request(
                    permissions,
                    method="PUT",
                    data={"email": accounts[1].email.upper(), "allowed": True},
                )[0]
                == 200
            )
            for uri in checked:
                safe(recipient.request(uri))
            before_like = json.loads(safe(owner.request(base + "/settings")))
            for target in (True, True, False, False, True):
                assert json.loads(safe(recipient.request(base + "/like", method="PUT", data={"liked": target}))) == {"liked": target, "like_count": int(target), "can_like": True}
            assert json.loads(safe(recipient.request(second_base + "/like")))["like_count"] == 0
            assert json.loads(safe(owner.request(base + "/settings"))) == before_like
            assert json.loads(safe(recipient.request(subscription_path, method="PUT", data={"subscribed": False}))) == {"subscribed": False, "can_subscribe": True}
            for uri in checked:
                safe(recipient.request(uri))
            assert [
                item["id"]
                for item in json.loads(safe(recipient.request(playlist_urls[0])))["items"]
            ] == [first[1]]
            safe(recipient.request(playlist_urls[2]), 404)
            assert (
                owner.request(
                    permissions, method="PUT", data={"email": accounts[1].email, "allowed": False}
                )[0]
                == 200
            )
            for uri in checked:
                safe(recipient.request(uri), 404)
            safe(recipient.request(base + "/like"), 404)
            safe(recipient.request(base + "/like", method="PUT", data={"liked": False}), 404)
            assert json.loads(safe(owner.request(base + "/like")))["like_count"] == 1
            assert json.loads(safe(recipient.request(subscription_path, method="PUT", data={"subscribed": True}))) == {"subscribed": True, "can_subscribe": True}
            recipient.login(accounts[1].email)
            assert json.loads(safe(recipient.request(subscription_path)))["subscribed"]
            for uri in checked:
                safe(recipient.request(uri), 404)
            assert json.loads(safe(recipient.request(playlist_urls[0])))["items"] == []
            metadata = {key: before[key] for key in ("title", "description", "visibility")}
            assert (
                owner.request(
                    base + "/settings", method="PUT", data={**metadata, "visibility": "public"}
                )[0]
                == 200
            )
            for uri in checked:
                safe(guest.request(uri))
            assert [
                item["id"] for item in json.loads(safe(guest.request(playlist_urls[0])))["items"]
            ] == [first[1]]
            feed = json.loads(safe(guest.request("/api/v1/videos/feed")))
            assert [item["id"] for item in feed["items"]] == [first[1]]
            assert feed["next_cursor"] is None
            assert feed["items"][0]["channel"]["name"] == "Author"
            assert feed["items"][0]["preview_url"] == base + "/preview.webp"
            assert feed["items"][0]["duration_seconds"] == 1
            assert json.loads(safe(guest.request(base + "/like"))) == {"liked": False, "like_count": 1, "can_like": False}
            assert json.loads(safe(recipient.request(base + "/like", method="PUT", data={"liked": False}))) == {"liked": False, "like_count": 0, "can_like": True}
            assert json.loads(safe(guest.request(channel_path + "/videos"))) == feed
            search = json.loads(
                safe(
                    guest.request(
                        "/api/v1/search?q=PRIVATE%20PREVIEW&type=video&feature=hd&feature=subtitles"
                    )
                )
            )
            assert [item["video"]["id"] for item in search["items"]] == [first[1]]
            assert search["next_cursor"] is None
            assert search["items"][0]["video"] == feed["items"][0]
            assert (
                json.loads(safe(guest.request("/api/v1/search?q=Private&type=video&feature=4k")))[
                    "items"
                ]
                == []
            )
            channels = json.loads(
                safe(guest.request("/api/v1/search?q=AUTHOR&type=channel&feature=4k&duration=long"))
            )
            assert len(channels["items"]) == 2 and all(
                item["channel"]["name"] == "Author" for item in channels["items"]
            )
            assert not any(
                marker in json.dumps(feed)
                for marker in (
                    "@example.com",
                    "encoded/",
                    "X-Amz",
                    "owner_id",
                    "artifact",
                    "error_code",
                )
            )
            assert owner.request(base + "/settings", method="PUT", data=metadata)[0] == 200
            assert json.loads(safe(guest.request("/api/v1/videos/feed"))) == {
                "items": [],
                "next_cursor": None,
            }
            assert json.loads(safe(guest.request(channel_path + "/videos")))["items"] == []
            assert (
                json.loads(safe(guest.request("/api/v1/search?q=Private&type=video")))["items"]
                == []
            )
            for uri in checked:
                safe(guest.request(uri), 404)

            async def hide():
                async with resources.sessions.begin() as session:
                    await session.execute(
                        text("UPDATE video_publications SET moderation='hidden' WHERE id=:id"),
                        {"id": first[1]},
                    )

            complete(hide())
            for uri in checked:
                safe(owner.request(uri), 404)
            assert owner.request(base + "/settings")[0] == 200
            # Shared physical files still respect the other publication's rights.
            safe(recipient.request(second_base + "/hls/master.m3u8"))
            safe(owner.request(second_base + "/hls/master.m3u8"), 404)

            for suffix in (
                "/hls/tracks/v999/init.mp4",
                "/hls/tracks/v1/segment_999999.m4s",
                "/hls/tracks/v1/init.mp4%3Fx=1",
                "/hls/tracks/v1/%0d%0aLocation:evil",
            ):
                assert recipient.request(second_base + suffix)[0] in {400, 404}
            assert guest.request("/_media_storage/private/anything")[0] == 404
            assert recipient.request(second_base + "/hls/master.m3u8", method="POST")[0] == 405
            forged = {
                "X-Accel-Redirect": "/_media_storage/any",
                "X-Owner-Id": str(second[0]),
                "Authorization": "Bearer arbitrary",
                "X-Forwarded-User": str(second[0]),
            }
            safe(guest.request(second_base + "/hls/tracks/v1/init.mp4", headers=forged), 404)

            # A vanished object returns a generic safe error, never RustFS XML.
            logical = second_base + "/hls/tracks/v1/init.mp4"
            unchanged = json.loads(recipient.request(second_base + "/settings")[2])

            async def remove():
                descriptor = await invoke(GetMedia, second[0], second[1], "v1", "init.mp4")
                from urllib.parse import urlsplit

                key = urlsplit(descriptor.redirect).path.split("/", 3)[3]
                data = await asyncio.to_thread(
                    resources.objects.get_object, Bucket=resources.bucket, Key=key
                )
                try:
                    content = data["Body"].read()
                finally:
                    data["Body"].close()
                await asyncio.to_thread(
                    resources.objects.delete_object, Bucket=resources.bucket, Key=key
                )
                return key, content

            key, content = complete(remove())
            try:
                safe(recipient.request(logical), 404)
            finally:
                complete(
                    asyncio.to_thread(
                        resources.objects.put_object,
                        Bucket=resources.bucket,
                        Key=key,
                        Body=content,
                        ContentType="video/mp4",
                    )
                )
            safe(recipient.request(logical))
            original_sign = S3MediaRedirects.sign

            async def invalid_signature(adapter, media, method):
                signed = await original_sign(adapter, media, method)
                return re.sub(r"(X-Amz-Signature=)[0-9a-f]", r"\g<1>z", signed)

            with patch.object(S3MediaRedirects, "sign", invalid_signature):
                safe(recipient.request(logical), 502)
            safe(recipient.request(logical))

            async def interrupt_next():
                faults.append(True)

            complete(interrupt_next())
            try:
                recipient.request(logical)
            except IncompleteRead as failure:
                assert len(failure.partial) == 1 and failure.expected > 0
            else:
                raise AssertionError("Truncated storage response was accepted")
            assert not faults
            safe(recipient.request(logical))
            after = json.loads(recipient.request(second_base + "/settings")[2])
            assert after == unchanged and after["processing"] == "ready"
            print(
                "Gateway qualification passed: decoded AV1/AAC/WebVTT/WebP, GET/HEAD/Range/If-Range/multipart, live grants/revocation/private/hidden, shared storage isolation, safe storage errors and interrupted response; API performed no object reads."
            )

        # These are the clients actually injected into HTTP handlers. Nginx has
        # an independent connection to RustFS and continues to serve real bytes.
        api_objects = application.state.resources.objects
        with (
            patch.object(api_objects, "get_object", side_effect=AssertionError("API media read")),
            patch.object(api_objects, "head_object", side_effect=AssertionError("API media HEAD")),
        ):
            await asyncio.to_thread(browser_check)
    finally:
        if application.state.resources is not None:
            for browser in browsers:
                for cookie in browser.cookies:
                    if cookie.name == "session":
                        await application.state.resources.cache.delete("sessions." + cookie.value)
        server.should_exit = True
        await serving
        proxy.close()
        await proxy.wait_closed()


if __name__ == "__main__":
    try:
        asyncio.run(qualify(gateway_check, production=sys.argv[2] == "prod"))
    except Exception as error:
        # Frame locations aid debugging without printing exception values,
        # storage URLs, signatures, SQL parameters or credentials.
        frames = traceback.extract_tb(error.__traceback__)
        print(
            type(error).__name__,
            [(Path(frame.filename).name, frame.lineno, frame.name) for frame in frames],
        )
        raise SystemExit("Gateway qualification failed") from None
