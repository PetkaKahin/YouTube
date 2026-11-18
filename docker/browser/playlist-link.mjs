import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createServer } from "node:http";
import { browser, pause } from "./cdp.mjs";
let fixture;
for (let attempt = 0; attempt < 600; attempt++) {
  try {
    fixture = JSON.parse(await readFile("/work/watch-fixture.json", "utf8"));
    if (fixture.playlists?.empty_unlisted) break;
  } catch {}
  await pause(250);
}
if (!fixture?.playlists?.empty_unlisted)
  throw new Error("Playlist fixture did not start");
let failing = false,
  slow = false;
const calls = [];
const server = createServer(async (req, res) => {
  try {
    const api = req.url.startsWith("/api/");
    if (api) calls.push(req.url);
    if (api && failing && req.url.includes("/playlists/")) {
      res
        .writeHead(503, { "content-type": "application/problem+json" })
        .end('{"code":"unavailable"}');
      return;
    }
    if (slow && api && req.url.includes("/playlists/")) await pause(700);
    const headers = new Headers();
    for (const name of ["cookie", "content-type", "x-csrftoken", "range"])
      if (req.headers[name]) headers.set(name, req.headers[name]);
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    const upstream = await fetch(
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
    res.statusCode = upstream.status;
    for (const name of ["content-type", "location", "cache-control"])
      if (upstream.headers.has(name))
        res.setHeader(name, upstream.headers.get(name));
    if (upstream.headers.getSetCookie().length)
      res.setHeader("set-cookie", upstream.headers.getSetCookie());
    res.end(Buffer.from(await upstream.arrayBuffer()));
  } catch {
    res.writeHead(502).end();
  }
});
await new Promise((resolve) => server.listen(4184, "127.0.0.1", resolve));
const c = await browser(),
  base = "http://127.0.0.1:4184";
let assertions = 0;
const check = (value, message) => {
  assert.ok(value, message);
  assertions++;
};
async function open(kind) {
  const id = fixture.playlists[kind];
  await c.navigate(`${base}/playlist?list=${id}`);
  await c.wait(
    `location.pathname.startsWith('/@') && new URLSearchParams(location.search).get('list')===${JSON.stringify(id)} && !!document.querySelector('.playlist-contents__header')`,
  );
  check(
    await c.evaluate(
      `location.pathname===${JSON.stringify(`/@${fixture.people.owner.tag}/playlists`)} && document.querySelector('.channel-page__identity p').textContent===${JSON.stringify("@" + fixture.people.owner.tag)}`,
    ),
    `${kind} resolves server owner channel`,
  );
  return id;
}
async function login(role) {
  await c.fill("#login-email", fixture.people[role].email);
  await c.fill("#login-password", "abcdef");
  await c.click(".auth-card button[type=submit]");
}
try {
  for (const [width, height, mobile] of [
    [1920, 935, false],
    [390, 844, true],
  ]) {
    await c.viewport(width, height, mobile);
    const publicId = await open("public");
    check(BigInt(publicId) > 9007199254740991n, "Exact large playlist ID");
    check(
      await c.evaluate(
        `document.querySelector('.playlist-contents__header h2').textContent.includes('<img') && !document.querySelector('.playlist-contents__header img') && !window.frontendInject`,
      ),
      "Resolved metadata stays text",
    );
    await open("empty");
    check(
      await c.evaluate(
        `document.querySelector('.playlist-contents .page-state h1').textContent==='Здесь пока нет доступных видео'`,
      ),
      "Empty public resolves without first video",
    );
    await open("unlisted");
    check(
      await c.evaluate(
        `document.querySelector('.playlist-contents__visibility').textContent==='Доступ по ссылке' && document.querySelectorAll('.playlist-contents__videos li').length===1`,
      ),
      "Unlisted filters inaccessible video",
    );
    await open("empty_unlisted");
    check(
      await c.evaluate(
        `document.querySelector('.playlist-contents .page-state h1').textContent==='Здесь пока нет доступных видео' && document.documentElement.scrollWidth<=innerWidth`,
      ),
      "Empty unlisted on desktop/phone",
    );
    await c.reload();
    await c.wait(`!!document.querySelector('.playlist-contents__header')`);
    check(
      await c.evaluate(`document.documentElement.scrollWidth<=innerWidth`),
      "Canonical route survives reload",
    );
    const history = await c.send("Page.getNavigationHistory");
    check(
      !history.entries.some(
        (entry) => new URL(entry.url).pathname === "/playlist",
      ),
      "Resolver replaces history entry",
    );
  }
  await c.navigate(`${base}/playlist?list=${fixture.playlists.private}`);
  await c.wait(
    `document.querySelector('.playlist-link-page .page-state h1')?.textContent==='Плейлист недоступен'`,
  );
  check(
    await c.evaluate(
      `location.pathname==='/playlist' && !document.querySelector('.channel-page__header') && !document.querySelector('.playlist-contents__header')`,
    ),
    "Guest private failure reveals no owner or title",
  );
  await c.click(".playlist-link-page__login");
  await c.wait(`location.pathname==='/login'`);
  check(
    await c.evaluate(
      `new URLSearchParams(location.search).get('returnTo')==='/playlist?list=${fixture.playlists.private}'`,
    ),
    "Private sign in returns to original link",
  );
  await login("invited");
  await c.wait(
    `location.pathname==='/playlist' && document.querySelector('.page-state h1')?.textContent==='Плейлист недоступен'`,
  );
  check(
    await c.evaluate(`!document.querySelector('.channel-page__header')`),
    "Video invitation does not open private playlist",
  );
  await c.navigate(
    `${base}/login?returnTo=${encodeURIComponent("/playlist?list=" + fixture.playlists.empty_private)}`,
  );
  await login("owner");
  await c.wait(
    `location.pathname.startsWith('/@') && !!document.querySelector('.playlist-contents__header')`,
  );
  check(
    await c.evaluate(
      `document.querySelector('.playlist-contents__visibility').textContent==='Приватный плейлист' && document.querySelector('.playlist-contents .page-state h1').textContent==='Здесь пока нет доступных видео'`,
    ),
    "Owner resolves empty private after login",
  );
  await open("private");
  check(
    await c.evaluate(
      `document.querySelectorAll('.playlist-contents__videos li').length===2`,
    ),
    "Owner sees permitted private entries",
  );
  await c.click(".avatar-button");
  await c.click("#logout-button");
  await c.wait(
    `!!document.querySelector('.masthead .sign-in') && !document.querySelector('.playlist-contents__header')`,
  );
  check(
    await c.evaluate(`!document.querySelector('.playlist-contents__videos')`),
    "Logout clears resolved private playlist",
  );
  const count = calls.filter((url) => url.includes("/playlists/")).length;
  for (const query of [
    "",
    "list=0",
    "list=-1",
    "list=9223372036854775808",
    "list=01",
    "list=1&list=2",
    "list=javascript:alert(1)",
  ]) {
    await c.navigate(`${base}/playlist${query ? "?" + query : ""}`);
    await c.wait(
      `document.querySelector('.playlist-link-page .page-state h1')?.textContent==='Плейлист недоступен'`,
    );
  }
  check(
    calls.filter((url) => url.includes("/playlists/")).length === count,
    "Invalid IDs rejected before API and redirect",
  );
  failing = true;
  await c.navigate(`${base}/playlist?list=${fixture.playlists.public}`);
  await c.wait(
    `document.querySelector('.playlist-link-page .page-state h1')?.textContent==='Не удалось открыть плейлист'`,
  );
  check(
    await c.evaluate(`!document.querySelector('.channel-page__header')`),
    "Network failure does not invent author",
  );
  failing = false;
  await c.click(".playlist-link-page .page-state button");
  await c.wait(
    `location.pathname.startsWith('/@') && !!document.querySelector('.playlist-contents__header')`,
  );
  check(true, "Retry resolves actual channel");
  slow = true;
  await c.navigate(`${base}/playlist?list=${fixture.playlists.public}`);
  await c.evaluate(`document.querySelector('.masthead__brand').click()`);
  await c.wait(`location.pathname==='/'`);
  await pause(900);
  slow = false;
  check(
    await c.evaluate(
      `location.pathname==='/' && !document.querySelector('.channel-page__header')`,
    ),
    "Late resolver cannot redirect after leaving",
  );
  check(c.errors.length === 0, "No uncaught resolver errors");
  console.log(
    JSON.stringify(
      {
        status: "pass",
        assertions,
        engine: "Chromium",
        screens: ["1920x935", "390x844"],
        backend: "real isolated API/PostgreSQL/Nginx/RustFS",
      },
      null,
      2,
    ),
  );
} catch (error) {
  await c.screenshot("playlist-link-failure");
  console.error({ runtimeErrors: c.errors });
  throw error;
} finally {
  await c.close();
  server.closeAllConnections();
  server.close();
}
