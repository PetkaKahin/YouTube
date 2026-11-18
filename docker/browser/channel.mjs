import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createServer } from "node:http";
import { browser, pause } from "./cdp.mjs";
let fixture;
for (let attempt = 0; attempt < 1200; attempt++) {
  try {
    fixture = JSON.parse(await readFile("/work/watch-fixture.json", "utf8"));
    if (fixture.playlists?.public) break;
  } catch {}
  await pause(250);
}
if (!fixture?.playlists?.public)
  throw new Error("Channel fixture was not prepared");
let failing = false,
  delay = false;
const calls = [];
const server = createServer(async (req, res) => {
  try {
    const api = req.url.startsWith("/api/");
    if (api) calls.push(req.url);
    if (api && failing) {
      res
        .writeHead(503, { "content-type": "application/problem+json" })
        .end('{"code":"unavailable"}');
      return;
    }
    if (api && delay) await pause(650);
    const headers = new Headers();
    for (const name of ["content-type", "cookie", "x-csrftoken", "range"])
      if (req.headers[name]) headers.set(name, req.headers[name]);
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    const r = await fetch(
      api
        ? `http://youtube-frontend-watch-gateway:8080${req.url}`
        : `http://node:5173${req.url}`,
      {
        method: req.method,
        headers,
        body: chunks.length ? Buffer.concat(chunks) : undefined,
        redirect: "manual",
      },
    );
    res.statusCode = r.status;
    for (const name of ["content-type", "location", "cache-control"])
      if (r.headers.has(name)) res.setHeader(name, r.headers.get(name));
    if (r.headers.getSetCookie().length)
      res.setHeader("set-cookie", r.headers.getSetCookie());
    res.end(Buffer.from(await r.arrayBuffer()));
  } catch {
    res.writeHead(502).end();
  }
});
await new Promise((resolve) => server.listen(4183, "127.0.0.1", resolve));
const c = await browser(),
  base = "http://127.0.0.1:4183",
  tag = fixture.people.owner.tag;
const root = `/@${tag}`,
  mixed = `${root}/playlists?list=${fixture.playlists.public}`;
let assertions = 0;
const check = (value, message) => {
  assert.ok(value, message);
  assertions++;
};
async function open(path = root) {
  await c.navigate(base + path);
  await c.wait(
    `!!document.querySelector('.channel-page h1') || !!document.querySelector('.page-state h1')`,
  );
}
async function login(role, to = root) {
  await c.navigate(`${base}/login?returnTo=${encodeURIComponent(to)}`);
  await c.fill("#login-email", fixture.people[role].email);
  await c.fill("#login-password", "abcdef");
  await c.click(".auth-card button[type=submit]");
  await c.wait(
    `location.pathname.startsWith('/@') && !!document.querySelector('.channel-page h1') && !!document.querySelector('.avatar-button')`,
  );
}
async function allVideos(selector, moreSelector) {
  while (
    await c.evaluate(
      `!!document.querySelector(${JSON.stringify(moreSelector)})`,
    )
  ) {
    await c.click(moreSelector);
    await c.wait(
      `!document.querySelector(${JSON.stringify(moreSelector)}) || !document.querySelector(${JSON.stringify(moreSelector)}).disabled`,
    );
  }
  return c.evaluate(
    `[...document.querySelectorAll(${JSON.stringify(selector)})].map(e=>e.getAttribute('href'))`,
  );
}
try {
  await c.viewport(1920, 935);
  await open();
  await c.wait(
    `document.querySelectorAll('.channel-page__grid--videos .video-card').length>=20`,
  );
  check(
    await c.evaluate(
      `document.querySelector('.channel-page h1').textContent==='Общий автор' && !document.querySelector('.channel-page h1 script') && !window.frontendInject`,
    ),
    "Channel name is text",
  );
  check(
    await c.evaluate(
      `document.querySelector('.channel-page__identity p').textContent==='@${tag}' && document.querySelector('.channel-page__tabs [aria-current]').textContent==='Видео'`,
    ),
    "Channel direct URL and unique tag",
  );
  check(
    await c.evaluate(
      `document.querySelectorAll('.channel-page__tabs a').length===2 && !document.querySelector('.channel-page__banner')`,
    ),
    "Only scope tabs and real profile fields",
  );
  const urls = await allVideos(
    ".channel-page__grid--videos .video-card__preview",
    ".channel-page__more button",
  );
  check(
    urls.length === 41 && new Set(urls).size === 41,
    "Channel video cursor pages 20/20/1 without duplicates",
  );
  check(
    !urls.some((url) =>
      [
        fixture.videos.private,
        fixture.videos.hidden,
        fixture.videos.draft,
        fixture.videos.pending,
      ].some((id) => url.endsWith(`v=${id}`)),
    ),
    "Public channel excludes private/hidden/draft/pending",
  );
  check(
    calls.some((url) => url.includes(`/channels/${tag}/videos?cursor=`)),
    "Video continuation uses server cursor",
  );
  await c.screenshot("channel-desktop-local");
  await c.click(".channel-page__tabs a:last-child");
  await c.wait(`document.querySelectorAll('.playlist-card').length>=20`);
  check(
    await c.evaluate(
      `location.pathname.endsWith('/playlists') && document.querySelector('.channel-page__tabs [aria-current]').textContent==='Плейлисты'`,
    ),
    "Playlist tab persists in URL",
  );
  const listUrls = await allVideos(
    ".playlist-card__preview",
    ".channel-page__more button",
  );
  check(
    listUrls.length === 41 &&
      !listUrls.some(
        (url) =>
          url.endsWith(`list=${fixture.playlists.private}`) ||
          url.endsWith(`list=${fixture.playlists.unlisted}`),
      ),
    "Only public playlist pages 20/20/1",
  );
  await c.evaluate(`document.querySelector('.playlist-card__preview').click()`);
  await c.wait(`!!document.querySelector('.playlist-contents__header h2')`);
  check(
    await c.evaluate(
      `document.querySelector('.playlist-contents .page-state h1')?.textContent==='Здесь пока нет доступных видео' && !document.querySelector('.playlist-contents__videos')`,
    ),
    "Empty playlist has no invented entries or player",
  );
  await open(mixed);
  await c.wait(
    `document.querySelectorAll('.playlist-contents__videos li').length>=20`,
  );
  check(
    await c.evaluate(
      `document.querySelector('.playlist-contents__header h2').textContent.includes('<img') && !document.querySelector('.playlist-contents__header img') && !document.querySelector('.playlist-contents__description script') && !window.frontendInject`,
    ),
    "Playlist title and description stay text",
  );
  const contents = await allVideos(
    ".playlist-contents .video-card__preview",
    ".playlist-contents__more button",
  );
  check(
    contents.length === 41 &&
      contents[0].endsWith(`v=${fixture.videos.public}`) &&
      contents.at(-1).endsWith(`v=${fixture.videos.silent}`),
    "Playlist inclusion order and cursor continuation",
  );
  check(
    !contents.some((url) =>
      [
        fixture.videos.private,
        fixture.videos.hidden,
        fixture.videos.draft,
        fixture.videos.pending,
      ].some((id) => url.endsWith(`v=${id}`)),
    ),
    "Public playlist does not expose inaccessible entries",
  );
  await c.reload();
  await c.wait(`!!document.querySelector('.playlist-contents__header h2')`);
  check(
    await c.evaluate(
      `new URLSearchParams(location.search).get('list')===${JSON.stringify(fixture.playlists.public)}`,
    ),
    "Playlist state restored after reload",
  );
  await open(`${root}/playlists?list=${fixture.playlists.unlisted}`);
  await c.wait(`!!document.querySelector('.playlist-contents__header h2')`);
  check(
    await c.evaluate(
      `document.querySelector('.playlist-contents__visibility').textContent==='Доступ по ссылке' && document.querySelectorAll('.playlist-contents__videos li').length===1`,
    ),
    "Unlisted playlist opens with known channel and filters private video",
  );
  await open(`${root}/playlists?list=${fixture.playlists.private}`);
  await c.wait(
    `document.querySelector('.playlist-contents .page-state h1')?.textContent==='Плейлист недоступен'`,
  );
  check(
    await c.evaluate(
      `!document.querySelector('.playlist-contents__header') && !document.querySelector('.playlist-contents__videos')`,
    ),
    "Private playlist does not expose metadata to guest",
  );
  await login("invited", `${root}/playlists?list=${fixture.playlists.private}`);
  await c.wait(
    `document.querySelector('.playlist-contents .page-state h1')?.textContent==='Плейлист недоступен'`,
  );
  check(
    await c.evaluate(`!document.querySelector('.playlist-contents__header')`),
    "Invitation to video does not grant private playlist",
  );
  await login("owner", mixed);
  await c.wait(
    `document.querySelectorAll('.playlist-contents__videos li').length>=20`,
  );
  const ownerContents = await allVideos(
    ".playlist-contents .video-card__preview",
    ".playlist-contents__more button",
  );
  check(
    ownerContents.length === 42 &&
      ownerContents[1].endsWith(`v=${fixture.videos.private}`),
    "Owner sees permitted private entry in inclusion order",
  );
  check(
    !ownerContents.some((url) => url.endsWith(`v=${fixture.videos.hidden}`)),
    "Hidden video excluded for owner",
  );
  await open(`${root}/playlists?list=${fixture.playlists.private}`);
  await c.wait(`!!document.querySelector('.playlist-contents__header h2')`);
  check(
    await c.evaluate(
      `document.querySelectorAll('.playlist-contents__videos li').length===2`,
    ),
    "Owner opens private playlist",
  );
  await c.click(".avatar-button");
  await c.click("#logout-button");
  await c.wait(
    `!!document.querySelector('.masthead .sign-in') && document.querySelector('.playlist-contents .page-state h1')?.textContent==='Плейлист недоступен'`,
  );
  check(
    await c.evaluate(`!document.querySelector('.playlist-contents__header')`),
    "Logout removes private playlist title and contents",
  );
  await open(
    `/@${fixture.people.stranger.tag}/playlists?list=${fixture.playlists.public}`,
  );
  await c.wait(
    `document.querySelector('.playlist-contents .page-state h1')?.textContent==='Плейлист недоступен'`,
  );
  check(
    await c.evaluate(`!document.querySelector('.playlist-contents__header')`),
    "Mismatched channel cannot display another playlist under same name",
  );
  await open(`/@${fixture.people.stranger.tag}`);
  await c.wait(
    `document.querySelector('.channel-page__content .page-state h1')?.textContent==='На канале пока нет видео'`,
  );
  check(
    await c.evaluate(
      `document.querySelector('.channel-page__identity p').textContent==='@${fixture.people.stranger.tag}'`,
    ),
    "Same names do not conflate channels, empty channel is available",
  );
  delay = true;
  await open(root);
  await c.click(".channel-page__tabs a:last-child");
  await c.click(".channel-page__tabs a:first-child");
  delay = false;
  await c.wait(
    `document.querySelectorAll('.channel-page__grid--videos .video-card').length>=20`,
  );
  check(
    await c.evaluate(`!document.querySelector('.playlist-card')`),
    "Rapid tab changes do not show old results",
  );
  failing = true;
  await open(`/@${tag}/playlists`);
  await c.wait(
    `document.querySelector('.page-state h1')?.textContent==='Не удалось загрузить канал'`,
  );
  check(
    await c.evaluate(
      `!document.querySelector('.channel-page__header') && !document.querySelector('.playlist-card')`,
    ),
    "API failure does not leave previous channel data",
  );
  failing = false;
  await c.click(".page-state button");
  await c.wait(`document.querySelectorAll('.playlist-card').length>=20`);
  check(true, "Channel retry recovers API");
  await open("/@missing_channel_123");
  await c.wait(
    `document.querySelector('.page-state h1')?.textContent==='Канал недоступен'`,
  );
  check(
    await c.evaluate(`!document.querySelector('.channel-page__header')`),
    "Unknown channel clears old identity",
  );
  await c.viewport(390, 844, true);
  await open();
  await c.wait(`document.querySelectorAll('.video-card').length>=20`);
  check(
    await c.evaluate(
      `document.documentElement.scrollWidth<=innerWidth && getComputedStyle(document.querySelector('.channel-page__grid .video-card')).gridTemplateColumns.split(' ').length===2`,
    ),
    "Phone video rows match mobile composition",
  );
  await c.screenshot("channel-mobile-local");
  await c.click(".channel-page__tabs a:last-child");
  await c.wait(`document.querySelectorAll('.playlist-card').length>=20`);
  check(
    await c.evaluate(
      `document.documentElement.scrollWidth<=innerWidth && document.querySelectorAll('.playlist-card').length>=20`,
    ),
    "Phone playlist rows fit",
  );
  await open(mixed);
  await c.wait(`!!document.querySelector('.playlist-contents__header')`);
  check(
    await c.evaluate(`document.documentElement.scrollWidth<=innerWidth`),
    "Phone playlist contents fit",
  );
  await c.screenshot("channel-playlist-mobile-local");
  await c.click(".playlist-contents .video-card__preview");
  await c.wait(
    `location.pathname==='/watch' && !!document.querySelector('.player')`,
  );
  check(true, "Playlist card navigates to actual watch screen");
  await c.click(".watch-page__author");
  await c.wait(
    `location.pathname.startsWith('/@') && !!document.querySelector('.channel-page h1')`,
  );
  check(true, "Watch author link returns to channel");
  check(c.errors.length === 0, "No uncaught channel errors");
  console.log(
    JSON.stringify(
      {
        status: "pass",
        assertions,
        engine: "Chromium",
        screens: ["1920x935", "390x844"],
        backend: "real isolated PostgreSQL/API/Nginx/RustFS",
      },
      null,
      2,
    ),
  );
} catch (error) {
  await c.screenshot("channel-failure");
  console.error({ runtimeErrors: c.errors });
  throw error;
} finally {
  await c.close();
  server.closeAllConnections();
  server.close();
}
