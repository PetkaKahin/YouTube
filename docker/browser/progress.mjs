import assert from "node:assert/strict";
import { createServer, request as httpRequest } from "node:http";
import { readFile } from "node:fs/promises";
import { browser, pause } from "./cdp.mjs";
import { firefoxBrowser } from "./bidi.mjs";
let fixture;
for (let i = 0; i < 1200; i++) {
  try {
    fixture = JSON.parse(await readFile("/work/watch-fixture.json", "utf8"));
    if (fixture.jobs && fixture.playlists?.public) break;
  } catch {}
  await pause(100);
}
if (!fixture?.jobs) throw new Error("Preview fixture not ready");
const firefox = process.argv.includes("--firefox");
let failure = false,
  delay = false,
  meFailure = false,
  progressFailure = false,
  hold = false,
  dropCsrf = false,
  releaseHold;
const writes = [];
const calls = [],
  sockets = new Set();
const server = createServer(async (req, res) => {
  try {
    const api = req.url.startsWith("/api/"),
      detail = req.url === "/api/v1/videos/" + fixture.videos.public;
    if (api) calls.push({ url: req.url, method: req.method });
    if ((detail && failure) || (meFailure && req.url === "/api/v1/auth/me")) {
      res
        .writeHead(503, { "content-type": "application/problem+json" })
        .end('{"code":"unavailable"}');
      return;
    }
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    const progress = req.url.endsWith("/progress") && req.method === "PUT";
    let write;
    if (progress) {
      const body = JSON.parse(Buffer.concat(chunks).toString());
      write = {
        id: req.url.split("/")[4],
        position: body.position_seconds,
        fields: Object.keys(body),
        status: undefined,
      };
      writes.push(write);
      if (progressFailure) {
        write.status = 503;
        res
          .writeHead(503, { "content-type": "application/problem+json" })
          .end('{"code":"unavailable"}');
        return;
      }
      if (hold)
        await new Promise((resolve) => {
          releaseHold = resolve;
        });
    }
    const headers = new Headers();
    for (const name of ["cookie", "content-type", "x-csrftoken", "range"])
      if (req.headers[name]) headers.set(name, req.headers[name]);
    if (progress && dropCsrf) headers.delete("x-csrftoken");
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
    if (write) write.status = r.status;
    const body = Buffer.from(await r.arrayBuffer());
    if (detail && delay) await pause(650);
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
const publicLink = `.video-card__preview[href="/watch?v=${v.public}"]`;
const card = (id = v.public) =>
  `document.querySelector('.video-card__preview[href="/watch?v=${id}"]').closest('.video-card')`;
const localKey = "youtube.guest-progress.v1:" + v.public;
let assertions = 0;
function check(value, message) {
  assert.ok(value, message);
  assertions++;
}
async function until(predicate) {
  for (let i = 0; i < 100; i++) {
    if (await predicate()) return;
    await pause(100);
  }
  throw new Error("Progress condition timed out");
}
async function navigate(path = "/") {
  await c.navigate(base + path);
}
async function home() {
  await navigate();
  await c.wait(
    `!!document.querySelector(${JSON.stringify(publicLink)}) && !!document.querySelector('.masthead .avatar-button')`,
  );
}
async function api(path, method = "GET", body) {
  return JSON.parse(
    await c.evaluate(
      `(async()=>{const cookie=document.cookie.split('; ').find(x=>x.startsWith('csrftoken='));const r=await fetch('/api/v1'+${JSON.stringify(path)},{method:${JSON.stringify(method)},headers:{'content-type':'application/json',...(cookie?{'x-csrftoken':decodeURIComponent(cookie.slice(10))}:{})},body:${body === undefined ? "undefined" : JSON.stringify(JSON.stringify(body))}});return JSON.stringify({status:r.status,data:r.status===204?null:await r.json()});})()`,
    ),
  );
}
async function position(id = v.public) {
  return (
    (await api("/videos/" + id)).data.watch_progress?.position_seconds ?? null
  );
}
async function saved(value, id = v.public) {
  await until(async () => {
    const current = await position(id);
    return current !== null && Math.abs(current - value) < 0.15;
  });
}
async function login(person = "owner") {
  await navigate("/login?returnTo=%2F");
  await c.fill("#login-email", fixture.people[person].email);
  await c.fill("#login-password", "abcdef");
  await c.click(".auth-card button[type=submit]");
  await c.wait(
    `location.pathname==='/' && !!document.querySelector('.masthead .avatar-button') && !!document.querySelector(${JSON.stringify(publicLink)})`,
  );
}
async function logout() {
  await c.click(".avatar-button");
  await c.click("#logout-button");
  await c.wait(`!document.querySelector('.masthead .avatar-button')`);
}
async function player(id = v.public) {
  await navigate("/watch?v=" + id);
  await c.wait(
    `document.querySelector('.player__start')?.disabled===false && Number(document.querySelector('.player__seek').max)>9`,
    30000,
  );
}
async function seek(value) {
  await c.fill(".player__seek", String(value));
  await c.wait(
    `Math.abs(document.querySelector('.player video').currentTime-${value})<0.15`,
  );
  await pause(120);
}
async function watchedBar() {
  await c.wait(`${card()}.querySelector('.video-card__watched')!==null`);
  return c.evaluate(
    `Number(${card()}.querySelector('.video-card__watched').getAttribute('aria-valuenow'))`,
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
async function visibility(value) {
  await ownerRequest("/videos/" + v.public + "/settings", "PUT", {
    title: "Проверка позиции",
    description: "Описание",
    visibility: value,
  });
}
try {
  await c.viewport(1280, 900);
  await navigate();
  await c.wait(`!!document.querySelector(${JSON.stringify(publicLink)})`);
  await c.evaluate(`localStorage.setItem(${JSON.stringify(localKey)},'6')`);
  await api("/auth/me");
  const suffix = crypto.randomUUID().replaceAll("-", "").slice(0, 16);
  fixture.people.stranger = {
    email: "progress-" + suffix + "@example.com",
    tag: "progress_" + suffix,
  };
  const registered = await api("/auth/register", "POST", {
    name: "Другой зритель",
    email: fixture.people.stranger.email,
    tag: fixture.people.stranger.tag,
    password: "abcdef",
    password_confirmation: "abcdef",
  });
  assert.equal(registered.status, 201);
  await login();
  // Clear only this account's fixture progress so sequential engines start alike.
  await ownerRequest("/videos/" + v.public + "/progress", "PUT", {
    position_seconds: 0,
  });
  await home();
  check(
    !(await c.evaluate(`!!document.querySelector('.video-card__watched')`)),
    "Guest position is not merged into an account",
  );
  const beforePreview = writes.length;
  await c.evaluate(
    `${card()}.dispatchEvent(new PointerEvent('pointerenter',{pointerType:'mouse'}))`,
  );
  await c.wait(
    `document.querySelector('.hover-preview video')?.currentTime>0.2`,
    30000,
  );
  await c.fill(".hover-preview input", "3");
  await c.evaluate(
    `${card()}.dispatchEvent(new PointerEvent('pointerleave',{pointerType:'mouse'}))`,
  );
  check(
    writes.length === beforePreview && (await position()) === 0,
    "Authenticated preview never writes progress",
  );

  await player();
  check(
    await c.evaluate(`document.querySelector('.player video').currentTime<0.5`),
    "Account with zero progress starts at zero instead of inheriting guest progress",
  );
  await c.evaluate(
    `document.querySelector('.player video').muted=true;document.querySelector('.player video').play()`,
  );
  await c.wait(`document.querySelector('.player video').currentTime>0.2`);
  await c.fill(".player__seek", "8");
  await c.evaluate(`document.querySelector('.player video').pause()`);
  await saved(8);
  check(true, "Real main HLS playback saves account position");
  check(
    writes.every(
      (x) =>
        x.id === v.public &&
        x.fields.length === 1 &&
        x.fields[0] === "position_seconds",
    ),
    "Exact bigint path and position-only body reach real API",
  );
  check(
    await c.evaluate(`localStorage.getItem(${JSON.stringify(localKey)})==='6'`),
    "Account writes do not change guest storage",
  );
  const pausedWrites = writes.length;
  await c.evaluate(
    `const v=document.querySelector('.player video');for(let i=0;i<30;i++)v.dispatchEvent(new Event('timeupdate'))`,
  );
  await pause(250);
  check(
    writes.length === pausedWrites,
    "Regular events are throttled and unchanged positions are deduplicated",
  );
  await seek(2);
  await saved(2);
  check(true, "Seeking backwards replaces the saved position");
  await seek(0);
  await saved(0);
  check(true, "Seeking to zero clears the watched part");

  hold = true;
  const burstStart = writes.length;
  await seek(5);
  await until(() => !!releaseHold);
  await seek(6);
  await seek(7);
  check(
    writes.length === burstStart + 1,
    "Only one progress request runs while server is slow",
  );
  hold = false;
  releaseHold();
  releaseHold = undefined;
  await saved(7);
  check(
    writes
      .slice(burstStart)
      .map((x) => x.position)
      .join(",") === "5,7",
    "Slow writes keep only the newest pending position",
  );
  await seek(4);
  await saved(4);
  await c.evaluate(
    `document.querySelector('.player video').muted=true;document.querySelector('.player video').play()`,
  );
  await c.wait(`document.querySelector('.player video').currentTime>4.4`);
  await c.click(".masthead__brand");
  await until(async () => (await position()) > 4.3);
  check(true, "SPA navigation flushes the final position after child unmount");
  await c.wait(
    `location.pathname==='/' && !!document.querySelector(${JSON.stringify(publicLink)})`,
  );
  const canonical = (await api("/videos/" + v.public)).data.watch_progress;
  const percentage = Math.round(
    (canonical.position_seconds / canonical.duration_seconds) * 100,
  );
  await c.wait(
    `${card()}.querySelector('.video-card__watched')?.getAttribute('aria-valuenow')==='${percentage}'`,
  );
  check(
    percentage > 0 && percentage < 100,
    "Feed displays canonical account progress",
  );
  await visibility("public");
  await navigate(
    "/results?search_query=" + encodeURIComponent("Проверка позиции"),
  );
  await c.wait(`!!document.querySelector(${JSON.stringify(publicLink)})`);
  check(
    (await watchedBar()) === percentage,
    "Search displays the same account progress: " +
      JSON.stringify({ expected: percentage, actual: await watchedBar() }),
  );
  await navigate("/@" + fixture.people.owner.tag);
  await c.wait(`!!document.querySelector(${JSON.stringify(publicLink)})`);
  check(
    (await watchedBar()) === percentage,
    "Channel displays the same account progress",
  );
  const playlistResult = await api("/playlists/" + fixture.playlists.public);
  assert.equal(playlistResult.status, 200, "Nonempty playlist returns 200");
  await navigate(
    "/@" +
      fixture.people.owner.tag +
      "/playlists?list=" +
      fixture.playlists.public,
  );
  await c.wait(`!!document.querySelector(${JSON.stringify(publicLink)})`);
  check(
    (await watchedBar()) === percentage,
    "Playlist cards display the same account progress",
  );

  await ownerRequest("/videos/" + v.public + "/progress", "PUT", {
    position_seconds: 2.5,
  });
  await home();
  check(
    (await watchedBar()) < percentage,
    "Another authenticated session updates the position seen by this device",
  );
  await logout();
  await c.evaluate(`localStorage.clear()`);
  await login();
  check(
    (await watchedBar()) > 0,
    "Fresh session with empty local storage reads server progress",
  );
  await logout();
  await login("stranger");
  check(
    !(await c.evaluate(`${card()}.querySelector('.video-card__watched')`)),
    "Another account cannot see owner progress",
  );
  check(
    (await position()) === null,
    "Another account detail has no owner position",
  );
  await logout();
  await login();

  await player();
  progressFailure = true;
  await seek(6);
  await c.wait(
    `!!document.querySelector('.watch-page__progress-error button')`,
  );
  check(
    (await position()) === 2.5,
    "Failed write leaves last server position intact",
  );
  check(
    await c.evaluate(`!!document.querySelector('.player video')`),
    "Save failure leaves main player usable",
  );
  progressFailure = false;
  await c.click(".watch-page__progress-error button");
  await saved(6);
  await c.wait(`!document.querySelector('.watch-page__progress-error')`);
  check(true, "Explicit retry persists position and clears error");
  dropCsrf = true;
  await seek(8);
  await c.wait(
    `!!document.querySelector('.watch-page__progress-error button')`,
  );
  check(
    writes.at(-1).status === 403 && (await position()) === 6,
    "Real CSRF rejection leaves server position unchanged",
  );
  check(
    await c.evaluate(`!!document.querySelector('.player video')`),
    "CSRF error does not falsely withdraw video access",
  );
  dropCsrf = false;
  await c.click(".watch-page__progress-error button");
  await saved(8);
  check(true, "Retry with valid CSRF writes successfully");

  await logout();
  await login("invited");
  await player(v.private);
  await seek(3);
  await saved(3, v.private);
  const oldPosition = await ownerRequest("/videos/" + v.private);
  check(
    oldPosition.watch_progress === null,
    "Invited private viewer position does not appear in owner detail",
  );
  await logout();
  await login("stranger");
  await player();
  await seek(4);
  await saved(4);
  await visibility("private");
  await seek(5);
  await c.wait(`!document.querySelector('.player video')`);
  check(
    await c.evaluate(
      `document.querySelector('main').textContent.includes('Видео недоступно')`,
    ),
    "Real progress 404 removes a video after access is withdrawn",
  );
  const withdrawn = writes.length;
  await pause(250);
  check(
    writes.length === withdrawn,
    "Access loss does not retry forbidden progress",
  );
  await visibility("public");

  await logout();
  await login();
  await player();
  hold = true;
  await seek(7);
  await until(() => !!releaseHold);
  await seek(9);
  const beforeLogout = writes.length;
  await logout();
  hold = false;
  releaseHold();
  releaseHold = undefined;
  await pause(400);
  check(writes.length === beforeLogout, "Logout drops queued account writes");
  await login("stranger");
  check(
    (await position()) === 4,
    "Old account completion cannot overwrite new account progress",
  );
  const cached = JSON.parse(
    await c.evaluate(
      `(()=>{const client=Object.values(document.querySelector('#app').__vue_app__._context.provides).find(x=>typeof x?.getQueryCache==='function');return JSON.stringify(client.getQueryCache().getAll().filter(x=>x.queryKey[0]!=='session').map(x=>String(x.queryKey[1])));})()`,
    ),
  );
  check(
    cached.every((x) => !x.endsWith(":" + fixture.people.owner.id)),
    "Late response cannot restore previous account query cache",
  );
  const errors = c.errors.filter(
    (x) =>
      !(
        x.includes("downloadable font: download failed") &&
        x.includes("status=2152398850")
      ),
  );
  check(errors.length === 0, "No runtime exceptions");
  console.log(
    JSON.stringify(
      {
        status: "pass",
        assertions,
        engine: firefox ? "Firefox" : "Chromium",
        backend: "isolated real sessions/progress/HLS",
      },
      null,
      2,
    ),
  );
} catch (error) {
  console.error({
    runtime: c.errors,
    writes: writes.map((x) => ({ position: x.position, status: x.status })),
  });
  throw error;
} finally {
  hold = false;
  releaseHold?.();
  progressFailure = false;
  try {
    await visibility("public");
  } catch {}
  await c.close();
  for (const socket of sockets) socket.destroy();
  server.closeAllConnections();
  server.close();
}
