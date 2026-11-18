import assert from "node:assert/strict";
import { createServer, request as httpRequest } from "node:http";
import { randomUUID } from "node:crypto";
import { browser, pause } from "./cdp.mjs";
import { firefoxBrowser } from "./bidi.mjs";

const firefox = process.argv.includes("--firefox");
let listFailure = false,
  createFailure = 0,
  slowCreate = false,
  slowList = false;
const requests = [];
const server = createServer(async (req, res) => {
  try {
    const api = req.url.startsWith("/api/");
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    if (api)
      requests.push({
        url: req.url,
        method: req.method,
        csrf: !!req.headers["x-csrftoken"],
      });
    const list = req.url.startsWith("/api/v1/playlists/mine"),
      create = req.url === "/api/v1/playlists" && req.method === "POST";
    if ((list && listFailure) || (create && createFailure)) {
      res
        .writeHead(list ? 503 : createFailure, {
          "content-type": "application/problem+json",
        })
        .end(
          JSON.stringify(
            createFailure === 422 && create
              ? {
                  code: "validation_error",
                  errors: [
                    { location: ["body", "title"], code: "invalid_length" },
                  ],
                }
              : { code: "unavailable" },
          ),
        );
      return;
    }
    if ((create && slowCreate) || (list && slowList)) await pause(650);
    const headers = new Headers();
    for (const key of ["content-type", "cookie", "x-csrftoken", "accept"])
      if (req.headers[key]) headers.set(key, req.headers[key]);
    const upstream = await fetch(
      api
        ? `http://python:8181${req.url.slice(4)}`
        : `http://node:5173${req.url}`,
      {
        method: req.method,
        headers,
        body: chunks.length ? Buffer.concat(chunks) : undefined,
        redirect: "manual",
      },
    );
    res.statusCode = upstream.status;
    for (const key of ["content-type", "location", "cache-control"])
      if (upstream.headers.has(key))
        res.setHeader(key, upstream.headers.get(key));
    if (upstream.headers.getSetCookie().length)
      res.setHeader("set-cookie", upstream.headers.getSetCookie());
    res.end(Buffer.from(await upstream.arrayBuffer()));
  } catch {
    res.writeHead(502).end();
  }
});
// Keep Vite HMR on the temporary origin, including its WebSocket connection.
const sockets = new Set();
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
await new Promise((resolve) => server.listen(4185, "127.0.0.1", resolve));
let ready = false;
for (let attempt = 0; attempt < 200; attempt++) {
  try {
    if ((await fetch("http://python:8181/v1/auth/me")).status === 401) {
      ready = true;
      break;
    }
  } catch {}
  await pause(100);
}
if (!ready) throw new Error("Isolated API unavailable");
const c = firefox ? await firefoxBrowser() : await browser();
const base = "http://127.0.0.1:4185",
  studio = "/studio/content/playlists";
const token = randomUUID().replaceAll("-", "").slice(0, 16),
  tag = "studio" + token;
const email = `${token}@example.com`,
  other = `${token}other@example.com`;
let assertions = 0;
const check = (value, message) => {
  assert.ok(value, message);
  assertions++;
};
const write = (url, body) =>
  c.evaluate(
    `(async()=>{const csrf=decodeURIComponent(document.cookie.split('; ').find(x=>x.startsWith('csrftoken=')).slice(10));const r=await fetch(${JSON.stringify(url)},{method:'POST',headers:{'content-type':'application/json','x-csrftoken':csrf},body:JSON.stringify(${JSON.stringify(body)})});return r.status})()`,
  );
const escape = async () => {
  if (firefox) await c.key("\uE00C");
  else {
    await c.send("Input.dispatchKeyEvent", {
      type: "keyDown",
      key: "Escape",
      code: "Escape",
    });
    await c.send("Input.dispatchKeyEvent", {
      type: "keyUp",
      key: "Escape",
      code: "Escape",
    });
  }
};
const posts = () =>
  requests.filter((r) => r.url === "/api/v1/playlists" && r.method === "POST")
    .length;
async function login(who = email) {
  await c.navigate(`${base}/login?returnTo=${encodeURIComponent(studio)}`);
  await c.fill("#login-email", who);
  await c.fill("#login-password", "abcdef");
  await c.click(".auth-card button[type=submit]");
  await c.wait(
    `location.pathname===${JSON.stringify(studio)} && !!document.querySelector('.studio-create') && !document.querySelector('.studio-playlists .page-state h1')?.textContent.startsWith('Загружаем')`,
  );
}
async function tab() {
  if (firefox) await c.key("\uE004");
  else {
    await c.send("Input.dispatchKeyEvent", {
      type: "keyDown",
      key: "Tab",
      code: "Tab",
      windowsVirtualKeyCode: 9,
    });
    await c.send("Input.dispatchKeyEvent", {
      type: "keyUp",
      key: "Tab",
      code: "Tab",
      windowsVirtualKeyCode: 9,
    });
  }
}
async function open() {
  await c.click(".studio-create");
  await c.click("#studio-create-menu button");
  await c.wait(
    `document.querySelector('.playlist-dialog').open && document.activeElement.id==='playlist-title'`,
  );
}
try {
  await c.viewport(1920, 935);
  await c.navigate(base + studio);
  await c.wait(`!!document.querySelector('.studio-playlists__login')`);
  check(
    !requests.some((r) => r.url.includes("/playlists/mine")),
    "Guest does not request personal data",
  );
  await c.click(".studio-playlists__login .button");
  await c.wait(`location.pathname==='/login'`);
  check(
    await c.evaluate(
      `new URLSearchParams(location.search).get('returnTo')===${JSON.stringify(studio)}`,
    ),
    "Sign in retains Studio destination",
  );
  for (const [address, accountTag] of [
    [email, tag],
    [other, tag + "b"],
  ])
    check(
      (await write("/api/v1/auth/register", {
        name: "Автор Студии",
        email: address,
        tag: accountTag,
        password: "abcdef",
        password_confirmation: "abcdef",
      })) === 201,
      "Register isolated owner",
    );
  await login();
  await c.wait(
    `document.querySelector('.studio-playlists .page-state h1')?.textContent==='Пока нет плейлистов'`,
  );
  check(true, "Empty owner list");
  await open();
  check(
    await c.evaluate(
      `document.querySelector('#playlist-visibility').value==='public' && document.querySelector('.playlist-dialog button[type=submit]').disabled`,
    ),
    "Reference public default and empty title disabled",
  );
  check(
    await c.evaluate(
      `document.querySelector('.playlist-dialog').getBoundingClientRect().width===576`,
    ),
    "Reference modal width",
  );
  await c.screenshot(
    `studio-modal-${firefox ? "firefox" : "chromium"}-desktop`,
  );
  await c.fill("#playlist-title", "   ");
  check(
    await c.evaluate(
      `document.querySelector('.playlist-dialog button[type=submit]').disabled`,
    ),
    "Whitespace title disabled",
  );
  const beforeInvalid = posts();
  await c.fill("#playlist-title", "😀".repeat(101));
  await c.evaluate(
    `document.querySelector('.playlist-dialog form').requestSubmit()`,
  );
  check(
    await c.evaluate(
      `document.querySelector('#playlist-title').value.length===202 && document.activeElement.id==='playlist-title'`,
    ),
    "Overlong Unicode title retained and focused",
  );
  await c.fill("#playlist-title", "😀".repeat(100));
  await c.fill("#playlist-description", "я".repeat(5001));
  await c.evaluate(
    `document.querySelector('.playlist-dialog form').requestSubmit()`,
  );
  check(
    await c.evaluate(
      `document.querySelector('#playlist-description').value.length===5001 && document.activeElement.id==='playlist-description'`,
    ),
    "Description limit does not truncate draft",
  );
  check(posts() === beforeInvalid, "Invalid drafts do not send writes");
  await c.fill("#playlist-description", "я".repeat(5000));
  await c.evaluate(
    `document.querySelector('.playlist-dialog button[type=submit]').focus()`,
  );
  await tab();
  check(
    await c.evaluate(`document.activeElement.closest('dialog')?.open===true`),
    "Tab remains inside modal",
  );
  slowCreate = true;
  const beforeDouble = posts();
  await c.evaluate(
    `const form=document.querySelector('.playlist-dialog form');form.requestSubmit();form.requestSubmit();`,
  );
  await c.wait(
    `document.querySelector('.playlist-dialog form').getAttribute('aria-busy')==='true'`,
  );
  await escape();
  check(
    await c.evaluate(`document.querySelector('.playlist-dialog').open`),
    "Pending request keeps modal open",
  );
  check(
    await c.evaluate(
      `document.querySelector('#playlist-title').disabled && document.querySelector('[aria-label="Закрыть создание плейлиста"]').disabled`,
    ),
    "Pending state prevents duplicate editing",
  );
  await c.wait(
    `!document.querySelector('.playlist-dialog').open && document.querySelector('.studio-playlists__notice')?.textContent==='Плейлист создан'`,
  );
  slowCreate = false;
  await c.wait(
    `document.querySelectorAll('.studio-table tbody tr').length===1`,
  );
  check(posts() === beforeDouble + 1, "Repeated submit sends one creation");
  check(
    await c.evaluate(
      `document.querySelector('.studio-table strong').textContent===${JSON.stringify("😀".repeat(100))}`,
    ),
    "100 Unicode title and 5000 description accepted by real API",
  );
  const id = await c.evaluate(
    `document.querySelector('.studio-table tbody tr').dataset.playlistId`,
  );
  check(
    BigInt(id) > 9007199254740991n &&
      (await c.evaluate(
        `document.querySelector('.studio-table__playlist').getAttribute('href')==='/playlist?list=${id}'`,
      )),
    "Exact int64 creation and link",
  );
  check(
    requests
      .filter((r) => r.url === "/api/v1/playlists" && r.method === "POST")
      .every((r) => r.csrf),
    "SDK sends CSRF",
  );
  for (const [mode, title] of [
    ["private", '<img src=x onerror="window.studioXss=1">'],
    ["unlisted", "По ссылке"],
  ]) {
    await open();
    await c.fill("#playlist-title", title);
    await c.evaluate(
      `(()=>{const select=document.querySelector('#playlist-visibility');select.value=${JSON.stringify(mode)};select.dispatchEvent(new Event('change',{bubbles:true}));})()`,
    );
    await c.click(".playlist-dialog button[type=submit]");
    await c.wait(
      `!document.querySelector('.playlist-dialog').open && document.querySelector('.studio-table strong')?.textContent===${JSON.stringify(title)}`,
    );
  }
  check(
    await c.evaluate(
      `document.querySelectorAll('.studio-table tbody tr').length===3 && !document.querySelector('.studio-table img') && !window.studioXss`,
    ),
    "All visibilities and safe user text",
  );
  await open();
  await c.fill("#playlist-title", "Сохранённый черновик");
  createFailure = 503;
  await c.click(".playlist-dialog button[type=submit]");
  await c.wait(`!!document.querySelector('.playlist-dialog .form-error')`);
  check(
    await c.evaluate(
      `document.querySelector('.playlist-dialog').open && document.querySelector('#playlist-title').value==='Сохранённый черновик' && !document.querySelector('.playlist-dialog button[type=submit]').disabled`,
    ),
    "Failed creation preserves retryable draft",
  );
  createFailure = 422;
  await c.click(".playlist-dialog button[type=submit]");
  await c.wait(
    `document.querySelector('#playlist-title').getAttribute('aria-invalid')==='true' && document.activeElement.id==='playlist-title'`,
  );
  check(true, "API validation maps and focuses field");
  createFailure = 0;
  await escape();
  await c.wait(
    `!document.querySelector('.playlist-dialog').open && document.activeElement.classList.contains('studio-create')`,
  );
  check(
    await c.evaluate(
      `!document.querySelector('#playlist-title').value && !document.querySelector('#playlist-description').value`,
    ),
    "Escape clears draft and restores focus",
  );
  await c.navigate(`${base}/@${tag}/playlists`);
  await c.wait(`document.querySelectorAll('.playlist-card').length===1`);
  check(
    await c.evaluate(
      `!document.querySelector('.studio-create') && !document.querySelector('.studio-table') && !document.body.innerText.includes('По ссылке') && !document.body.innerText.includes('<img')`,
    ),
    "Own public channel has public contents and ordinary interface",
  );
  await c.click(".avatar-button");
  await c.click(".account-menu__studio");
  await c.wait(
    `location.pathname===${JSON.stringify(studio)} && !!document.querySelector('.studio-table')`,
  );
  check(true, "Account menu opens Studio");
  // No media needed: these are actual empty playlists in the isolated database.
  for (let index = 0; index < 38; index++)
    assert.equal(
      await write("/api/v1/playlists", {
        title: `Список ${index}`,
        description: "",
        visibility: ["public", "private", "unlisted"][index % 3],
      }),
      201,
    );
  await c.reload();
  await c.wait(
    `document.querySelectorAll('.studio-table tbody tr').length>=20`,
  );
  for (let page = 0; page < 4; page++) {
    if (
      await c.evaluate(
        `document.querySelectorAll('.studio-table tbody tr').length===41`,
      )
    )
      break;
    await c.evaluate(`window.scrollTo(0,document.body.scrollHeight)`);
    await pause(200);
    await c.evaluate(
      `document.querySelector('.studio-playlists__more button')?.click()`,
    );
    await c.wait(
      `document.querySelectorAll('.studio-table tbody tr').length===41 || !document.querySelector('.studio-playlists__more button')?.disabled`,
    );
  }
  await c.wait(
    `document.querySelectorAll('.studio-table tbody tr').length===41`,
  );
  check(
    await c.evaluate(
      `new Set([...document.querySelectorAll('.studio-table tbody tr')].map(x=>x.dataset.playlistId)).size===41`,
    ),
    "20/20/1 owner pagination without duplicates",
  );
  check(
    requests.some((r) => r.url.includes("/playlists/mine?cursor=")),
    "Server cursors used",
  );
  await c.evaluate(`window.scrollTo(0,0)`);
  await c.screenshot(`studio-list-${firefox ? "firefox" : "chromium"}-desktop`);
  for (const [width, height] of [
    [390, 844],
    [320, 568],
  ]) {
    await c.viewport(width, height, true);
    await open();
    check(
      await c.evaluate(
        `document.documentElement.scrollWidth<=innerWidth && document.querySelector('.playlist-dialog').getBoundingClientRect().width<=innerWidth-32`,
      ),
      "Phone table scrolling contained and modal fits",
    );
    await c.fill("#playlist-title", "Телефон");
    await c.evaluate(
      `document.querySelector('.playlist-dialog button[type=submit]').scrollIntoView()`,
    );
    check(
      await c.evaluate(
        `document.querySelector('.playlist-dialog button[type=submit]').getBoundingClientRect().bottom<=innerHeight`,
      ),
      "Phone create action reachable",
    );
    await c.screenshot(
      `studio-modal-${firefox ? "firefox" : "chromium"}-${width}`,
    );
    await escape();
  }
  await c.viewport(1920, 935);
  listFailure = true;
  await c.reload();
  await c.wait(
    `document.querySelector('.studio-playlists .page-state h1')?.textContent==='Не удалось загрузить плейлисты'`,
  );
  check(
    await c.evaluate(`!document.querySelector('.studio-table')`),
    "List failure does not fabricate data",
  );
  listFailure = false;
  await c.click(".studio-playlists .page-state button");
  await c.wait(`!!document.querySelector('.studio-table')`);
  check(true, "List retry recovers");
  await open();
  await c.fill("#playlist-title", "Секрет старого аккаунта");
  check(
    (await write("/api/v1/auth/logout", {})) === 204,
    "Expire actual session outside page",
  );
  await c.click(".playlist-dialog button[type=submit]");
  await c.wait(
    `!!document.querySelector('.studio-playlists__login') && !document.querySelector('.playlist-dialog')`,
  );
  check(
    await c.evaluate(
      `!document.querySelector('.studio-table') && !document.body.innerText.includes('Список 37') && !document.body.innerText.includes('Секрет старого аккаунта')`,
    ),
    "Expired session removes private list and draft",
  );
  check(
    await c.evaluate(
      `(()=>{const client=Object.values(document.querySelector('#app').__vue_app__._context.provides).find(x=>typeof x?.getQueryCache==='function');return !JSON.stringify(client.getQueryCache().getAll().map(q=>q.state.data)).includes('Список 37') && !JSON.stringify(client.getMutationCache().getAll().map(m=>m.state)).includes('Секрет старого аккаунта');})()`,
    ),
    "Expired private data and draft absent from Query and mutation caches",
  );
  await login(other);
  await c.wait(
    `document.querySelector('.studio-playlists .page-state h1')?.textContent==='Пока нет плейлистов'`,
  );
  check(
    await c.evaluate(`!document.querySelector('.studio-table')`),
    "Another owner never inherits old list",
  );
  await c.click(".studio-header .studio-avatar");
  await c.click("#studio-logout");
  await c.wait(`!!document.querySelector('.studio-playlists__login')`);
  check(
    await c.evaluate(
      `!document.querySelector('.studio-avatar') && !document.querySelector('.studio-create')`,
    ),
    "Studio logout removes owner controls",
  );
  await login();
  slowList = true;
  await c.reload();
  await c.click(".studio-logo"); // Same route while request is pending.
  await c.evaluate(
    `document.querySelector('.studio-navigation__exit').click()`,
  );
  await c.wait(`location.pathname==='/'`);
  await pause(900);
  slowList = false;
  check(
    await c.evaluate(
      `location.pathname==='/' && !document.querySelector('.studio-table')`,
    ),
    "Late personal response cannot restore Studio after leaving",
  );
  check(c.errors.length === 0, "No uncaught runtime errors");
  console.log(
    JSON.stringify(
      {
        status: "pass",
        assertions,
        engine: firefox ? "Firefox" : "Chromium",
        screens: ["1920x935", "390x844", "320x568"],
        backend: "real isolated API/PostgreSQL/Valkey",
      },
      null,
      2,
    ),
  );
} catch (error) {
  await c.screenshot(`studio-failure-${firefox ? "firefox" : "chromium"}`);
  console.error({ runtimeErrors: c.errors });
  throw error;
} finally {
  await c.close();
  for (const socket of sockets) socket.destroy();
  server.closeAllConnections();
  server.close();
}
