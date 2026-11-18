import assert from "node:assert/strict";
import { createServer, request as httpRequest } from "node:http";
import { readFile } from "node:fs/promises";
import { browser, pause } from "./cdp.mjs";
import { firefoxBrowser } from "./bidi.mjs";
let fixture;
for (let i = 0; i < 1200; i++) {
  try {
    fixture = JSON.parse(await readFile("/work/watch-fixture.json", "utf8"));
    if (fixture.videos?.public) break;
  } catch {}
  await pause(250);
}
if (!fixture?.videos?.public) throw new Error("Watch access fixture not ready");
const firefox = process.argv.includes("--firefox");
let meFailure = false,
  holdMe = false,
  releaseMe,
  holdId,
  releaseDetail,
  failureId,
  invalidMedia = false;
const calls = [],
  sockets = new Set();
const server = createServer(async (req, res) => {
  try {
    const api = req.url.startsWith("/api/"),
      identity = req.url === "/api/v1/auth/me",
      detail = /^\/api\/v1\/videos\/[0-9]+$/.test(req.url),
      id = detail ? req.url.split("/").at(-1) : undefined;
    if (api) calls.push(req.url);
    if ((identity && meFailure) || (detail && id === failureId)) {
      res
        .writeHead(503, { "content-type": "application/problem+json" })
        .end('{"code":"unavailable"}');
      return;
    }
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    const headers = new Headers();
    for (const name of ["cookie", "content-type", "x-csrftoken", "range"])
      if (req.headers[name]) headers.set(name, req.headers[name]);
    const r = await fetch(
      api
        ? "http://youtube-frontend-upload-gateway:8080" + req.url
        : "http://node:5173" + req.url,
      {
        method: req.method,
        headers,
        body: chunks.length ? Buffer.concat(chunks) : undefined,
        redirect: "manual",
      },
    );
    let body = Buffer.from(await r.arrayBuffer());
    if (detail && invalidMedia && r.status === 200)
      body = Buffer.from(
        body
          .toString()
          .replace(
            /"hls_url":"[^"]+"/,
            '\"hls_url\":\"https://invalid.example/foreign.m3u8\"',
          ),
      );
    if (identity && holdMe)
      await new Promise((resolve) => {
        releaseMe = resolve;
      });
    if (detail && id === holdId)
      await new Promise((resolve) => {
        releaseDetail = resolve;
      });
    res.statusCode = r.status;
    for (const name of ["content-type", "location", "cache-control"])
      if (r.headers.has(name)) res.setHeader(name, r.headers.get(name));
    if (r.headers.getSetCookie().length)
      res.setHeader("set-cookie", r.headers.getSetCookie());
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

await new Promise((resolve) => server.listen(4189, "127.0.0.1", resolve));
const c = firefox ? await firefoxBrowser() : await browser(),
  base = "http://127.0.0.1:4189",
  v = fixture.videos;
let assertions = 0;
const check = (x, message) => {
  assert.ok(x, message);
  assertions++;
};
const detailCalls = () =>
  calls.filter((x) => /^\/api\/v1\/videos\/[0-9]+$/.test(x));
async function until(predicate) {
  for (let i = 0; i < 100; i++) {
    if (await predicate()) return;
    await pause(100);
  }
  throw new Error("Watch API condition timed out");
}
async function navigate(path = "/") {
  await c.navigate(base + path);
}
async function route(path) {
  await c.evaluate(
    `document.querySelector('#app').__vue_app__.config.globalProperties.$router.push(${JSON.stringify(path)})`,
  );
}
async function login(person = "owner") {
  await navigate("/login?returnTo=%2F");
  await c.fill("#login-email", fixture.people[person].email);
  await c.fill("#login-password", "abcdef");
  await c.click(".auth-card button[type=submit]");
  await c.wait(
    `location.pathname==='/'&&!!document.querySelector('.avatar-button')`,
  );
}
async function refreshSession() {
  await c.evaluate(
    `(async()=>{const client=Object.values(document.querySelector('#app').__vue_app__._context.provides).find(x=>typeof x?.getQueryCache==='function');await client.refetchQueries({queryKey:['session']})})()`,
  );
}
async function player(id) {
  await navigate("/watch?v=" + id);
  await c.wait(
    `document.querySelector('.player__start')?.disabled===false&&!!document.querySelector('.watch-page__metadata h1')`,
    30000,
  );
}
async function denied() {
  await c.wait(
    `document.querySelector('.watch-page .page-state h1')?.textContent==='Видео недоступно'`,
  );
}
async function ownerRequest(path, method = "GET", body) {
  const host = "http://youtube-frontend-upload-gateway:8080/api/v1";
  const cookies = new Map();
  const absorb = (r) =>
    r.headers.getSetCookie().forEach((x) => {
      const p = x.split(";")[0],
        i = p.indexOf("=");
      cookies.set(p.slice(0, i), p.slice(i + 1));
    });
  absorb(await fetch(host + "/auth/me"));
  const headers = () => ({
    cookie: [...cookies].map(([k, x]) => k + "=" + x).join("; "),
    "x-csrftoken": decodeURIComponent(cookies.get("csrftoken")),
    "content-type": "application/json",
  });
  const response = await fetch(host + "/auth/login", {
    method: "POST",
    headers: headers(),
    body: JSON.stringify({
      email: fixture.people.owner.email,
      password: "abcdef",
    }),
  });
  assert.equal(response.status, 200);
  absorb(response);
  const result = await fetch(host + path, {
    method,
    headers: headers(),
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  assert.equal(result.status, 200);
  return result.json();
}
async function permission(allowed) {
  await ownerRequest("/videos/" + v.private + "/permissions", "PUT", {
    email: fixture.people.invited.email,
    allowed,
  });
}
async function logout() {
  await c.click(".avatar-button");
  await c.click("#logout-button");
  await c.wait(`!document.querySelector('.avatar-button')`);
}
async function refreshVideo() {
  await c.evaluate(
    `(async()=>{const client=Object.values(document.querySelector('#app').__vue_app__._context.provides).find(x=>typeof x?.getQueryCache==='function');await client.refetchQueries({predicate:q=>q.queryKey[0]==='video'});})()`,
  );
}
async function play() {
  await c.evaluate(
    `document.querySelector('.player video').muted=true;document.querySelector('.player video').play()`,
  );
  await c.wait(`document.querySelector('.player video').currentTime>0.2`);
}
async function exerciseAccess() {
  await c.wait(
    `document.querySelector('.player__start')?.disabled===false`,
    30000,
  );
  await play();
  await c.evaluate(
    `window.identityPlayer=document.querySelector('.player video')`,
  );
  meFailure = true;
  await refreshSession();
  await c.wait(
    `document.querySelector('.page-state h1')?.textContent==='Не удалось проверить аккаунт'`,
  );
  check(
    await c.evaluate(
      `!document.querySelector('.watch-page__metadata')&&!document.querySelector('.player')&&window.identityPlayer.paused&&!window.identityPlayer.getAttribute('src')&&document.title==='YouTube'`,
    ),
    "Identity failure clears private title, metadata, source and sound",
  );
  const failedCount = detailCalls().length;
  await c.click(".page-state button");
  await pause(200);
  check(
    detailCalls().length === failedCount,
    "Failed identity retry cannot fetch video as guest",
  );
  meFailure = false;
  await c.click(".page-state button");
  await c.wait(`!!document.querySelector('.watch-page__metadata')`);
  check(true, "Identity retry recovers confirmed private viewer");
  await logout();
  await player(v.public);
  check(
    await c.evaluate(
      `document.querySelector('.watch-page h1').textContent.includes('<script>')&&!document.querySelector('.watch-page h1 script')&&!document.querySelector('.watch-page__description img')&&!window.frontendInject`,
    ),
    "Metadata and description are inert user text",
  );
  await c.click(".watch-page__description button");
  check(
    await c.evaluate(
      `document.querySelector('.watch-page__description button').getAttribute('aria-expanded')==='true'`,
    ),
    "Description expands",
  );
  await c.click(".watch-page__description button");
  check(
    await c.evaluate(
      `document.querySelector('.watch-page__description button').getAttribute('aria-expanded')==='false'`,
    ),
    "Description collapses",
  );
  for (const id of [
    v.private,
    v.hidden,
    v.draft,
    v.pending,
    "9223372036854775807",
  ]) {
    const before = calls.filter(
      (x) =>
        x.startsWith("/api/v1/videos/" + id + "/") &&
        (x.includes("/hls/") || x.includes("/preview.webp")),
    ).length;
    await navigate("/watch?v=" + id);
    await denied();
    check(
      await c.evaluate(
        `!document.querySelector('.watch-page__metadata')&&!document.querySelector('.player')&&!document.querySelector('.watch-page__author')`,
      ),
      "Denied direct URL discloses no protected metadata",
    );
    check(
      calls.filter(
        (x) =>
          x.startsWith("/api/v1/videos/" + id + "/") &&
          (x.includes("/hls/") || x.includes("/preview.webp")),
      ).length === before,
      "Denied metadata cannot start media",
    );
  }
  check(
    detailCalls().includes("/api/v1/videos/9223372036854775807"),
    "Maximum int64 reaches API with exact digits",
  );
  check(
    await c.evaluate(
      `new URL(document.querySelector('.watch-page__login').href).searchParams.get('returnTo')==='/watch?v=9223372036854775807'`,
    ),
    "Guest login preserves internal return URL",
  );
  for (const suffix of [
    "v=0",
    "v=-1",
    "v=01",
    "v=9223372036854775808",
    "v=abc",
    "v=1&v=2",
    "v=%3Cscript%3E",
  ]) {
    const before = detailCalls().length;
    await navigate("/watch?" + suffix);
    await denied();
    check(
      detailCalls().length === before,
      "Invalid or repeated ID is rejected before metadata API",
    );
  }
  failureId = v.public;
  await navigate("/watch?v=" + v.public);
  await c.wait(
    `document.querySelector('.page-state h1')?.textContent==='Не удалось загрузить видео'`,
  );
  check(
    !(await c.evaluate(
      `!!document.querySelector('.watch-page__metadata')||!!document.querySelector('.player')`,
    )),
    "Metadata 503 shows error without previous content",
  );
  failureId = undefined;
  await c.click(".page-state button");
  await c.wait(
    `document.querySelector('.player__start')?.disabled===false`,
    30000,
  );
  check(true, "Metadata retry restores real view");
  await c.click(".masthead__brand");
  invalidMedia = true;
  await navigate("/watch?v=" + v.public);
  await c.wait(
    `document.querySelector('.page-state h1')?.textContent==='Не удалось загрузить видео'`,
  );
  check(
    !(await c.evaluate(`!!document.querySelector('.player')`)) &&
      !(await c.evaluate(`!!document.querySelector('.watch-page__metadata')`)),
    "Foreign media URL is rejected before player creation",
  );
  invalidMedia = false;
  await c.click(".page-state button");
  await c.wait(`!!document.querySelector('.watch-page__metadata')`);
  await login("stranger");
  await navigate("/watch?v=" + v.private);
  await denied();
  check(
    !(await c.evaluate(`!!document.querySelector('.watch-page__metadata')`)),
    "Stranger cannot view private metadata",
  );
  await login("owner");
  await player(v.private);
  check(
    await c.evaluate(
      `!!document.querySelector('.watch-page__private-discussion')&&!document.querySelector('.discussion')`,
    ),
    "Private view has no public discussion",
  );
  for (const id of [v.hidden, v.draft, v.pending]) {
    await navigate("/watch?v=" + id);
    await denied();
    check(
      !(await c.evaluate(`!!document.querySelector('.player')`)),
      "Owner ordinary route still denies hidden draft and unready video",
    );
  }
  await player(v.public);
  await play();
  await c.evaluate(`window.oldPlayer=document.querySelector('.player video')`);
  holdId = v.private;
  await route("/watch?v=" + v.private);
  await until(() => !!releaseDetail);
  check(
    await c.evaluate(
      `!document.querySelector('.watch-page__metadata')&&!document.querySelector('.player')&&window.oldPlayer.paused&&!window.oldPlayer.getAttribute('src')`,
    ),
    "Changing URL clears former title and source while new metadata waits",
  );
  await route("/watch?v=" + v.silent);
  await c.wait(
    `document.querySelector('.watch-page h1')?.textContent==='Вертикальное видео без звука'`,
  );
  holdId = undefined;
  releaseDetail();
  releaseDetail = undefined;
  await pause(300);
  check(
    await c.evaluate(
      `new URLSearchParams(location.search).get('v')==='${v.silent}'&&document.querySelector('.watch-page h1').textContent==='Вертикальное видео без звука'`,
    ),
    "Late previous response cannot replace current video",
  );
  await permission(true);
  await login("invited");
  await player(v.private);
  await play();
  await c.evaluate(`window.backPlayer=document.querySelector('.player video')`);
  await c.click(".masthead__brand");
  await c.wait(`location.pathname==='/'`);
  await permission(false);
  holdId = v.private;
  await c.evaluate(
    `window.privateFlashed=false;window.backObserver=new MutationObserver(()=>{if(location.pathname==='/watch'&&document.querySelector('.watch-page__metadata'))window.privateFlashed=true;});window.backObserver.observe(document.body,{childList:true,subtree:true});history.back()`,
  );
  await until(() => !!releaseDetail);
  check(
    await c.evaluate(
      `!window.privateFlashed&&!document.querySelector('.watch-page__metadata')&&window.backPlayer.paused&&!window.backPlayer.getAttribute('src')`,
    ),
    "Back after revocation cannot flash inactive private cache",
  );
  holdId = undefined;
  releaseDetail();
  releaseDetail = undefined;
  await denied();
  check(
    await c.evaluate(
      `!window.privateFlashed&&!document.querySelector('.watch-page__metadata')`,
    ),
    "Fresh denial keeps stale private data hidden",
  );
  await c.evaluate(`window.backObserver.disconnect()`);
  await permission(true);
  await c.reload();
  await c.wait(
    `document.querySelector('.player__start')?.disabled===false`,
    30000,
  );
  await play();
  await c.evaluate(
    `window.revokedPlayer=document.querySelector('.player video')`,
  );
  await permission(false);
  await refreshVideo();
  await denied();
  check(
    await c.evaluate(
      `!document.querySelector('.player')&&window.revokedPlayer.paused&&!window.revokedPlayer.getAttribute('src')`,
    ),
    "Permission revocation stops an active player on metadata recheck",
  );
  await permission(true);
  await c.reload();
  await c.wait(
    `document.querySelector('.player__start')?.disabled===false`,
    30000,
  );
  await play();
  await c.evaluate(
    `window.expiredPlayer=document.querySelector('.player video');(async()=>{const csrf=decodeURIComponent(document.cookie.split('; ').find(x=>x.startsWith('csrftoken=')).slice(10));await fetch('/api/v1/auth/logout',{method:'POST',headers:{'x-csrftoken':csrf}})})()`,
  );
  await refreshSession();
  await denied();
  check(
    await c.evaluate(
      `!document.querySelector('.watch-page__metadata')&&window.expiredPlayer.paused&&!window.expiredPlayer.getAttribute('src')&&!document.querySelector('.avatar-button')`,
    ),
    "Lost server session clears private metadata and media",
  );
  check(
    await c.evaluate(
      `localStorage.getItem('youtube.guest-progress.v1:${v.private}')===null`,
    ),
    "Private account position never flushes to guest",
  );
  await c.viewport(390, 844);
  await player(v.public);
  await c.click(".watch-page__description button");
  check(
    await c.evaluate(
      `document.querySelector('.watch-page__description button').getAttribute('aria-expanded')==='true'`,
    ),
    "Phone description expands",
  );
  await c.click(".watch-page__author");
  await c.wait(`!!document.querySelector('.channel-page h1')`);
  check(
    await c.evaluate(`location.pathname==='/@${fixture.people.owner.tag}'`),
    "Phone author link opens correct channel",
  );
  check(
    c.errors.filter(
      (x) =>
        !(
          x.includes("downloadable font: download failed") &&
          x.includes("status=2152398850")
        ),
    ).length === 0,
    "No runtime exceptions",
  );
  console.log(
    JSON.stringify(
      {
        status: "pass",
        assertions,
        engine: firefox ? "Firefox" : "Chromium",
        backend: "isolated real metadata, permissions, sessions and HLS",
      },
      null,
      2,
    ),
  );
}
try {
  await c.viewport(1280, 900);
  await login();
  holdMe = true;
  const before = detailCalls().length;
  await navigate("/watch?v=" + v.private);
  await until(() => !!releaseMe);
  await pause(300);
  check(
    detailCalls().length === before &&
      !(await c.evaluate(
        `!!document.querySelector('.watch-page__metadata')||!!document.querySelector('.player')`,
      )),
    "Pending identity cannot request or render private metadata",
  );
  holdMe = false;
  releaseMe();
  releaseMe = undefined;
  await c.wait(`!!document.querySelector('.watch-page__metadata')`);
  check(true, "Confirmed owner opens direct private URL");
  if (process.argv.includes("--probe-session")) {
    console.log(
      JSON.stringify({ status: "pass", assertions, probe: "identity" }),
    );
  } else await exerciseAccess();
} catch (error) {
  console.error({ runtime: c.errors, lastApi: calls.slice(-4) });
  throw error;
} finally {
  holdMe = false;
  meFailure = false;
  invalidMedia = false;
  failureId = undefined;
  holdId = undefined;
  releaseMe?.();
  releaseDetail?.();
  try {
    await permission(true);
  } catch {}
  await c.close();
  for (const socket of sockets) socket.destroy();
  server.closeAllConnections();
  server.close();
}
