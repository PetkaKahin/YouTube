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
if (!fixture?.playlists?.public) throw new Error("Search fixture not ready");
const firefox = process.argv.includes("--firefox");
let failure = "",
  delay = "";
const calls = [],
  sockets = new Set();
const server = createServer(async (req, res) => {
  try {
    const api = req.url.startsWith("/api/"),
      search = req.url.startsWith("/api/v1/search"),
      term = new URL(req.url, "http://test").searchParams.get("q");
    if (api) calls.push(req.url);
    if (
      search &&
      (failure === "all" || (failure === "next" && req.url.includes("cursor=")))
    ) {
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
    if (search && term === delay) await pause(650);
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
const exact = (text) =>
  JSON.parse(text, (key, value, context) =>
    key === "id" && typeof value === "number" ? context.source : value,
  );
const rowKeys = `Array.from(document.querySelectorAll('.search-results > article')).map(x=>x.classList.contains('video-card')?'video:'+x.querySelector('.video-card__preview').getAttribute('href'):x.classList.contains('channel-result')?'channel:'+x.querySelector('h2 a').getAttribute('href'):'playlist:'+x.querySelector('.playlist-result__preview').getAttribute('href'))`;
const key = (item) =>
  item.type === "video"
    ? "video:" + href(item.video.id)
    : item.type === "channel"
      ? "channel:/@" + item.channel.tag
      : "playlist:/@" +
        item.playlist.channel.tag +
        "/playlists?list=" +
        item.playlist.id;
const searchCalls = () => calls.filter((x) => x.startsWith("/api/v1/search"));
async function navigate(path = "/") {
  await c.navigate(base + path);
}
function url(q, params = {}) {
  return "/results?" + new URLSearchParams({ search_query: q, ...params });
}
async function query(q, params = {}) {
  await navigate(url(q, params));
}
async function api(q, params = {}) {
  const keys = [],
    pages = [];
  let cursor;
  do {
    const qs = new URLSearchParams({ q, ...params });
    if (cursor) qs.set("cursor", cursor);
    const response = await fetch(
      "http://youtube-frontend-upload-gateway:8080/api/v1/search?" + qs,
    );
    assert.equal(response.status, 200);
    const page = exact(await response.text());
    pages.push(page.items.length);
    keys.push(...page.items.map(key));
    cursor = page.next_cursor;
  } while (cursor);
  return { keys, pages };
}
async function actual() {
  return JSON.parse(await c.evaluate(`JSON.stringify(${rowKeys})`));
}
async function loadAll(expected) {
  await c.wait(
    `document.querySelectorAll('.search-results > article').length>0`,
  );
  for (let i = 0; i < 80; i++) {
    if (
      await c.evaluate(
        `document.querySelectorAll('.search-results > article').length===${expected}&&!document.querySelector('.search-page__pagination')`,
      )
    )
      break;
    await c.evaluate(
      `window.scrollTo(0,document.body.scrollHeight);document.querySelector('.search-page__pagination button:not(:disabled)')?.click()`,
    );
    await pause(120);
  }
  await c.wait(
    `document.querySelectorAll('.search-results > article').length===${expected}&&!document.querySelector('.search-page__pagination')`,
  );
  return actual();
}
async function empty() {
  await c.wait(
    `document.querySelector('.search-page .page-state')?.textContent.includes('Ничего не найдено')`,
  );
}
async function submit(value, keyboard = false) {
  await c.fill(".search-form input", value);
  if (keyboard) {
    await c.evaluate(`document.querySelector('.search-form input').focus()`);
    if (firefox) await c.key("\uE007");
    else {
      await c.send("Input.dispatchKeyEvent", {
        type: "keyDown",
        text: "\r",
        key: "Enter",
        code: "Enter",
        windowsVirtualKeyCode: 13,
      });
      await c.send("Input.dispatchKeyEvent", {
        type: "keyUp",
        key: "Enter",
        code: "Enter",
        windowsVirtualKeyCode: 13,
      });
    }
  } else await c.click(".search-form__submit");
}
async function login(person) {
  await navigate("/login?returnTo=%2F");
  await c.fill("#login-email", fixture.people[person].email);
  await c.fill("#login-password", "abcdef");
  await c.click(".auth-card button[type=submit]");
  await c.wait(
    `location.pathname==='/'&&!!document.querySelector('.avatar-button')`,
  );
}
try {
  await c.viewport(1280, 900);
  await navigate();
  await c.wait(`!!document.querySelector('.home-page .video-card')`);
  const mixed = await api("И");
  check(
    mixed.pages.length > 2 &&
      mixed.keys.some((x) => x.startsWith("video:")) &&
      mixed.keys.some((x) => x.startsWith("channel:")) &&
      mixed.keys.some((x) => x.startsWith("playlist:")),
    "Real mixed query spans multiple pages of all three types",
  );
  delay = "И";
  await submit("  И  ", true);
  await c.wait(`!!document.querySelector('.search-page__loading')`);
  check(
    await c.evaluate(
      `new URLSearchParams(location.search).get('search_query')==='И'&&!document.querySelector('.search-page .page-state')`,
    ),
    "Enter submits trimmed query and loading is distinct from empty",
  );
  delay = "";
  check(
    JSON.stringify(await loadAll(mixed.keys.length)) ===
      JSON.stringify(mixed.keys),
    "Mixed cursor pages preserve exact API order and bigint links",
  );
  check(
    new Set(await actual()).size === mixed.keys.length,
    "Mixed pages contain no duplicate rows",
  );
  check(
    searchCalls().some((x) =>
      new URL(x, "http://test").searchParams.has("cursor"),
    ),
    "Next page uses server cursor",
  );
  check(
    ![v.private, v.hidden, v.pending, v.draft].some((x) =>
      mixed.keys.includes("video:" + href(x)),
    ) &&
      !["private", "unlisted"].some((x) =>
        mixed.keys.some((y) => y.endsWith("list=" + fixture.playlists[x])),
      ),
    "Private hidden pending draft and nonpublic playlists are absent",
  );
  check(
    await c.evaluate(
      `!window.frontendInject&&!document.querySelector('.search-results h2 script')&&!document.querySelector('.playlist-result__description script')`,
    ),
    "HTML-like names and descriptions remain inert text",
  );

  const videos = await api("и", { type: "video" });
  check(
    videos.pages.join(",") === "20,20,1",
    "Real video search has 20/20/1 pages",
  );
  await query("и", { type: "video" });
  check(
    JSON.stringify(await loadAll(videos.keys.length)) ===
      JSON.stringify(videos.keys),
    "Video-only pages preserve server order",
  );
  await c.evaluate(`window.scrollTo(0,0)`);
  const videoTarget = (await actual())[0].slice(6);
  await c.click(".search-results .video-card__preview");
  await c.wait(
    `location.pathname==='/watch'&&!!document.querySelector('.watch-page h1')`,
  );
  check(
    await c.evaluate(
      `location.pathname+location.search===${JSON.stringify(videoTarget)}`,
    ),
    "Video search opens exact selected video",
  );
  await c.click(".watch-page__author");
  await c.wait(`!!document.querySelector('.channel-page h1')`);
  check(
    await c.evaluate(`location.pathname==='/@${fixture.people.owner.tag}'`),
    "Video author opens its public channel",
  );

  const channels = await api("Общий", { type: "channel" });
  await query("оБщИй", { type: "channel" });
  check(
    JSON.stringify(await loadAll(channels.keys.length)) ===
      JSON.stringify(channels.keys),
    "Channel name search is case-insensitive and keeps distinct tags",
  );
  const channelTarget = (await actual())[0].slice(8);
  await c.click(".channel-result h2 a");
  await c.wait(`!!document.querySelector('.channel-page h1')`);
  check(
    await c.evaluate(`location.pathname===${JSON.stringify(channelTarget)}`),
    "Channel search opens selected channel",
  );
  await query("@" + fixture.people.owner.tag.toUpperCase(), {
    type: "channel",
  });
  check(
    (await loadAll(1))[0] === "channel:/@" + fixture.people.owner.tag,
    "Channel tag lookup supports @ and case folding",
  );

  const playlists = await api("плейлист", { type: "playlist" });
  check(
    playlists.pages.join(",") === "20,20,1",
    "Real playlist search has 20/20/1 pages",
  );
  await query("плейлист", { type: "playlist" });
  check(
    JSON.stringify(await loadAll(playlists.keys.length)) ===
      JSON.stringify(playlists.keys),
    "Playlist-only pages preserve exact IDs and server order",
  );
  const playlistSelector = `.playlist-result__preview[href="/@${fixture.people.owner.tag}/playlists?list=${fixture.playlists.public}"]`;
  await c.click(playlistSelector);
  await c.wait(
    `!!document.querySelector('.playlist-contents h2')&&!!document.querySelector('.playlist-contents .video-card')`,
  );
  check(
    await c.evaluate(
      `new URLSearchParams(location.search).get('list')==='${fixture.playlists.public}'&&!document.querySelector('.video-card__preview[href="/watch?v=${v.private}"]')`,
    ),
    "Playlist result opens actual content without granting private video access",
  );

  for (const term of [
    "Описание",
    fixture.people.owner.email,
    "%",
    "' OR 1=1--",
  ]) {
    await query(term);
    await empty();
    check(
      (await actual()).length === 0,
      "Descriptions, email and literal injection terms do not broaden search",
    );
  }
  await query("<script>");
  check(
    (await loadAll(1))[0] === "video:" + href(v.public),
    "HTML search term is literal data",
  );
  check(
    await c.evaluate(
      `!window.frontendInject&&!document.querySelector('.search-results script')`,
    ),
    "Returned matching title cannot execute HTML",
  );
  await query("😀".repeat(100));
  await empty();
  check(
    searchCalls().some(
      (x) =>
        new URL(x, "http://test").searchParams.get("q") === "😀".repeat(100),
    ),
    "100 Unicode code points reach API without UTF-16 truncation",
  );
  const beforeInvalid = searchCalls().length;
  await query("😀".repeat(101));
  await c.wait(
    `document.querySelector('.search-page .page-state')?.textContent.includes('Введите поисковый запрос')`,
  );
  check(
    searchCalls().length === beforeInvalid,
    "Overlong direct query is rejected without API traffic",
  );
  await query("   ");
  await c.wait(
    `document.querySelector('.search-page .page-state')?.textContent.includes('Введите поисковый запрос')`,
  );
  check(
    searchCalls().length === beforeInvalid,
    "Blank direct query is not sent",
  );

  failure = "all";
  await query("и", { type: "video" });
  await c.wait(
    `document.querySelector('.search-page .page-state')?.textContent.includes('Не удалось выполнить поиск')`,
  );
  check(
    !(await c.evaluate(
      `!!document.querySelector('.search-results')||!!document.querySelector('.search-page__loading')`,
    )),
    "Initial 503 is distinct from empty and loading",
  );
  failure = "";
  await c.click(".search-page .page-state button");
  check(
    JSON.stringify(await loadAll(videos.keys.length)) ===
      JSON.stringify(videos.keys),
    "Initial error retry restores real results",
  );
  failure = "next";
  await query("и", { type: "video" });
  await c.wait(
    `document.querySelectorAll('.search-results > article').length===20`,
  );
  await c.evaluate(`window.scrollTo(0,document.body.scrollHeight)`);
  await c.wait(
    `document.querySelector('.search-page__pagination button')?.textContent.includes('Повторить загрузку')`,
  );
  check(
    (await actual()).length === 20,
    "Next-page 503 preserves earlier results",
  );
  failure = "";
  await c.click(".search-page__pagination button");
  check(
    JSON.stringify(await loadAll(videos.keys.length)) ===
      JSON.stringify(videos.keys),
    "Next-page retry completes without duplicate rows",
  );

  await query("Общий", { type: "channel" });
  await loadAll(3);
  delay = "Открытое";
  await submit("Открытое");
  await c.wait(`!!document.querySelector('.search-page__loading')`);
  await submit("НетТакогоРезультата");
  await empty();
  await pause(800);
  delay = "";
  check(
    (await actual()).length === 0 &&
      (await c.evaluate(
        `new URLSearchParams(location.search).get('search_query')==='НетТакогоРезультата'`,
      )),
    "Late old response cannot replace newer query",
  );
  await query("Общий", { type: "channel" });
  await loadAll(3);
  await submit("Плейлист");
  await loadAll(41);
  await c.evaluate(`history.back()`);
  await c.wait(
    `new URLSearchParams(location.search).get('search_query')==='Общий'&&document.querySelector('.channel-result')!==null`,
  );
  check(
    await c.evaluate(
      `document.querySelector('.search-form input').value==='Общий'&&new URLSearchParams(location.search).get('type')==='channel'`,
    ),
    "Back restores input and query parameters",
  );
  await c.evaluate(`history.forward()`);
  await loadAll(41);
  check(
    await c.evaluate(
      `document.querySelector('.search-form input').value==='Плейлист'`,
    ),
    "Forward restores query and correct results",
  );
  await c.reload();
  await loadAll(41);
  check(
    await c.evaluate(
      `document.querySelector('.search-form input').value==='Плейлист'`,
    ),
    "Reload restores query from URL",
  );

  await login("invited");
  await query("и", { type: "video" });
  const invited = await loadAll(41);
  check(
    !invited.includes("video:" + href(v.private)),
    "An email invitation never makes private video discoverable",
  );
  await query("плейлист", { type: "playlist" });
  check(
    JSON.stringify(await loadAll(41)) === JSON.stringify(playlists.keys),
    "Authenticated viewer cannot discover private or unlisted playlists",
  );

  await c.viewport(390, 844);
  await c.click(".masthead__search");
  await c.wait(
    `location.hash==='#searching'&&document.activeElement===document.querySelector('.search-form input')`,
  );
  await submit("@" + fixture.people.owner.tag, true);
  await loadAll(1);
  check(
    await c.evaluate(
      `location.hash===''&&!!document.querySelector('.channel-result')`,
    ),
    "Phone search submits by keyboard and opens actual results",
  );
  await c.click(".channel-result h2 a");
  await c.wait(`!!document.querySelector('.channel-page h1')`);
  check(
    await c.evaluate(`location.pathname==='/@${fixture.people.owner.tag}'`),
    "Phone result opens correct channel",
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
        mixedPages: mixed.pages,
        backend: "isolated real search/metadata/HLS/storage",
      },
      null,
      2,
    ),
  );
} catch (error) {
  console.error({
    runtime: c.errors,
    location: await c.evaluate(`location.pathname+location.search`),
    input: await c.evaluate(
      `document.querySelector('.search-form input')?.value`,
    ),
    rows: await actual(),
    lastSearch: searchCalls().slice(-3),
  });
  throw error;
} finally {
  failure = "";
  delay = "";
  await c.close();
  for (const socket of sockets) socket.destroy();
  server.closeAllConnections();
  server.close();
}
