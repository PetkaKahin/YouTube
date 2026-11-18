import assert from "node:assert/strict";
import { createServer, request as httpRequest } from "node:http";
import { readFile } from "node:fs/promises";
import { randomUUID } from "node:crypto";
import { browser, pause } from "./cdp.mjs";
import { firefoxBrowser } from "./bidi.mjs";

const firefox = process.argv.includes("--firefox");
const source = await readFile("/media/portrait.mp4");
const image = await readFile("/media/thumbnail.png");
let fail = "",
  slow = false,
  processingState = "";
const calls = [],
  sockets = new Set();
const server = createServer(async (req, res) => {
  try {
    if (req.url === "/fixture-source") {
      res
        .writeHead(200, { "content-type": "application/octet-stream" })
        .end(source);
      return;
    }
    if (req.url === "/fixture-image") {
      res
        .writeHead(200, { "content-type": "application/octet-stream" })
        .end(image);
      return;
    }
    const api = req.url.startsWith("/api/"),
      storage = req.url.startsWith("/uploads/");
    // Never retain bearer URLs or object keys in test logs/results.
    const kind = req.url.endsWith("/permissions")
      ? "permissions"
      : req.url.endsWith("/playlists") && req.method === "PUT"
        ? "playlists"
        : req.url.endsWith("/thumbnail") && req.method === "PUT"
          ? "thumbnail"
          : storage
            ? "part"
            : req.url === "/api/v1/videos"
              ? "create"
              : req.url.endsWith("/complete")
                ? "complete"
                : req.url.endsWith("/publish")
                  ? "publish"
                  : "other";
    if (api || storage)
      calls.push({
        kind,
        method: req.method,
        csrf: !!req.headers["x-csrftoken"],
      });
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    if (slow && (kind === "part" || kind === "complete")) await pause(650);
    if (fail === kind && kind !== "other" && req.method !== "GET") {
      res
        .writeHead(503, { "content-type": "application/problem+json" })
        .end('{"code":"unavailable"}');
      return;
    }
    const headers = new Headers();
    for (const key of ["content-type", "cookie", "x-csrftoken", "range"])
      if (req.headers[key]) headers.set(key, req.headers[key]);
    const target =
      api || storage
        ? `http://youtube-frontend-upload-gateway:8080${req.url}`
        : `http://node:5173${req.url}`;
    const upstream = await fetch(target, {
      method: req.method,
      headers,
      body: chunks.length ? Buffer.concat(chunks) : undefined,
      redirect: "manual",
    });
    let body = Buffer.from(await upstream.arrayBuffer());
    if (processingState && req.url.endsWith("/processing") && upstream.ok) {
      const state = JSON.parse(body.toString());
      state.processing = processingState;
      if (processingState === "failed") {
        state.available_qualities = [];
        state.qualities = state.qualities.map((q) => ({
          ...q,
          status: "failed",
          error_code: "test_failure",
        }));
      } else if (processingState === "partial")
        state.qualities.push({
          quality: 2160,
          range: "sdr",
          status: "failed",
          error_code: "test_failure",
        });
      body = Buffer.from(JSON.stringify(state));
    }
    if (
      slow &&
      ["permissions", "playlists", "thumbnail"].includes(kind) &&
      req.method === "PUT"
    )
      await pause(650);
    res.statusCode = upstream.status;
    for (const key of ["content-type", "location", "cache-control"])
      if (upstream.headers.has(key))
        res.setHeader(key, upstream.headers.get(key));
    if (upstream.headers.getSetCookie().length)
      res.setHeader("set-cookie", upstream.headers.getSetCookie());
    res.end(body);
  } catch {
    res.writeHead(502).end();
  }
});
server.on("upgrade", (req, socket, head) => {
  const upstream = httpRequest({
    hostname: "node",
    port: 5173,
    path: req.url,
    headers: req.headers,
  });
  upstream.on("upgrade", (response, peer, peerHead) => {
    sockets.add(socket);
    sockets.add(peer);
    socket.on("close", () => {
      sockets.delete(socket);
      peer.destroy();
    });
    peer.on("close", () => {
      sockets.delete(peer);
      socket.destroy();
    });
    socket.on("error", () => peer.destroy());
    peer.on("error", () => socket.destroy());
    socket.write(
      "HTTP/1.1 101 Switching Protocols\r\n" +
        Object.entries(response.headers)
          .map(([key, value]) => `${key}: ${value}`)
          .join("\r\n") +
        "\r\n\r\n",
    );
    if (head.length) peer.write(head);
    if (peerHead.length) socket.write(peerHead);
    socket.pipe(peer);
    peer.pipe(socket);
  });
  upstream.on("error", () => socket.destroy());
  upstream.end();
});
await new Promise((resolve) => server.listen(4186, "127.0.0.1", resolve));
let ready = false;
for (let attempt = 0; attempt < 200; attempt++) {
  try {
    if (
      (
        await fetch(
          "http://youtube-frontend-upload-gateway:8080/api/v1/auth/me",
        )
      ).status === 401
    ) {
      ready = true;
      break;
    }
  } catch {}
  await pause(100);
}
if (!ready) throw new Error("Isolated upload API did not start");
const c = firefox ? await firefoxBrowser() : await browser();
const base = "http://127.0.0.1:4186",
  token = randomUUID().replaceAll("-", "").slice(0, 16);
let assertions = 0;
const check = (value, message) => {
  assert.ok(value, message);
  assertions++;
};
const count = (kind) => calls.filter((call) => call.kind === kind).length;
async function key(name, bidiValue, code) {
  if (firefox) await c.key(bidiValue);
  else {
    await c.send("Input.dispatchKeyEvent", {
      type: "keyDown",
      key: name,
      code: name,
      windowsVirtualKeyCode: code,
    });
    await c.send("Input.dispatchKeyEvent", {
      type: "keyUp",
      key: name,
      code: name,
      windowsVirtualKeyCode: code,
    });
  }
}
const write = (url, body) =>
  c.evaluate(
    `(async()=>{const csrf=decodeURIComponent(document.cookie.split('; ').find(x=>x.startsWith('csrftoken=')).slice(10));const r=await fetch(${JSON.stringify(url)},{method:'POST',headers:{'content-type':'application/json','x-csrftoken':csrf},body:JSON.stringify(${JSON.stringify(body)})});return r.status})()`,
  );
async function selectFile(name = "Исходник.unknown") {
  await c.evaluate(
    `(async()=>{const bytes=await (await fetch('/fixture-source')).arrayBuffer();const files=new DataTransfer();files.items.add(new File([bytes],${JSON.stringify(name)},{type:'application/octet-stream'}));const input=document.querySelector('#video-file');input.files=files.files;input.dispatchEvent(new Event('change',{bubbles:true}));})()`,
  );
  await c.wait(`!!document.querySelector('#video-title')`);
}
const refetchProcessing = () =>
  c.evaluate(
    `(async()=>{const client=Object.values(document.querySelector('#app').__vue_app__._context.provides).find(x=>typeof x?.getQueryCache==='function');await client.refetchQueries({queryKey:['upload-processing']});})()`,
  );
const request = (url, method, body) =>
  c.evaluate(
    `(async()=>{const csrf=decodeURIComponent(document.cookie.split('; ').find(x=>x.startsWith('csrftoken=')).slice(10));const r=await fetch(${JSON.stringify(url)},{method:${JSON.stringify(method)},headers:{'content-type':'application/json','x-csrftoken':csrf},body:${body === undefined ? "undefined" : JSON.stringify(JSON.stringify(body))}});return JSON.stringify({status:r.status,text:await r.text()})})()`,
  );
const read = async (url) => JSON.parse(await request(url, "GET"));
const put = async (url, body) => JSON.parse(await request(url, "PUT", body));
async function selectImage() {
  await c.evaluate(
    `(async()=>{const bytes=await (await fetch('/fixture-image')).arrayBuffer();const files=new DataTransfer();files.items.add(new File([bytes],'Превью.unusual',{type:'application/octet-stream'}));const input=document.querySelector('#thumbnail-file');input.files=files.files;input.dispatchEvent(new Event('change',{bubbles:true}));})()`,
  );
}
const playlistIds = [];
let videoId;
try {
  await c.viewport(1920, 935);
  await c.navigate(base + "/login?returnTo=%2Fstudio%2Fupload");
  const email = token + "@example.com",
    guest = token + "guest@example.com";
  for (const [address, tag] of [
    [email, "creator" + token],
    [guest, "invited" + token],
  ])
    check(
      (await write("/api/v1/auth/register", {
        name: "Автор Канала",
        tag,
        email: address,
        password: "abcdef",
        password_confirmation: "abcdef",
      })) === 201,
      "Isolated owner/recipient registration",
    );
  await c.fill("#login-email", email);
  await c.fill("#login-password", "abcdef");
  await c.click(".auth-card button[type=submit]");
  await c.wait(`!!document.querySelector('.upload-drop')`);
  for (let index = 0; index < 41; index++) {
    const response = JSON.parse(
      await request("/api/v1/playlists", "POST", {
        title: "Список " + index,
        description: "",
        visibility: ["public", "private", "unlisted"][index % 3],
      }),
    );
    assert.equal(response.status, 201);
    playlistIds.push(response.text.match(/"id":(\d+)/)[1]);
  }
  await selectFile();
  check(
    await c.evaluate(
      `document.querySelector('.thumbnail-actions button:nth-of-type(2)').disabled`,
    ),
    "Generated candidates disabled until source is processed",
  );
  await selectImage();
  check(
    await c.evaluate(
      `document.querySelector('.thumbnail-file').textContent.includes('Превью.unusual')`,
    ),
    "Custom image draft kept without extension whitelist",
  );
  await c.click(".playlist-choose");
  await c.wait(
    `document.querySelectorAll('.playlist-choices input').length===20`,
  );
  check(
    await c.evaluate(
      `document.querySelectorAll('.playlist-choices input').length===20`,
    ),
    "Playlist first page is twenty",
  );
  await c.click(
    `.playlist-choices input[data-playlist-id="${playlistIds[40]}"]`,
  );
  await c.click('dialog[aria-label="Плейлисты"] .playlist-create button');
  await c.wait(`document.querySelector('.playlist-dialog').open`);
  await c.fill("#playlist-title", "Создан из выбора");
  await c.click(".playlist-dialog button[type=submit]");
  await c.wait(
    `!document.querySelector('.playlist-dialog').open && [...document.querySelectorAll('.playlist-choices label')].some(x=>x.textContent.includes('Создан из выбора'))`,
  );
  check(
    await c.evaluate(
      `document.querySelector('dialog[aria-label="Плейлисты"]').open`,
    ),
    "Nested playlist creation returns to picker",
  );
  while (
    await c.evaluate(
      `!!document.querySelector('dialog[aria-label="Плейлисты"] .studio-dialog__body > button')`,
    )
  ) {
    await c.click(
      'dialog[aria-label="Плейлисты"] .studio-dialog__body > button',
    );
    await c.wait(
      `!document.querySelector('dialog[aria-label="Плейлисты"] .studio-dialog__body > button')?.disabled`,
    );
  }
  await c.click(
    `.playlist-choices input[data-playlist-id="${playlistIds[0]}"]`,
  );
  await c.click(".playlist-done");
  check(
    count("playlists") === 0,
    "Playlist draft stays local before video creation",
  );
  await c.click(".upload-dialog__buttons button[type=submit]");
  await c.click("#video-private");
  await c.click(".video-permissions > button");
  await c.fill("#permission-emails", "invalid");
  await c.click(".permission-done");
  check(
    await c.evaluate(
      `document.querySelector('dialog[aria-label^="Кому"] .form-error').textContent.includes('Проверьте')`,
    ),
    "Malformed email rejected",
  );
  await c.fill(
    "#permission-emails",
    `${guest.toUpperCase()}, unknown-${token}@example.com`,
  );
  await c.click(".permission-done");
  await c.wait(`!document.querySelector('dialog[aria-label^="Кому"]').open`);
  check(
    count("permissions") === 0,
    "Email draft stays local before video creation",
  );
  fail = "permissions";
  await c.click(".upload-dialog__buttons button[type=submit]");
  await c.wait(
    `!!document.querySelector('.upload-error') && document.querySelector('.upload-form').getAttribute('aria-busy')==='false'`,
  );
  videoId = await c.evaluate(
    `new URLSearchParams(location.search).get('video')`,
  );
  check(
    count("publish") === 0 &&
      !(await c.evaluate(`!!document.querySelector('.upload-dialog__saved')`)),
    "Failed grant prevents false publication success",
  );
  fail = "";
  await c.click(".upload-dialog__buttons button[type=submit]");
  await c.wait(`!!document.querySelector('.upload-dialog__saved')`);
  await c.wait(`!!document.querySelector('.upload-watch-link')`, 120000);
  let thumbnail;
  for (let attempt = 0; attempt < 120; attempt++) {
    thumbnail = JSON.parse(
      (await read(`/api/v1/videos/${videoId}/thumbnail`)).text,
    );
    if (thumbnail.selected === "custom" && thumbnail.custom_status === "ready")
      break;
    await pause(250);
  }
  check(
    thumbnail.selected === "custom" && thumbnail.custom_status === "ready",
    "Real PNG uploaded and normalized by media worker",
  );
  const permissions = JSON.parse(
    (await read(`/api/v1/videos/${videoId}/permissions`)).text,
  );
  check(
    permissions.emails.includes(guest) &&
      permissions.emails.includes("unknown-" + token + "@example.com"),
    "Registered and unknown recipients saved identically",
  );
  const choices = await read(`/api/v1/videos/${videoId}/playlists`);
  check(choices.status === 200, "Playlist choices readable by owner");
  await c.click(".upload-dialog__buttons .button--secondary");
  await c.wait(
    `document.querySelector('.upload-information').getClientRects().length>0 && !document.querySelector('.thumbnail-actions button:nth-of-type(2)').disabled`,
  );
  await c.click(".thumbnail-actions button:nth-of-type(2)");
  check(
    await c.evaluate(
      `document.querySelectorAll('.thumbnail-candidates button').length===3`,
    ),
    "Three server candidates shown",
  );
  fail = "thumbnail";
  await c.click(".thumbnail-candidates button:nth-child(3)");
  await c.wait(
    `!!document.querySelector('dialog[aria-label^="Выберите превью"] .form-error')`,
  );
  check(
    JSON.parse((await read(`/api/v1/videos/${videoId}/thumbnail`)).text)
      .selected === "custom",
    "Failed frame selection preserves custom preview",
  );
  fail = "";
  await c.click(".thumbnail-candidates button:nth-child(3)");
  await c.wait(
    `!document.querySelector('dialog[aria-label^="Выберите превью"]').open`,
  );
  thumbnail = JSON.parse(
    (await read(`/api/v1/videos/${videoId}/thumbnail`)).text,
  );
  check(
    thumbnail.selected === "generated" && thumbnail.slot === 2,
    "Explicit candidate selection confirmed",
  );
  await c.screenshot(`creator-information-${firefox ? "firefox" : "chromium"}`);
  await c.reload();
  await c.wait(`!!document.querySelector('#video-title')`);
  await c.click(".playlist-choose");
  await c.wait(
    `document.querySelectorAll('.playlist-choices input').length===20`,
  );
  const selectedNewest = `.playlist-choices input[data-playlist-id="${playlistIds[40]}"]`;
  check(
    await c.evaluate(
      `document.querySelector(${JSON.stringify(selectedNewest)}).checked`,
    ),
    "Saved selection restored",
  );
  await c.click(selectedNewest);
  await c.click(".playlist-done");
  await c.wait(
    `!document.querySelector('dialog[aria-label="Плейлисты"]').open`,
  );
  await c.click(".playlist-choose");
  check(
    await c.evaluate(
      `!document.querySelector(${JSON.stringify(selectedNewest)}).checked && document.querySelector('.playlist-choices input[data-playlist-id="${playlistIds[0]}"]').checked`,
    ),
    "Saving first page preserves selected last-page item",
  );
  await c.evaluate(
    `document.querySelectorAll('.playlist-choices input').forEach(input=>{if(!input.checked)input.click()})`,
  );
  await c.click(".playlist-done");
  await c.wait(
    `!document.querySelector('dialog[aria-label="Плейлисты"]').open`,
  );
  await c.click(".playlist-choose");
  check(
    await c.evaluate(
      `document.querySelectorAll('.playlist-choices input:checked').length===42`,
    ),
    "All playlists can be selected beyond a page",
  );
  await c.evaluate(
    `document.querySelectorAll('.playlist-choices input').forEach(input=>{if(input.checked)input.click()})`,
  );
  await c.click(".playlist-done");
  await c.wait(
    `!document.querySelector('dialog[aria-label="Плейлисты"]').open`,
  );
  await c.click(".playlist-choose");
  check(
    await c.evaluate(
      `document.querySelectorAll('.playlist-choices input:checked').length===0`,
    ),
    "Empty selection confirmed",
  );
  await c.click(".playlist-done");
  await c.wait(
    `!document.querySelector('dialog[aria-label="Плейлисты"]').open`,
  );
  for (let index = 0; index < 21; index++)
    assert.equal(
      (
        await put(`/api/v1/videos/${videoId}/permissions`, {
          email: `extra${index}-${token}@example.com`,
          allowed: true,
        })
      ).status,
      200,
    );
  await c.reload();
  await c.wait(`!!document.querySelector('#video-title')`);
  await c.click(".upload-dialog__buttons button[type=submit]");
  await c.click(".video-permissions > button");
  await c.wait(`document.querySelectorAll('.permission-list li').length===20`);
  await c.click('dialog[aria-label^="Кому"] .studio-dialog__body > button');
  await c.wait(`document.querySelectorAll('.permission-list li').length===23`);
  check(true, "Grant cursor reaches addresses beyond first page");
  await c.click(`button[aria-label="Отозвать доступ: ${guest}"]`);
  fail = "permissions";
  await c.click(".permission-done");
  await c.wait(
    `!!document.querySelector('dialog[aria-label^="Кому"] .form-error')`,
  );
  check(
    JSON.parse(
      (
        await read(
          `/api/v1/videos/${videoId}/permissions?cursor=` +
            encodeURIComponent(
              JSON.parse(
                (await read(`/api/v1/videos/${videoId}/permissions`)).text,
              ).next_cursor,
            ),
        )
      ).text,
    ).emails.includes(guest),
    "Failed revoke preserves real permission",
  );
  fail = "";
  await c.click(".permission-done");
  await c.wait(`!document.querySelector('dialog[aria-label^="Кому"]').open`);
  let allEmails = [];
  let cursor;
  do {
    const page = JSON.parse(
      (
        await read(
          `/api/v1/videos/${videoId}/permissions${cursor ? "?cursor=" + encodeURIComponent(cursor) : ""}`,
        )
      ).text,
    );
    allEmails.push(...page.emails);
    cursor = page.next_cursor;
  } while (cursor);
  check(
    !allEmails.includes(guest) &&
      allEmails.includes("unknown-" + token + "@example.com"),
    "Revoke changes only requested recipient",
  );
  for (const [width, height] of [
    [390, 844],
    [320, 568],
  ]) {
    await c.viewport(width, height, true);
    await c.click(".video-permissions > button");
    await c.wait(`document.querySelector('dialog[aria-label^="Кому"]').open`);
    check(
      await c.evaluate(
        `document.documentElement.scrollWidth<=innerWidth && document.querySelector('dialog[aria-label^="Кому"]').getBoundingClientRect().width<=innerWidth-32`,
      ),
      "Phone grant modal fits",
    );
    await c.screenshot(
      `creator-permissions-${firefox ? "firefox" : "chromium"}-${width}`,
    );
    await key("Escape", "\uE00C", 27);
  }
  await c.viewport(1920, 935);
  await c.click(".video-permissions > button");
  await c.fill("#permission-emails", "late-" + token + "@example.com");
  slow = true;
  await c.click(".permission-done");
  await c.wait(
    `document.querySelector('dialog[aria-label^="Кому"]').getAttribute('aria-busy')==='true'`,
  );
  await c.evaluate(
    `document.querySelector('.studio-navigation__exit').click()`,
  );
  await c.wait(`location.pathname==='/'`);
  await pause(900);
  slow = false;
  check(
    !(await c.evaluate(`!!document.querySelector('dialog[open]')`)),
    "Late write cannot restore a closed private modal",
  );
  await c.navigate(base + "/studio/upload?video=" + videoId);
  await c.wait(`!!document.querySelector('#video-title')`);
  check(
    (await write("/api/v1/auth/logout", {})) === 204,
    "Owner session ended",
  );
  await refetchProcessing();
  await c.wait(`!document.querySelector('.upload-dialog')`);
  check(
    await c.evaluate(
      `(()=>{const client=Object.values(document.querySelector('#app').__vue_app__._context.provides).find(x=>typeof x?.getQueryCache==='function');return !JSON.stringify(client.getQueryCache().getAll().map(q=>q.state.data)).includes(${JSON.stringify("unknown-" + token + "@example.com")}) && !document.body.textContent.includes(${JSON.stringify("unknown-" + token + "@example.com")});})()`,
    ),
    "Logout clears private recipients and thumbnail state from UI/cache",
  );
  check(
    calls
      .filter(
        (call) =>
          ["permissions", "playlists", "thumbnail"].includes(call.kind) &&
          call.method === "PUT",
      )
      .every((call) => call.csrf),
    "Creator writes use CSRF",
  );
  check(c.errors.length === 0, "No uncaught runtime errors");
  console.log(
    JSON.stringify(
      {
        status: "pass",
        assertions,
        engine: firefox ? "Firefox" : "Chromium",
        backend: "real isolated API/PostgreSQL/Nginx/RustFS/media-worker",
        screens: ["1920x935", "390x844", "320x568"],
      },
      null,
      2,
    ),
  );
} catch (error) {
  await c.screenshot(`creator-failure-${firefox ? "firefox" : "chromium"}`);
  console.error({ runtimeErrors: c.errors });
  throw error;
} finally {
  await c.close();
  for (const socket of sockets) socket.destroy();
  server.closeAllConnections();
  server.close();
}
