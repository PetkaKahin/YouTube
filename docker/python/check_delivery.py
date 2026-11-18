"""Qualify actual gateway bytes and live permissions without pytest or production data."""

import asyncio
import json
import re
import sys
import traceback
from datetime import UTC, datetime
from uuid import uuid4
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
from app.job_administration import JobAdministration
from app.media_worker import create_queue as create_media_queue
from common.infrastructure.job_error_model import JobErrorRow
from check_thumbnails import qualify
from channel.public import GetPublicChannels
from user.public import GetAccount, SetRole
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
            discussion = base + "/comments"
            for browser in (owner, recipient, guest):
                safe(browser.request(discussion), 404)
            safe(owner.request(discussion, method="POST", data={"text": "private"}), 404)
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
            # Email permission permits private playback, but never opens its discussion.
            safe(recipient.request(discussion), 404)
            safe(recipient.request(discussion, method="POST", data={"text": "private"}), 404)
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
            before_comments = json.loads(safe(owner.request(base + "/settings")))
            content = "<script>plain text</script>\n" + "я" * 1972
            assert len(content) == 2000
            created = json.loads(safe(owner.request(discussion, method="POST", data={
                "text": content, "author_id": second[0], "like_count": 999,
            }), 201))
            assert created["text"] == content and created["author_name"] == "Author"
            comment_like = discussion + f"/{created['id']}/like"
            for target in (True, True, False, False, True):
                assert json.loads(safe(recipient.request(comment_like, method="PUT", data={
                    "liked": target,
                }))) == {"liked": target, "like_count": int(target), "can_like": True}
            safe(owner.request(discussion, method="POST", data={"text": "я" * 2001}), 422)
            safe(owner.request(discussion, method="POST", data={"text": " \t\n"}), 422)
            safe(guest.request(discussion, method="POST", data={"text": "guest"}), 401)
            safe(guest.request(comment_like, method="PUT", data={"liked": True}), 401)
            for index in range(20):
                safe(owner.request(discussion, method="POST", data={"text": str(index)}), 201)
            comment_page = json.loads(safe(guest.request(discussion)))
            assert len(comment_page["items"]) == 20 and not comment_page["can_comment"]
            comment_tail = json.loads(safe(guest.request(
                discussion + "?cursor=" + comment_page["next_cursor"]
            )))
            assert [item["id"] for item in comment_tail["items"]] == [created["id"]]
            assert comment_tail["next_cursor"] is None
            popular = json.loads(safe(guest.request(discussion + "?sort=popular")))
            assert popular["items"][0]["id"] == created["id"]
            assert popular["items"][0]["like_count"] == 1
            assert not popular["items"][0]["liked"]
            assert not any(marker in json.dumps(popular) for marker in (
                "@example.com", "author_id", "owner_id", "role", "encoded/",
            ))
            assert json.loads(safe(owner.request(base + "/settings"))) == before_comments
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
            for browser in (owner, recipient, guest):
                safe(browser.request(discussion), 404)
                safe(browser.request(comment_like), 404)
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
            safe(owner.request(discussion), 404)
            safe(owner.request(comment_like, method="PUT", data={"liked": False}), 404)
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
            check_moderation(owner, recipient, guest, second_base, accounts, complete, invoke, resources)
            check_job_administration(owner, recipient, guest, second_base, complete, container, resources, settings)
            print(
                "Gateway qualification passed: decoded AV1/AAC/WebVTT/WebP, GET/HEAD/Range/If-Range/multipart, live grants/revocation/private/hidden, comments and comment likes, report decisions and staff media, shared storage isolation, safe storage errors and interrupted response; API performed no object reads."
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


def check_moderation(staff, reporter, guest, base, accounts, complete, invoke, resources):
    """Actual staff cookies, private/hidden media, revision conflicts and live demotion."""
    import subprocess

    queue = "/api/v1/moderation/reports"
    safe(guest.request(queue), 401)
    safe(staff.request(queue), 403)
    for reason in ("spam", "copyright"):
        assert json.loads(safe(reporter.request(base + "/reports", method="POST", data={
            "reason": reason, "reporter_id": accounts[0].id, "role": "admin",
        }), 202)) == {"accepted": True}
    complete(invoke(SetRole, accounts[0].email, "moderator"))
    page = json.loads(safe(staff.request(queue)))
    assert len(page["items"]) == 1 and page["next_cursor"] is None
    report = page["items"][0]
    assert report["revision"] == 2 and report["reason"] == "copyright"
    assert report["reporter_id"] == accounts[1].id
    path = queue + "/" + str(report["id"])
    safe(staff.request(base), 404)
    material = json.loads(safe(staff.request(path + "/video")))
    assert material["hls_url"] == path + "/hls/master.m3u8"
    assert not any(marker in json.dumps(material) for marker in (
        "@example.com", "reporter_id", "owner_id", "X-Amz", "encoded/",
    ))
    safe(staff.request(queue + "/4611686018427387904/video"), 404)
    master_url = material["hls_url"]
    master = safe(staff.request(master_url)).decode()
    assert "/videos/" not in master and "TYPE=SUBTITLES" in master
    tracks = re.findall(r'URI="([^"]+)"', master)
    tracks += [line for line in master.splitlines() if line and not line.startswith("#")]
    downloaded = {master_url: master.encode()}
    service_paths = [master_url, path + "/video", path + "/preview.webp"]
    types = set()
    for relative in tracks:
        uri = urljoin(master_url, relative)
        data = safe(staff.request(uri))
        downloaded[uri] = data
        service_paths.append(uri)
        files = re.findall(r'URI="([^"]+)"', data.decode())
        files += [line for line in data.decode().splitlines() if line and not line.startswith("#")]
        for filename in files:
            file_url = urljoin(uri, filename)
            response = staff.request(file_url)
            body = safe(response)
            downloaded[file_url] = body
            service_paths.append(file_url)
            types.add(response[1]["content-type"].split(";")[0])
            assert safe(staff.request(file_url, method="HEAD")) == b""
            assert safe(staff.request(file_url, headers={"Range": "bytes=0-15"}), 206) == body[:16]
    assert types == {"video/mp4", "audio/mp4", "text/vtt"}
    assert safe(staff.request(path + "/preview.webp"))[8:12] == b"WEBP"
    with TemporaryDirectory() as directory:
        root = Path(directory)
        for uri, body in downloaded.items():
            file = root / uri.lstrip("/")
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(body)
        subprocess.run([
            "ffmpeg", "-v", "error", "-nostdin", "-protocol_whitelist", "file",
            "-allowed_extensions", "ALL", "-i", str(root / master_url.lstrip("/")),
            "-map", "0:v", "-map", "0:a", "-f", "null", "-",
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    async def evidence():
        async with resources.sessions() as session:
            return (await session.execute(text(
                "SELECT actor_id,revision,reason,resource FROM moderation_accesses WHERE report_id=:id"
            ), {"id": report["id"]})).mappings().all()
    audit = complete(evidence())
    assert audit and all(row["actor_id"] == accounts[0].id and row["revision"] == 2
                         and row["reason"] == "copyright" for row in audit)
    decision = {"revision": 2, "action": "dismiss"}
    resolved = json.loads(safe(staff.request(path + "/decision", method="PUT", data=decision)))
    assert json.loads(safe(staff.request(path + "/decision", method="PUT", data=decision))) == resolved
    for uri in service_paths:
        safe(staff.request(uri), 404)
    safe(reporter.request(base))
    safe(reporter.request(base + "/reports", method="POST", data={"reason": "other"}), 202)
    renewed = json.loads(safe(staff.request(path)))
    assert renewed["revision"] == 3 and renewed["decision"] is None
    safe(staff.request(path + "/decision", method="PUT", data={
        "revision": 2, "action": "hide", "reason": "other",
    }), 409)
    # A second pending complaint remains a valid, independently audited basis after hiding.
    safe(reporter.request(base + "/permissions", method="PUT", data={
        "email": accounts[0].email, "allowed": True,
    }))
    safe(staff.request(base + "/reports", method="POST", data={"reason": "other"}), 202)
    safe(reporter.request(base + "/permissions", method="PUT", data={
        "email": accounts[0].email, "allowed": False,
    }))
    other = next(item for item in json.loads(safe(staff.request(queue)))["items"]
                 if item["id"] != report["id"])
    other_path = queue + "/" + str(other["id"])
    safe(staff.request(path + "/decision", method="PUT", data={
        "revision": 3, "action": "hide", "reason": "violence",
    }))
    for uri in (base, base + "/preview.webp", base + "/hls/master.m3u8",
                base + "/hls/tracks/v1/init.mp4", base + "/hls/tracks/v1/segment_000000.m4s"):
        safe(reporter.request(uri), 404)
        safe(staff.request(uri), 404)
    for uri in service_paths:
        safe(staff.request(uri), 404)
    safe(staff.request(other_path + "/video"))
    safe(staff.request(other_path + "/hls/master.m3u8"))
    for assigned_role, expected in (("user", 403), ("admin", 200)):
        complete(invoke(SetRole, accounts[0].email, assigned_role))
        for suffix in ("/video", "/preview.webp", "/hls/master.m3u8", "/hls/tracks/v1/init.mp4"):
            safe(staff.request(other_path + suffix), expected)
        safe(staff.request(base), 404)
    safe(staff.request(other_path + "/decision", method="PUT", data={"revision": 1, "action": "dismiss"}))
    safe(staff.request(other_path + "/video"), 404)
    safe(reporter.request(base), 404)


def check_job_administration(admin, owner, guest, base, complete, container, resources, settings):
    safe(guest.request("/api/v1/admin/job-errors"), 401)
    safe(owner.request("/api/v1/admin/job-errors"), 403)
    try:
        with admin.http.open(Request(urljoin(admin.gateway, "/api/v1/admin/job-retries"),
            data=b'{"mode":"all"}', headers={"Content-Type": "application/json"}, method="POST"), timeout=30):
            raise AssertionError("Administrator retry accepted without CSRF")
    except HTTPError as error:
        assert error.code == 403
    before = json.loads(safe(owner.request(base + "/settings")))

    async def failure():
        async with resources.sessions.begin() as session:
            target = (await session.execute(text(
                "SELECT e.id,e.job_id,e.artifact,j.priority FROM video_encoded_tracks e "
                "JOIN procrastinate_jobs j ON j.id=e.job_id "
                "WHERE e.spec->>'kind'='video' AND e.status='ready' ORDER BY e.id LIMIT 1"
            ))).one()
            await session.execute(text("UPDATE procrastinate_jobs SET status='failed' WHERE id=:id"), {"id": target.job_id})
            session.add(JobErrorRow(event_id=uuid4(), job_id=target.job_id, attempt=1,
                stage="execute", occurred_at=datetime.now(UTC), reason="task_failed",
                exception_type="RuntimeError", trace=[], error_code="encoding_failed"))
            return target.id, target.job_id, target.artifact, target.priority
    encoded_id, job_id, artifact, priority = complete(failure())
    history = json.loads(safe(admin.request("/api/v1/admin/job-errors")))
    assert history["items"][0]["job_id"] == job_id
    selected = json.loads(safe(admin.request("/api/v1/admin/job-retries", method="POST",
        data={"mode": "selected", "job_ids": [job_id, job_id]}), 202))
    all_jobs = json.loads(safe(admin.request("/api/v1/admin/job-retries", method="POST",
        data={"mode": "all"}), 202))

    async def execute():
        for _ in range(4):
            async with container() as operation:
                await (await operation.get(JobAdministration)).dispatch(settings.job_retry_batch_size)
        queue = create_media_queue(settings, container)
        async with queue.open_async():
            await queue.run_worker_async(queues=["media"], concurrency=1, wait=False,
                listen_notify=False, install_signal_handlers=False)
        async with resources.sessions() as session:
            current = (await session.execute(text(
                "SELECT e.job_id,e.artifact,j.status,j.attempts,j.priority FROM video_encoded_tracks e "
                "JOIN procrastinate_jobs j ON j.id=e.job_id WHERE e.id=:id"
            ), {"id": encoded_id})).one()
            assert current.job_id != job_id and current.artifact == artifact
            assert current.status == "succeeded" and current.attempts == 1
            assert current.priority == priority
    complete(execute())
    first = json.loads(safe(admin.request("/api/v1/admin/job-retries/" + str(selected["id"]))))
    second = json.loads(safe(admin.request("/api/v1/admin/job-retries/" + str(all_jobs["id"]))))
    assert first["queued"] == 1 and second["skipped"] == 1 and second["queued"] == 0
    assert first["status"] == second["status"] == "submitted"
    assert json.loads(safe(owner.request(base + "/settings"))) == before
    safe(owner.request(base), 404)
    assert json.loads(safe(admin.request("/api/v1/admin/job-errors")))["items"][0]["job_id"] == job_id
    print("Job administration passed: live admin/CSRF HTTP, durable selected/all overlap, fresh native cycle, retained shared package after original deletion, preserved hidden state and history.")


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
