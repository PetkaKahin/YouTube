import assert from "node:assert/strict";
import { createServer, request as httpRequest } from "node:http";
import { readFile } from "node:fs/promises";
import { browser, pause } from "./cdp.mjs";
import { firefoxBrowser } from "./bidi.mjs";
let fixture;
for (let i = 0; i < 1200; i++) {
  try {
    fixture = JSON.parse(await readFile("/work/watch-fixture.json", "utf8"));
    if (fixture.jobs) break;
  } catch {}
  await pause(100);
}
if (!fixture?.jobs) throw new Error("Preview fixture not ready");
const firefox = process.argv.includes("--firefox");
let failure = false,
  delay = false,
  meFailure = false;
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
const card = (id) =>
  `document.querySelector('.video-card__preview[href="/watch?v=${id}"]').closest('.video-card')`;
const key = "youtube.guest-progress.v1:" + v.public;
let assertions = 0;
const check = (x, message) => {
  assert.ok(x, message);
  assertions++;
};
const mediaCount = () => calls.filter((x) => x.url.includes("/hls/")).length;
async function navigate(path = "/") {
  await c.navigate(base + path);
}
async function home() {
  await navigate();
  await c.wait(`!!document.querySelector(${JSON.stringify(publicLink)})`);
}
async function enter(id = v.public) {
  await c.evaluate(
    `${card(id)}.dispatchEvent(new PointerEvent('pointerenter',{pointerType:'mouse'}))`,
  );
}
async function leave(id = v.public) {
  await c.evaluate(
    `${card(id)}.dispatchEvent(new PointerEvent('pointerleave',{pointerType:'mouse'}))`,
  );
  await c.wait(`!document.querySelector('.hover-preview')`);
}
async function playing(id = v.public) {
  await c.wait(
    `document.querySelector('.hover-preview')?.dataset.previewId==='${id}' && document.querySelector('.hover-preview video')?.currentTime>0.2`,
    30000,
  );
}
async function ownerVisibility(visibility) {
  const host = "http://youtube-frontend-upload-gateway:8080";
  const cookies = new Map();
  const absorb = (r) =>
    r.headers.getSetCookie().forEach((x) => {
      const p = x.split(";")[0],
        i = p.indexOf("=");
      cookies.set(p.slice(0, i), p.slice(i + 1));
    });
  absorb(await fetch(host + "/api/v1/auth/me"));
  const headers = () => ({
    cookie: [...cookies].map(([k, x]) => k + "=" + x).join("; "),
    "x-csrftoken": decodeURIComponent(cookies.get("csrftoken")),
    "content-type": "application/json",
  });
  const login = await fetch(host + "/api/v1/auth/login", {
    method: "POST",
    headers: headers(),
    body: JSON.stringify({
      email: fixture.people.owner.email,
      password: "abcdef",
    }),
  });
  assert.equal(login.status, 200);
  absorb(login);
  const response = await fetch(
    host + "/api/v1/videos/" + v.public + "/settings",
    {
      method: "PUT",
      headers: headers(),
      body: JSON.stringify({
        title: "Проверка предпросмотра",
        description: "Описание",
        visibility,
      }),
    },
  );
  assert.equal(response.status, 200);
}
async function login() {
  await navigate("/login?returnTo=%2F");
  await c.fill("#login-email", fixture.people.owner.email);
  await c.fill("#login-password", "abcdef");
  await c.click(".auth-card button[type=submit]");
  await c.wait(
    `!!document.querySelector('.masthead .avatar-button') && !!document.querySelector('.video-card')`,
  );
}
async function mainPlayer(id = v.public) {
  await navigate("/watch?v=" + id);
  await c.wait(
    `document.querySelector('.player__start')?.disabled===false && document.querySelector('.player video').duration>0 && Number(document.querySelector('.player__seek').max)>9`,
    30000,
  );
}
try {
  await c.viewport(1920, 935);
  await home();
  check(mediaCount() === 0, "No HLS preload before hover");
  await c.evaluate(
    `${card(v.public)}.dispatchEvent(new PointerEvent('pointerenter',{pointerType:'touch'}))`,
  );
  await pause(250);
  check(
    !(await c.evaluate(`!!document.querySelector('.hover-preview')`)),
    "Touch pointer does not autoplay",
  );
  await enter();
  await playing();
  check(
    await c.evaluate(
      `document.querySelector('.hover-preview video').muted===true`,
    ),
    "Preview autoplays muted",
  );
  check(
    await c.evaluate(
      `!document.querySelector('.hover-preview input').closest('a')`,
    ),
    "Interactive controls are outside watch link",
  );
  check(mediaCount() > 0, "Real HLS master and fragments loaded");
  check(
    await c.evaluate(
      `document.querySelector('.hover-preview video').videoHeight>0`,
    ),
    "Real preview frames decoded",
  );
  const master = await (
    await fetch(
      "http://youtube-frontend-upload-gateway:8080/api/v1/videos/" +
        v.public +
        "/hls/master.m3u8",
    )
  ).text();
  const heights = [...master.matchAll(/RESOLUTION=\d+x(\d+)/g)].map((x) =>
    Number(x[1]),
  );
  check(
    heights.length > 0 &&
      (await c.evaluate(
        `document.querySelector('.hover-preview video').videoHeight`,
      )) === Math.min(...heights),
    "Preview selects lowest available video resolution",
  );
  const maximum = await c.evaluate(
    `document.querySelector('.hover-preview input').max`,
  );
  await c.fill(".hover-preview input", "3");
  await c.wait(
    `document.querySelector('.hover-preview video').currentTime>=2.9`,
  );
  check(
    await c.evaluate(`location.pathname==='/'`),
    "Preview seek does not navigate",
  );
  check(
    await c.evaluate(`localStorage.getItem(${JSON.stringify(key)})===null`),
    "Preview play and seek do not save main progress",
  );
  await c.click(".hover-preview__mute");
  check(
    !(await c.evaluate(`document.querySelector('.hover-preview video').muted`)),
    "Preview sound toggle works",
  );
  if (
    await c.evaluate(`!!document.querySelector('.hover-preview__captions')`)
  ) {
    await c.click(".hover-preview__captions");
    check(
      await c.evaluate(
        `document.querySelector('.hover-preview__captions').getAttribute('aria-pressed')==='true'`,
      ),
      "Available preview subtitles toggle",
    );
  }
  await c.evaluate(
    `window.oldPreview=document.querySelector('.hover-preview video')`,
  );
  await enter(v.silent);
  await playing(v.silent);
  check(
    await c.evaluate(
      `document.querySelectorAll('.hover-preview').length===1 && window.oldPreview.paused`,
    ),
    "Second card stops first preview",
  );
  await leave(v.silent);
  check(
    await c.evaluate(
      `window.oldPreview.paused && !window.oldPreview.getAttribute('src')`,
    ),
    "Stopped preview releases media source",
  );
  delay = true;
  await enter();
  await c.wait(`!!document.querySelector('.hover-preview')`);
  await leave();
  const before = mediaCount();
  await pause(800);
  delay = false;
  check(
    mediaCount() === before &&
      !(await c.evaluate(`!!document.querySelector('.hover-preview')`)),
    "Late metadata cannot restart preview after leaving",
  );
  failure = true;
  await enter();
  await pause(500);
  check(
    !(await c.evaluate(`!!document.querySelector('.hover-preview')`)) &&
      (await c.evaluate(
        `!!document.querySelector(${JSON.stringify(publicLink)})`,
      )),
    "Metadata failure falls back to watchable card",
  );
  failure = false;
  await c.evaluate(
    `${card(v.public)}.dispatchEvent(new PointerEvent('pointerleave',{pointerType:'mouse'}))`,
  );
  await mainPlayer();
  await c.evaluate(
    `document.querySelector('.player video').muted=true;document.querySelector('.player video').play()`,
  );
  await c.wait(`document.querySelector('.player video').currentTime>0.2`);
  await c.fill(".player__seek", "6");
  await c.evaluate(`document.querySelector('.player video').pause()`);
  await c.wait(`Number(localStorage.getItem(${JSON.stringify(key)}))>=5.9`);
  check(true, "Main player persists guest position");
  const saved = await c.evaluate(
    `localStorage.getItem(${JSON.stringify(key)})`,
  );
  await home();
  check(
    await c.evaluate(
      `${card(v.public)}.querySelector('.video-card__watched')!==null`,
    ),
    "Guest progress bar appears on card",
  );
  await c.reload();
  await c.wait(`!!document.querySelector(${JSON.stringify(publicLink)})`);
  check(
    await c.evaluate(
      `${card(v.public)}.querySelector('.video-card__watched')!==null`,
    ),
    "Guest progress survives reload",
  );
  await enter();
  await playing();
  await c.fill(".hover-preview input", "1");
  await leave();
  check(
    await c.evaluate(
      `localStorage.getItem(${JSON.stringify(key)})===${JSON.stringify(saved)}`,
    ),
    "Preview seek leaves saved guest position unchanged",
  );
  await mainPlayer();
  await c.fill(".player__seek", "3");
  await c.wait(`Number(localStorage.getItem(${JSON.stringify(key)}))<=3.1`);
  check(
    true,
    "Main seek backwards replaces position instead of keeping maximum",
  );
  const guestSaved = await c.evaluate(
    `localStorage.getItem(${JSON.stringify(key)})`,
  );
  await home();
  await ownerVisibility("private");
  await enter();
  await c.wait(`!document.querySelector(${JSON.stringify(publicLink)})`);
  check(
    !(await c.evaluate(`!!document.querySelector('.hover-preview video')`)),
    "Preview access refusal removes withdrawn card",
  );
  await ownerVisibility("public");
  await login();
  check(
    await c.evaluate(
      `(async()=>{const data=await (await fetch('/api/v1/videos/${v.public}')).json();const progress=data.watch_progress;const bar=${card(v.public)}.querySelector('.video-card__watched');return progress?.position_seconds>0?Number(bar?.getAttribute('aria-valuenow'))===Math.round(progress.position_seconds/progress.duration_seconds*100):!bar;})()`,
    ),
    "Account displays only its server progress",
  );
  await c.click(".avatar-button");
  await c.wait(`!!document.querySelector('.account-menu__jobs')`);
  if (!firefox)
    check(
      await c.evaluate(
        `(()=>{const a=document.querySelector('.account-menu__moderation'),b=document.querySelector('.account-menu__jobs');return getComputedStyle(a).display==='flex'&&getComputedStyle(b).display==='flex'&&a.getBoundingClientRect().bottom<=b.getBoundingClientRect().top})()`,
      ),
      "Admin menu links are separate full rows",
    );
  await c.click(".account-menu__jobs");
  await c.wait(
    `location.pathname==='/admin/jobs' && !!document.querySelector('.job-history')`,
  );
  check(true, "Admin menu jobs link opens panel");
  meFailure = true;
  await navigate("/watch?v=" + v.private);
  await c.wait(
    `document.querySelector('.watch-page .page-state h1')?.textContent==='Не удалось проверить аккаунт'`,
  );
  check(
    await c.evaluate(
      `localStorage.getItem(${JSON.stringify("youtube.guest-progress.v1:" + v.private)})===null`,
    ),
    "Failed account lookup blocks private view without guest progress",
  );
  await home();
  check(
    !(await c.evaluate(`!!document.querySelector('.video-card__watched')`)),
    "Unknown identity does not display guest progress",
  );
  await enter();
  await pause(350);
  check(
    !(await c.evaluate(`!!document.querySelector('.hover-preview')`)),
    "Unknown identity does not start preview",
  );
  meFailure = false;
  await mainPlayer();
  await c.evaluate(
    `document.querySelector('.player video').muted=true;document.querySelector('.player video').play()`,
  );
  await c.wait(`document.querySelector('.player video').currentTime>0.2`);
  await c.fill(".player__seek", "9");
  await c.evaluate(`document.querySelector('.player video').pause()`);
  check(
    await c.evaluate(
      `localStorage.getItem(${JSON.stringify(key)})===${JSON.stringify(guestSaved)}`,
    ),
    "Authenticated playback does not write guest storage",
  );
  await c.evaluate(
    `(async()=>{const csrf=decodeURIComponent(document.cookie.split('; ').find(x=>x.startsWith('csrftoken=')).slice(10));await fetch('/api/v1/auth/logout',{method:'POST',headers:{'x-csrftoken':csrf}});const client=Object.values(document.querySelector('#app').__vue_app__._context.provides).find(x=>typeof x?.getQueryCache==='function');await client.refetchQueries({queryKey:['session']});})()`,
  );
  await c.wait(`!document.querySelector('.masthead .avatar-button')`);
  check(
    await c.evaluate(
      `localStorage.getItem(${JSON.stringify(key)})===${JSON.stringify(guestSaved)}`,
    ),
    "Logout flush cannot leak authenticated position to guest",
  );
  check(
    calls.some((x) => x.url.endsWith("/progress")) &&
      calls
        .filter((x) => x.url.endsWith("/progress"))
        .every((x) => x.method === "PUT"),
    "Account uses the published progress PUT API",
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
        backend: "isolated real metadata/HLS/storage",
      },
      null,
      2,
    ),
  );
} catch (error) {
  console.error({
    runtime: c.errors,
    preview: await c.evaluate(
      `(()=>{const v=document.querySelector('.hover-preview video');return {exists:!!v,paused:v?.paused,time:v?.currentTime,ready:v?.readyState}})()`,
    ),
  });
  throw error;
} finally {
  delay = false;
  failure = false;
  meFailure = false;
  try {
    await ownerVisibility("public");
  } catch {}
  await c.close();
  for (const socket of sockets) socket.destroy();
  server.closeAllConnections();
  server.close();
}
