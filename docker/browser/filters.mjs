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
  delay = "",
  delayFilter = false;
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
    if (
      search &&
      (term === delay ||
        (delayFilter &&
          new URL(req.url, "http://test").searchParams.get("duration") ===
            "short"))
    )
      await pause(650);
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
function parameters(values) {
  const result = new URLSearchParams();
  for (const [key, value] of Object.entries(values))
    for (const item of Array.isArray(value) ? value : [value])
      result.append(key, item);
  return result;
}
function url(q, params = {}) {
  return "/results?" + parameters({ search_query: q, ...params });
}
async function query(q, params = {}) {
  await navigate(url(q, params));
}
async function api(q, params = {}) {
  const keys = [],
    pages = [];
  let cursor;
  do {
    const qs = parameters({ q, ...params });
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
const filter = (value) => `[data-filter="${value}"]`;
const openDialog = `document.querySelector('dialog[open]')`;
async function open() {
  await c.evaluate("window.scrollTo(0,0)");
  await c.click(".filters-toggle");
  await c.wait(
    `document.querySelector('.filters-toggle').getAttribute('aria-expanded')==='true'`,
  );
}
async function pick(value) {
  await open();
  await c.click(filter(value));
  await c.wait(
    `document.querySelector('.filters-toggle').getAttribute('aria-expanded')==='false'&&!${openDialog}`,
  );
}
async function pressed(value) {
  return c.evaluate(
    `document.querySelector(${JSON.stringify(filter(value))}).getAttribute('aria-pressed')==='true'`,
  );
}
async function same(params) {
  const expected = await api("и", params);
  if (!expected.keys.length) {
    await empty();
    return [];
  }
  const rows = await loadAll(expected.keys.length);
  assert.deepEqual(
    rows,
    expected.keys,
    "Filtered results match all real API pages",
  );
  return rows;
}
async function keyPress(name, value, code) {
  if (firefox) await c.key(value);
  else
    for (const type of ["keyDown", "keyUp"])
      await c.send("Input.dispatchKeyEvent", {
        type,
        key: name,
        code: name,
        windowsVirtualKeyCode: code,
        ...(name === "Enter" && type === "keyDown" ? { text: "\r" } : {}),
      });
}
try {
  await c.viewport(1280, 900);
  await query("и");
  await c.wait(`!!document.querySelector('.search-results > article')`);
  await pick("type:video");
  check(
    await c.evaluate(
      `new URLSearchParams(location.search).get('type')==='video'&&document.activeElement===document.querySelector('.filters-toggle')`,
    ),
    "Desktop selection applies immediately, closes panel and restores focus",
  );
  await same({ type: "video" });
  const before = searchCalls().length;
  await pick("duration:short");
  await same({ type: "video", duration: "short" });
  const first = new URL(searchCalls()[before], "http://test").searchParams;
  check(
    first.get("duration") === "short" && !first.has("cursor"),
    "New filter starts at first server page",
  );
  await pick("duration:medium");
  await same({ type: "video", duration: "medium" });
  check(
    await c.evaluate(
      `new URLSearchParams(location.search).getAll('duration').join(',')==='medium'`,
    ),
    "Duration is replaced rather than appended",
  );
  await pick("duration:long");
  await same({ type: "video", duration: "long" });
  check(true, "All three duration values use real API filtering");
  await pick("duration:long");
  await same({ type: "video" });
  check(
    await c.evaluate(`!new URLSearchParams(location.search).has('duration')`),
    "Selecting active duration removes its restriction",
  );

  for (const period of ["today", "week", "month", "year"]) {
    await pick("period:" + period);
    await same({ type: "video", period });
    check(
      await c.evaluate(
        `new URLSearchParams(location.search).getAll('period').join(',')==='${period}'`,
      ),
      "Calendar period replaces prior selection and reaches server",
    );
  }
  await pick("period:year");
  for (const feature of ["hd", "subtitles", "4k", "hdr"]) {
    await pick("feature:" + feature);
    await same({ type: "video", feature });
    await open();
    check(
      await pressed("feature:" + feature),
      "Feature reopens selected from URL",
    );
    await c.click(filter("feature:" + feature));
    await c.wait(
      `document.querySelector('.filters-toggle').getAttribute('aria-expanded')==='false'`,
    );
  }
  await same({ type: "video" });
  const hd = (await api("и", { type: "video", feature: "hd" })).keys;
  const subs = (await api("и", { type: "video", feature: "subtitles" })).keys;
  await pick("feature:hd");
  await pick("feature:subtitles");
  const intersection = await same({
    type: "video",
    feature: ["hd", "subtitles"],
  });
  check(
    intersection.length === hd.filter((x) => subs.includes(x)).length &&
      hd.some((x) => !subs.includes(x)),
    "HD and subtitles are combined with AND, excluding ready video without subtitles",
  );
  await open();
  check(
    (await pressed("feature:hd")) && (await pressed("feature:subtitles")),
    "Multiple active features survive reopening",
  );
  await c.click(filter("feature:hd"));
  await same({ type: "video", feature: "subtitles" });
  check(
    await c.evaluate(
      `new URLSearchParams(location.search).getAll('feature').join(',')==='subtitles'`,
    ),
    "Removing one feature preserves the other",
  );
  await pick("feature:subtitles");
  await pick("feature:4k");
  await pick("feature:hdr");
  await same({ type: "video", feature: ["4k", "hdr"] });
  check(
    await c.evaluate(
      `new URLSearchParams(location.search).getAll('feature').sort().join(',')==='4k,hdr'`,
    ),
    "4K and HDR remain separate repeated parameters",
  );
  await pick("feature:4k");
  await pick("feature:hdr");

  await login("owner");
  const changed = await c.evaluate(
    `(async()=>{const settings=await (await fetch('/api/v1/videos/${v.public}/settings')).json();const csrf=decodeURIComponent(document.cookie.split('; ').find(x=>x.startsWith('csrftoken=')).slice(10));const r=await fetch('/api/v1/videos/${v.public}/settings',{method:'PUT',headers:{'content-type':'application/json','x-csrftoken':csrf},body:JSON.stringify({title:settings.title,description:settings.description+'\\nПроверка сортировки',visibility:'public'})});return r.status;})()`,
  );
  assert.equal(changed, 200);
  await query("и", { type: "video" });
  const created = await same({ type: "video" });
  await pick("sort:updated");
  const updated = await same({ type: "video", sort: "updated" });
  check(
    updated[0] === "video:" + href(v.public) && created[0] !== updated[0],
    "Updated sort reflects a real metadata edit, not client-side sorting",
  );
  await pick("sort:created");
  check(
    JSON.stringify(await same({ type: "video", sort: "created" })) ===
      JSON.stringify(created),
    "Created sort restores original API order",
  );
  await pick("sort:created");
  await open();
  check(
    (await pressed("sort:created")) &&
      (await c.evaluate(`!new URLSearchParams(location.search).has('sort')`)),
    "Created remains default after clearing explicit sort",
  );
  await keyPress("Escape", "\uE00C", 27);
  check(
    await c.evaluate(
      `document.querySelector('.filters-toggle').getAttribute('aria-expanded')==='false'&&document.activeElement===document.querySelector('.filters-toggle')`,
    ),
    "Desktop Escape closes panel and returns focus",
  );

  const combination = {
    type: "playlist",
    duration: "short",
    period: "today",
    feature: ["hd", "subtitles"],
    sort: "updated",
  };
  const combinedStart = searchCalls().length;
  await query("и", combination);
  await same(combination);
  const combinedCalls = searchCalls()
    .slice(combinedStart)
    .map((x) => new URL(x, "http://test").searchParams);
  check(
    combinedCalls.some((x) => x.has("cursor")) &&
      combinedCalls.every(
        (x) =>
          x.get("type") === "playlist" &&
          x.get("duration") === "short" &&
          x.get("period") === "today" &&
          x.get("sort") === "updated" &&
          x.getAll("feature").sort().join(",") === "hd,subtitles",
      ),
    "Every cursor request retains all constraints",
  );
  await open();
  check(
    (await pressed("duration:short")) &&
      (await pressed("period:today")) &&
      (await pressed("sort:updated")) &&
      (await pressed("feature:hd")) &&
      (await pressed("feature:subtitles")),
    "Direct filtered URL restores every selected group",
  );
  await c.evaluate(
    `document.querySelector('.search-form input').dispatchEvent(new PointerEvent('pointerdown',{bubbles:true}));document.querySelector('.search-form input').focus()`,
  );
  check(
    await c.evaluate(
      `document.querySelector('.filters-toggle').getAttribute('aria-expanded')==='false'&&document.activeElement===document.querySelector('.search-form input')`,
    ),
    "Outside pointer closes desktop panel without stealing focus",
  );

  await query("и", {
    type: "channel",
    duration: "long",
    feature: ["4k", "hdr"],
  });
  check(
    (await same({ type: "channel", duration: "long", feature: ["4k", "hdr"] }))
      .length === 3,
    "Video-only constraints do not filter channels",
  );
  await query("и", {
    type: "playlist",
    duration: "long",
    feature: ["4k", "hdr"],
  });
  check(
    (await same({ type: "playlist", duration: "long", feature: ["4k", "hdr"] }))
      .length === 41,
    "Video-only constraints do not filter playlist contents or hide public playlists",
  );

  await query("и", { type: "video" });
  await same({ type: "video" });
  await pick("duration:short");
  await same({ type: "video", duration: "short" });
  await pick("period:today");
  await same({ type: "video", duration: "short", period: "today" });
  await c.evaluate("history.back()");
  await c.wait(`!new URLSearchParams(location.search).has('period')`);
  await open();
  check(
    (await pressed("duration:short")) && !(await pressed("period:today")),
    "Back restores previous selected filters",
  );
  await keyPress("Escape", "\uE00C", 27);
  await c.evaluate("history.forward()");
  await c.wait(`new URLSearchParams(location.search).get('period')==='today'`);
  await c.reload();
  await open();
  check(
    (await pressed("duration:short")) && (await pressed("period:today")),
    "Forward and reload retain URL selection",
  );
  await keyPress("Escape", "\uE00C", 27);

  for (const params of [
    { type: ["video", "channel"] },
    { duration: ["short", "long"] },
    { period: ["today", "week"] },
    { sort: ["created", "updated"] },
    { feature: ["hd", "<script>"] },
    { duration: "" },
    { sort: "unknown" },
  ]) {
    const start = searchCalls().length;
    await query("и", params);
    await c.wait(
      `document.querySelector('.page-state h1')?.textContent==='Некорректные фильтры поиска'`,
    );
    check(
      searchCalls().length === start &&
        !(await c.evaluate(
          `!!document.querySelector('.search-results')||!!document.querySelector('.page-state button')`,
        )),
      "Invalid/repeated single filter cannot silently broaden query or create inert retry",
    );
  }
  await c.click(".page-state a");
  await same({});
  check(
    await c.evaluate(
      `location.search==='?search_query=%D0%B8'&&document.querySelector('.search-form input').value==='и'`,
    ),
    "Invalid-filter reset preserves search text and recovers",
  );
  await query("и", { type: "video", feature: ["hd", "hd"] });
  await same({ type: "video", feature: "hd" });
  check(
    new URL(searchCalls().at(-1), "http://test").searchParams
      .getAll("feature")
      .join(",") === "hd",
    "Repeated identical features are normalized before API",
  );

  await query("и", { type: "video" });
  await same({ type: "video" });
  delayFilter = true;
  await pick("duration:short");
  await c.wait(`!!document.querySelector('.search-page__loading')`);
  await pick("duration:medium");
  await empty();
  await pause(800);
  delayFilter = false;
  check(
    (await actual()).length === 0 &&
      (await c.evaluate(
        `new URLSearchParams(location.search).get('duration')==='medium'`,
      )),
    "Late response for old filters cannot restore stale rows",
  );
  failure = "all";
  await pick("duration:short");
  await c.wait(
    `document.querySelector('.page-state h1')?.textContent==='Не удалось выполнить поиск'`,
  );
  await open();
  check(
    await pressed("duration:short"),
    "Network error keeps requested selection",
  );
  await keyPress("Escape", "\uE00C", 27);
  failure = "";
  await c.click(".page-state button");
  await same({ type: "video", duration: "short" });
  check(true, "Retry preserves filter constraints and uses real API");

  await c.viewport(390, 600);
  await open();
  await c.wait(`!!${openDialog}`);
  check(
    await c.evaluate(
      `${openDialog}.matches(':modal')&&${openDialog}.contains(document.activeElement)&&document.body.style.overflow==='hidden'`,
    ),
    "Phone filters are modal, focused and lock background scrolling",
  );
  check(await pressed("duration:short"), "Phone modal uses same URL selection");
  await c.evaluate(`${openDialog}.scrollTop=${openDialog}.scrollHeight`);
  check(
    await c.evaluate(`${openDialog}.scrollTop>0`),
    "Long phone modal scrolls to lower controls",
  );
  await c.evaluate(
    `${openDialog}.querySelector('[data-filter="sort:updated"]').focus()`,
  );
  await keyPress("Tab", "\uE004", 9);
  check(
    await c.evaluate(
      `document.activeElement===${openDialog}.querySelector('header button')`,
    ),
    "Tab from final option stays inside modal",
  );
  await keyPress("Escape", "\uE00C", 27);
  check(
    await c.evaluate(
      `!${openDialog}&&document.body.style.overflow===''&&document.activeElement===document.querySelector('.filters-toggle')`,
    ),
    "Phone Escape restores focus and scrolling without changing filters",
  );
  await open();
  await c.click(filter("period:today"));
  await c.wait(`!${openDialog}`);
  await same({ type: "video", duration: "short", period: "today" });
  check(
    await c.evaluate(
      `document.body.style.overflow===''&&new URLSearchParams(location.search).get('period')==='today'`,
    ),
    "Phone choice immediately applies and closes modal",
  );
  await open();
  await c.click("dialog[open] header button");
  check(
    await c.evaluate(
      `!${openDialog}&&document.activeElement===document.querySelector('.filters-toggle')`,
    ),
    "Phone close button returns focus",
  );
  await open();
  await c.evaluate("history.back()");
  await c.wait(`!${openDialog}`);
  check(
    await c.evaluate(
      `document.body.style.overflow===''&&!new URLSearchParams(location.search).has('period')`,
    ),
    "History navigation closes phone modal and releases scrolling",
  );
  await open();
  await c.viewport(1280, 900);
  await c.wait(
    `!${openDialog}&&document.querySelector('.filters-toggle').getAttribute('aria-expanded')==='false'`,
  );
  check(
    await c.evaluate(`document.body.style.overflow===''`),
    "Viewport change releases modal without trapping the page",
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
        backend: "isolated real filtered search and metadata",
      },
      null,
      2,
    ),
  );
} catch (error) {
  console.error({
    runtime: c.errors,
    location: await c.evaluate("location.pathname+location.search"),
    rows: await actual(),
    lastSearch: searchCalls().slice(-2),
  });
  throw error;
} finally {
  failure = "";
  delayFilter = false;
  await c.close();
  for (const socket of sockets) socket.destroy();
  server.closeAllConnections();
  server.close();
}
