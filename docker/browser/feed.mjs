import assert from "node:assert/strict";
import { createServer, request as httpRequest } from "node:http";
import { readFile } from "node:fs/promises";
import { browser, pause } from "./cdp.mjs";
import { firefoxBrowser } from "./bidi.mjs";
let fixture;
for (let i = 0; i < 1200; i++) {
  try {
    fixture = JSON.parse(await readFile("/work/watch-fixture.json", "utf8"));
    if (fixture.playlists?.public) break;
  } catch {}
  await pause(250);
}
if (!fixture?.playlists?.public) throw new Error("Feed fixture not ready");
const firefox = process.argv.includes("--firefox");
let failure = "",
  empty = false,
  delay = false;
const calls = [],
  sockets = new Set();
const server = createServer(async (req, res) => {
  try {
    const api = req.url.startsWith("/api/"),
      feed = req.url.startsWith("/api/v1/videos/feed");
    if (api) calls.push(req.url);
    if (
      feed &&
      (failure === "all" || (failure === "next" && req.url.includes("cursor=")))
    ) {
      res
        .writeHead(503, { "content-type": "application/problem+json" })
        .end('{"code":"unavailable"}');
      return;
    }
    if (feed && empty) {
      res
        .writeHead(200, { "content-type": "application/json" })
        .end('{"items":[],"next_cursor":null}');
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
    if (feed && delay) await pause(650);
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
const href = (id) => `/watch?v=${id}`;
const cards = `[...document.querySelectorAll('.home-page .video-card__preview')].map(x=>x.getAttribute('href'))`;
async function navigate(path = "/") {
  await c.navigate(base + path);
}
async function loadAll() {
  await c.wait(
    `document.querySelectorAll('.home-page .video-card').length>=20`,
  );
  for (let i = 0; i < 4; i++) {
    if (
      await c.evaluate(
        `document.querySelectorAll('.home-page .video-card').length===41`,
      )
    )
      break;
    await c.evaluate(
      `window.scrollTo(0,document.body.scrollHeight);document.querySelector('.pagination button')?.click()`,
    );
    await pause(150);
  }
  await c.wait(
    `document.querySelectorAll('.home-page .video-card').length===41`,
  );
}
const exact = (text) =>
  JSON.parse(text, (key, value, context) =>
    key === "id" && typeof value === "number" ? context.source : value,
  );
let ownerCookies;
const restore = new Map();
async function owner(path, method = "GET", body) {
  const host = "http://youtube-frontend-upload-gateway:8080";
  if (!ownerCookies) {
    ownerCookies = new Map();
    const absorb = (r) => {
      for (const line of r.headers.getSetCookie()) {
        const pair = line.split(";")[0],
          at = pair.indexOf("=");
        ownerCookies.set(pair.slice(0, at), pair.slice(at + 1));
      }
    };
    const me = await fetch(host + "/api/v1/auth/me");
    absorb(me);
    const login = await fetch(host + "/api/v1/auth/login", {
      method: "POST",
      headers: {
        cookie: [...ownerCookies].map(([k, x]) => k + "=" + x).join("; "),
        "content-type": "application/json",
        "x-csrftoken": decodeURIComponent(ownerCookies.get("csrftoken")),
      },
      body: JSON.stringify({
        email: fixture.people.owner.email,
        password: "abcdef",
      }),
    });
    assert.equal(login.status, 200);
    absorb(login);
  }
  const response = await fetch(host + path, {
    method,
    headers: {
      cookie: [...ownerCookies].map(([k, x]) => k + "=" + x).join("; "),
      "content-type": "application/json",
      "x-csrftoken": decodeURIComponent(ownerCookies.get("csrftoken")),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  assert.ok(response.ok, "Owner fixture operation");
  return exact(await response.text());
}
async function metadata(visibility, title) {
  return owner("/api/v1/videos/" + v.public + "/settings", "PUT", {
    title,
    description: "Описание",
    visibility,
  });
}
try {
  await c.viewport(1920, 935);
  delay = true;
  await navigate();
  await c.wait(`!!document.querySelector('.home-page [role=status]')`);
  check(
    !(await c.evaluate(`!!document.querySelector('.home-page .page-state')`)),
    "Loading is distinct from empty",
  );
  delay = false;
  await loadAll();
  const expected = [];
  let cursor;
  do {
    const response = await fetch(
      "http://youtube-frontend-upload-gateway:8080/api/v1/videos/feed" +
        (cursor ? "?cursor=" + encodeURIComponent(cursor) : ""),
    );
    assert.equal(response.status, 200);
    const page = exact(await response.text());
    expected.push(...page.items.map((x) => href(x.id)));
    cursor = page.next_cursor;
  } while (cursor);
  const actual = JSON.parse(await c.evaluate(`JSON.stringify(${cards})`));
  check(
    JSON.stringify(actual) === JSON.stringify(expected),
    "All feed pages preserve API order and exact IDs",
  );
  check(new Set(actual).size === 41, "20/20/1 without duplicate cards");
  check(
    calls.some((x) => x.includes("/videos/feed?cursor=")),
    "Server cursor used",
  );
  check(
    ![v.private, v.hidden, v.pending, v.draft].some((id) =>
      actual.includes(href(id)),
    ),
    "Private hidden pending and draft excluded",
  );
  check(
    await c.evaluate(
      `!window.frontendInject && !document.querySelector('.video-card h2 script')`,
    ),
    "Title remains safe text",
  );
  await c.evaluate(`window.scrollTo(0,0)`);
  await c.wait(
    `document.querySelector('.video-card__preview img')?.complete===true`,
  );
  check(
    await c.evaluate(
      `document.querySelector('.video-card__preview img').naturalWidth>0`,
    ),
    "Real preview loads",
  );

  const target = actual[0];
  await c.click(".video-card__preview");
  await c.wait(
    `location.pathname==='/watch' && !!document.querySelector('.watch-page h1')`,
  );
  check(
    await c.evaluate(
      `location.pathname+location.search===${JSON.stringify(target)}`,
    ),
    "Preview opens correct video",
  );
  await navigate();
  await c.wait(`!!document.querySelector('.video-card__channel')`);
  await c.click(".video-card__channel");
  await c.wait(
    `location.pathname.startsWith('/@') && !!document.querySelector('.channel-page')`,
  );
  check(
    await c.evaluate(
      `location.pathname===${JSON.stringify("/@" + fixture.people.owner.tag)}`,
    ),
    "Author opens correct channel",
  );
  failure = "next";
  await navigate();
  await c.wait(
    `document.querySelectorAll('.home-page .video-card').length>=20`,
  );
  await c.evaluate(
    `window.scrollTo(0,document.body.scrollHeight);document.querySelector('.pagination button')?.click()`,
  );
  await c.wait(
    `document.querySelector('.pagination button')?.textContent.includes('Повторить')`,
  );
  check(
    await c.evaluate(
      `document.querySelectorAll('.home-page .video-card').length===20`,
    ),
    "Next page failure keeps first page",
  );
  failure = "";
  await c.click(".pagination button");
  await loadAll();
  check(true, "Next page retry continues without duplicates");
  failure = "all";
  await navigate();
  await c.wait(
    `document.querySelector('.home-page .page-state h1')?.textContent==='Не удалось загрузить видео'`,
  );
  check(
    !(await c.evaluate(`!!document.querySelector('.video-card')`)),
    "First page failure shows no fabricated cards",
  );
  failure = "";
  await c.click(".home-page .page-state button");
  await loadAll();
  empty = true;
  await navigate();
  await c.wait(
    `document.querySelector('.home-page .page-state h1')?.textContent==='Пока нет видео'`,
  );
  check(
    !(await c.evaluate(`!!document.querySelector('.video-card')`)),
    "Empty feed is separate state",
  );
  empty = false;
  await c.viewport(390, 844, true);
  await navigate();
  await c.wait(`!!document.querySelector('.video-card__preview')`);
  const phoneTarget = await c.evaluate(
    `document.querySelector('.video-card__preview').getAttribute('href')`,
  );
  await c.click(".video-card__preview");
  await c.wait(
    `location.pathname==='/watch' && !!document.querySelector('.watch-page h1')`,
  );
  check(
    await c.evaluate(
      `location.pathname+location.search===${JSON.stringify(phoneTarget)}`,
    ),
    "Card navigation works on phone",
  );
  await c.viewport(1920, 935);
  await navigate();
  await loadAll();
  const original = await owner("/api/v1/videos/" + v.public + "/settings");
  await metadata("private", original.title);
  await c.evaluate(
    `(()=>{const a=[...document.querySelectorAll('.video-card__preview')].find(x=>x.getAttribute('href')===${JSON.stringify(href(v.public))});a.scrollIntoView({block:'center'});const img=a.querySelector('img');img.src=img.getAttribute('src')+'?verify=1';})()`,
  );
  await c.wait(`!${cards}.includes(${JSON.stringify(href(v.public))})`);
  check(
    true,
    "Preview rejection refreshes feed and removes withdrawn metadata",
  );
  await c.evaluate(
    `document.querySelector('#app').__vue_app__.config.globalProperties.$router.push(${JSON.stringify("/watch?v=" + v.public)})`,
  );
  await c.wait(
    `document.querySelector('.watch-page .page-state h1')?.textContent==='Видео недоступно'`,
  );
  delay = true;
  await c.evaluate(
    `document.querySelector('#app').__vue_app__.config.globalProperties.$router.back()`,
  );
  await c.wait(
    `location.pathname==='/' && !!document.querySelector('.home-page')`,
  );
  check(
    !(await c.evaluate(`!!document.querySelector('.home-page .video-card')`)),
    "Back does not flash inactive stale feed",
  );
  delay = false;
  await c.wait(
    `document.querySelectorAll('.home-page .video-card').length>=20`,
  );
  const longTitle = "Длинное название ".repeat(8).slice(0, 100);
  await metadata("public", longTitle);
  await navigate();
  await loadAll();
  check(
    await c.evaluate(
      `(()=>{const a=[...document.querySelectorAll('.video-card h2 a')].find(x=>x.getAttribute('href')===${JSON.stringify(href(v.public))});return a.textContent===${JSON.stringify(longTitle)}})()`,
    ),
    "Long title stays intact in card link",
  );
  await metadata("public", original.title);
  for (const [name, path] of [
    ["search", "/results?search_query=" + encodeURIComponent(original.title)],
    ["channel", "/@" + fixture.people.owner.tag + "/videos"],
  ]) {
    await navigate(path);
    await c.wait(`!!document.querySelector('.video-card__preview img')`);
    const link = await c.evaluate(
      `document.querySelector('.video-card__preview').getAttribute('href')`,
    );
    const targetId = new URL(link, base).searchParams.get("v");
    const current = await owner("/api/v1/videos/" + targetId + "/settings");
    const body = {
      title: current.title,
      description: current.description,
      visibility: "public",
    };
    restore.set(targetId, body);
    await owner("/api/v1/videos/" + targetId + "/settings", "PUT", {
      ...body,
      visibility: "private",
    });
    await c.evaluate(
      `(()=>{const a=[...document.querySelectorAll('.video-card__preview')].find(x=>x.getAttribute('href')===${JSON.stringify(link)});a.scrollIntoView({block:'center'});const img=a.querySelector('img');img.src=img.getAttribute('src')+'?verify=1';})()`,
    );
    await c.wait(
      `![...document.querySelectorAll('.video-card__preview')].some(x=>x.getAttribute('href')===${JSON.stringify(link)})`,
    );
    check(true, "Withdrawn card removed from " + name);
    await owner("/api/v1/videos/" + targetId + "/settings", "PUT", body);
  }
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
        backend: "isolated real feed/media/visibility API",
      },
      null,
      2,
    ),
  );
} catch (error) {
  console.error({ runtime: c.errors });
  throw error;
} finally {
  delay = false;
  for (const [id, body] of restore) {
    try {
      await owner("/api/v1/videos/" + id + "/settings", "PUT", body);
    } catch {}
  }
  try {
    const value = await owner("/api/v1/videos/" + v.public + "/settings");
    await metadata("public", value.title);
  } catch {}
  await c.close();
  for (const socket of sockets) socket.destroy();
  server.closeAllConnections();
  server.close();
}
