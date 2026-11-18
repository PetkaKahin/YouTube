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
const held = new Set();
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
    if (feed && delay) await new Promise((resolve) => held.add(resolve));
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
function check(value, message) {
  assert.ok(value, message);
  assertions++;
}
const session =
  "document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('session-ui')";
async function spa(path) {
  await c.evaluate(
    `(async()=>{await document.querySelector('#app').__vue_app__.config.globalProperties.$router.push(${JSON.stringify(path)});return true})()`,
  );
}
async function state(text) {
  await c.wait(
    `[...document.querySelectorAll('.page-state h1')].some(x=>x.textContent===${JSON.stringify(text)})`,
  );
}
async function key(name) {
  if (firefox)
    return c.key({ Enter: "\uE007", Escape: "\uE00C", Tab: "\uE004" }[name]);
  for (const type of ["keyDown", "keyUp"])
    await c.send("Input.dispatchKeyEvent", {
      type,
      key: name,
      windowsVirtualKeyCode: { Enter: 13, Escape: 27, Tab: 9 }[name],
      ...(type === "keyDown" && name === "Enter" ? { text: "\r" } : {}),
    });
}
async function login(target = "/") {
  await c.navigate(base + "/login?returnTo=" + encodeURIComponent(target));
  await c.fill("#login-email", fixture.people.owner.email);
  await c.fill("#login-password", "abcdef");
  await c.click(".auth-card button[type=submit]");
  await c.wait(
    `${session}.account?.id===${JSON.stringify(fixture.people.owner.id)}&&!document.querySelector('#login-password')`,
  );
}
async function content(selector) {
  await c.wait(`!!document.querySelector(${JSON.stringify(selector)})`);
}
async function fits(label) {
  check(
    await c.evaluate("document.documentElement.scrollWidth<=innerWidth+1"),
    label,
  );
}
async function tap(selector) {
  if (firefox) return c.click(selector);
  const point = await c.evaluate(
    `(()=>{const r=document.querySelector(${JSON.stringify(selector)}).getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2}})()`,
  );
  await c.send("Input.dispatchTouchEvent", {
    type: "touchStart",
    touchPoints: [point],
  });
  await c.send("Input.dispatchTouchEvent", {
    type: "touchEnd",
    touchPoints: [],
  });
}
function release() {
  delay = false;
  for (const resolve of held) resolve();
  held.clear();
}
try {
  await c.viewport(1440, 900);
  if (!firefox)
    await c.send("Emulation.setFocusEmulationEnabled", { enabled: true });
  await c.navigate(base);
  await content(".home-page .video-card");
  check(
    await c.evaluate(
      "!!document.querySelector('.masthead .sign-in')&&!document.querySelector('.studio-create')",
    ),
    "guest has public navigation without author actions",
  );
  await fits("desktop pages fit viewport");
  await c.click(".masthead__menu");
  check(
    await c.evaluate(
      "document.querySelector('.app-shell').classList.contains('app-shell--collapsed')",
    ),
    "desktop navigation collapses",
  );
  await c.click(".masthead__menu");
  check(
    await c.evaluate(
      "!document.querySelector('.app-shell').classList.contains('app-shell--collapsed')",
    ),
    "desktop navigation expands",
  );
  await c.click(".skip-link");
  check(
    await c.evaluate("document.activeElement.id==='main-content'"),
    "skip link focuses page content",
  );
  await c.fill(".search-form input", "nature");
  await c.evaluate("document.querySelector('.search-form input').focus()");
  await key("Enter");
  await c.wait(
    "location.pathname==='/results'&&new URLSearchParams(location.search).get('search_query')==='nature'",
  );
  check(true, "keyboard search submits URL state");
  await c.evaluate("history.back()");
  await c.wait("location.pathname==='/'");
  check(true, "back returns to home");
  await c.evaluate("history.forward()");
  await c.wait("location.pathname==='/results'");
  await c.reload();
  check(
    await c.evaluate(
      "document.querySelector('.search-form input').value==='nature'",
    ),
    "forward and reload preserve search",
  );
  await spa("/results?search_query=absent_" + crypto.randomUUID());
  await state("Ничего не найдено");
  check(true, "real empty search differs from loading or error");
  await spa("/missing-final-route");
  await state("Страница не найдена");
  await c.click(".page-state a");
  await content(".home-page .video-card");
  check(true, "unknown route offers working return to home");
  failure = "all";
  await c.navigate(base);
  await state("Не удалось загрузить видео");
  check(
    await c.evaluate(
      "document.querySelector('.page-state').getAttribute('role')==='alert'&&!document.querySelector('.home-page .video-card')",
    ),
    "failed feed has retry and hides old cards",
  );
  failure = "";
  delay = true;
  await c.click(".page-state button");
  await c.wait(
    "!!document.querySelector('.home-page [aria-label=\"Загружаем видео\"]')",
  );
  check(
    await c.evaluate("!document.querySelector('.home-page .video-card')"),
    "retry waits for real response without stale success",
  );
  release();
  await content(".home-page .video-card");
  check(true, "retry restores server feed");
  await c.navigate(base + "/playlist?list=" + fixture.playlists.public);
  await c.wait(
    `new URLSearchParams(location.search).get('list')===${JSON.stringify(fixture.playlists.public)}&&location.pathname.startsWith('/@')`,
  );
  check(true, "direct playlist resolves exact id to channel contents");
  await c.navigate(base + "/watch?v=" + v.public);
  await c.wait(
    "document.querySelector('.player video')?.readyState>=2&&!document.querySelector('.player--restoring')",
  );
  check(
    await c.evaluate(
      "!!document.querySelector('.watch-page__metadata h1')&&!!document.querySelector('.watch-page__feed')",
    ),
    "direct watch opens player and feed",
  );
  await spa("/feed/history");
  await c.wait(
    "document.querySelector('.library-page')?.textContent.includes('Войдите')",
  );
  check(
    await c.evaluate(
      "!document.querySelector('.player video')&&!document.querySelector('.masthead--ambient')",
    ),
    "protected section prompts guest and releases playback",
  );
  await login();
  await content(".avatar-button");
  check(
    await c.evaluate(
      "!!document.querySelector('.studio-create')&&!document.querySelector('.masthead .sign-in')",
    ),
    "login switches shell to confirmed author",
  );
  await c.click('[aria-label="Меню аккаунта"]');
  await content(".account-menu");
  await key("Escape");
  check(
    await c.evaluate(
      "!document.querySelector('.account-menu')&&document.activeElement.getAttribute('aria-label')==='Меню аккаунта'",
    ),
    "account Escape closes and returns focus",
  );
  for (const kind of ["subscriptions", "history", "liked", "playlists"]) {
    await spa("/feed/" + kind);
    await c.wait(
      "!!document.querySelector('.library-page')&&!document.querySelector('.library-page')?.textContent.includes('Проверяем аккаунт')",
    );
    await c.reload();
    await c.wait(
      `!!document.querySelector('.library-page')&&!${session}.loading&&!${session}.changing`,
    );
    check(
      await c.evaluate(
        `location.pathname==='/feed/${kind}'&&!!document.querySelector('.library-page')&&!document.querySelector('#login-password')`,
      ),
      "personal " + kind + " survives direct reload",
    );
  }
  await spa("/@" + fixture.people.owner.tag);
  await content(".channel-page");
  check(
    await c.evaluate(
      "!document.querySelector('.own-video-table')&&!document.querySelector('.studio-shell')",
    ),
    "own public channel keeps ordinary channel interface",
  );
  await spa("/studio");
  await content(".own-video-table tbody tr");
  check(
    await c.evaluate("location.pathname==='/studio/content/videos'"),
    "studio alias opens own published catalogue",
  );
  await c.navigate(base + "/studio/video/" + v.public + "/edit");
  await content(".video-settings form");
  check(true, "direct author settings restore session and data");
  await fits("desktop author settings fit viewport");
  await c.viewport(390, 844, true);
  await fits("phone author settings fit viewport");
  await spa("/");
  await content(".masthead__search");
  await tap(".mobile-navigation button");
  await content(".navigation--open");
  check(
    await c.evaluate(
      "document.querySelector('.navigation').contains(document.activeElement)&&document.querySelector('.app-shell__content').inert",
    ),
    "phone drawer focuses navigation and isolates background",
  );
  await key("Escape");
  check(
    await c.evaluate(
      "!document.querySelector('.navigation--open')&&document.activeElement.matches('.mobile-navigation button')",
    ),
    "phone drawer closes by keyboard and restores trigger",
  );
  await tap(".masthead__search");
  await c.wait(
    "location.hash==='#searching'&&document.activeElement.matches('.search-form input')",
  );
  check(true, "phone search opens from touch and focuses input");
  await c.evaluate("history.back()");
  await c.wait("location.hash===''");
  await c.evaluate("history.forward()");
  await c.wait("location.hash==='#searching'");
  await c.reload();
  check(
    await c.evaluate(
      "document.querySelector('.app-shell').classList.contains('app-shell--search')",
    ),
    "phone search survives back forward and reload",
  );
  await c.fill(".search-form input", "nature");
  await c.evaluate("document.querySelector('.search-form input').focus()");
  await key("Enter");
  await c.wait("location.pathname==='/results'&&location.hash===''");
  check(true, "phone keyboard search closes overlay and navigates");
  await fits("phone search results fit viewport");
  await tap(".studio-create");
  await content(".studio-create-menu");
  await c.click(".studio-create-menu button");
  await content(".playlist-dialog[open]");
  check(
    await c.evaluate(
      "document.querySelector('.playlist-dialog').contains(document.activeElement)",
    ),
    "create playlist dialog receives keyboard focus",
  );
  await fits("phone creation dialog fits viewport");
  await key("Escape");
  await c.wait("!document.querySelector('.playlist-dialog[open]')");
  check(
    await c.evaluate("document.activeElement.matches('.studio-create')"),
    "cancel creation returns focus without mutation",
  );
  check(
    await c.evaluate("location.pathname==='/studio/content/playlists'"),
    "header creation opens the Studio playlist workflow",
  );
  await spa("/");
  await content(".masthead .avatar-button");
  await c.click('[aria-label="Меню аккаунта"]');
  await c.click("#logout-button");
  await c.wait(`${session}.account===undefined||${session}.account===null`);
  await c.wait("!document.querySelector('.avatar-button')");
  check(
    await c.evaluate(
      "!document.querySelector('.studio-create')&&!document.querySelector('.account-menu')",
    ),
    "logout clears author controls and menu",
  );
  await spa("/feed/playlists");
  await c.wait(
    "document.querySelector('.library-page')?.textContent.includes('Войдите')",
  );
  check(
    await c.evaluate("!document.querySelector('.playlist-card')"),
    "logout leaves no personal playlists in next screen",
  );
  check(c.errors.length === 0, "no unexpected runtime exceptions");
  console.log(
    JSON.stringify({
      profile: "final-shell",
      engine: firefox ? "Firefox" : "Chromium",
      assertions,
      status: "pass",
    }),
  );
} catch (error) {
  console.error(
    JSON.stringify({
      profile: "final-shell",
      assertions,
      path: await c.evaluate("location.pathname"),
      titles: await c.evaluate(
        "[...document.querySelectorAll('.page-state h1')].map(x=>x.textContent)",
      ),
      errors: c.errors,
    }),
  );
  throw error;
} finally {
  failure = "";
  release();
  await c.close();
  for (const socket of sockets) socket.destroy();
  server.closeAllConnections();
  server.close();
}
