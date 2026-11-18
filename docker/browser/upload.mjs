import assert from "node:assert/strict";
import { createServer, request as httpRequest } from "node:http";
import { readFile } from "node:fs/promises";
import { randomUUID } from "node:crypto";
import { browser, pause } from "./cdp.mjs";
import { firefoxBrowser } from "./bidi.mjs";

const firefox = process.argv.includes("--firefox");
const source = await readFile("/media/portrait.mp4");
let fail = "",
  slow = false,
  processingState = "";
const calls = [],
  sockets = new Set();
const server = createServer(async (req, res) => {
  try {
    if (req.url === "/fixture-source") {
      res
        .writeHead(200, { "content-type": "application/octet-stream" })
        .end(source);
      return;
    }
    const api = req.url.startsWith("/api/"),
      storage = req.url.startsWith("/uploads/");
    // Never retain bearer URLs or object keys in test logs/results.
    const kind = storage
      ? "part"
      : req.url === "/api/v1/videos"
        ? "create"
        : req.url.endsWith("/complete")
          ? "complete"
          : req.url.endsWith("/publish")
            ? "publish"
            : "other";
    if (api || storage)
      calls.push({
        kind,
        method: req.method,
        csrf: !!req.headers["x-csrftoken"],
      });
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    if (slow && (kind === "part" || kind === "complete")) await pause(650);
    if (fail === kind && kind !== "other") {
      res
        .writeHead(503, { "content-type": "application/problem+json" })
        .end('{"code":"unavailable"}');
      return;
    }
    const headers = new Headers();
    for (const key of ["content-type", "cookie", "x-csrftoken", "range"])
      if (req.headers[key]) headers.set(key, req.headers[key]);
    const target =
      api || storage
        ? `http://youtube-frontend-upload-gateway:8080${req.url}`
        : `http://node:5173${req.url}`;
    const upstream = await fetch(target, {
      method: req.method,
      headers,
      body: chunks.length ? Buffer.concat(chunks) : undefined,
      redirect: "manual",
    });
    let body = Buffer.from(await upstream.arrayBuffer());
    if (processingState && req.url.endsWith("/processing") && upstream.ok) {
      const state = JSON.parse(body.toString());
      state.processing = processingState;
      if (processingState === "failed") {
        state.available_qualities = [];
        state.qualities = state.qualities.map((q) => ({
          ...q,
          status: "failed",
          error_code: "test_failure",
        }));
      } else if (processingState === "partial")
        state.qualities.push({
          quality: 2160,
          range: "sdr",
          status: "failed",
          error_code: "test_failure",
        });
      body = Buffer.from(JSON.stringify(state));
    }
    res.statusCode = upstream.status;
    for (const key of ["content-type", "location", "cache-control"])
      if (upstream.headers.has(key))
        res.setHeader(key, upstream.headers.get(key));
    if (upstream.headers.getSetCookie().length)
      res.setHeader("set-cookie", upstream.headers.getSetCookie());
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
await new Promise((resolve) => server.listen(4186, "127.0.0.1", resolve));
let ready = false;
for (let attempt = 0; attempt < 200; attempt++) {
  try {
    if (
      (
        await fetch(
          "http://youtube-frontend-upload-gateway:8080/api/v1/auth/me",
        )
      ).status === 401
    ) {
      ready = true;
      break;
    }
  } catch {}
  await pause(100);
}
if (!ready) throw new Error("Isolated upload API did not start");
const c = firefox ? await firefoxBrowser() : await browser();
const base = "http://127.0.0.1:4186",
  token = randomUUID().replaceAll("-", "").slice(0, 16);
let assertions = 0;
const check = (value, message) => {
  assert.ok(value, message);
  assertions++;
};
const count = (kind) => calls.filter((call) => call.kind === kind).length;
async function key(name, bidiValue, code) {
  if (firefox) await c.key(bidiValue);
  else {
    await c.send("Input.dispatchKeyEvent", {
      type: "keyDown",
      key: name,
      code: name,
      windowsVirtualKeyCode: code,
    });
    await c.send("Input.dispatchKeyEvent", {
      type: "keyUp",
      key: name,
      code: name,
      windowsVirtualKeyCode: code,
    });
  }
}
const write = (url, body) =>
  c.evaluate(
    `(async()=>{const csrf=decodeURIComponent(document.cookie.split('; ').find(x=>x.startsWith('csrftoken=')).slice(10));const r=await fetch(${JSON.stringify(url)},{method:'POST',headers:{'content-type':'application/json','x-csrftoken':csrf},body:JSON.stringify(${JSON.stringify(body)})});return r.status})()`,
  );
async function selectFile(name = "Исходник.unknown") {
  await c.evaluate(
    `(async()=>{const bytes=await (await fetch('/fixture-source')).arrayBuffer();const files=new DataTransfer();files.items.add(new File([bytes],${JSON.stringify(name)},{type:'application/octet-stream'}));const input=document.querySelector('#video-file');input.files=files.files;input.dispatchEvent(new Event('change',{bubbles:true}));})()`,
  );
  await c.wait(`!!document.querySelector('#video-title')`);
}
const refetchProcessing = () =>
  c.evaluate(
    `(async()=>{const client=Object.values(document.querySelector('#app').__vue_app__._context.provides).find(x=>typeof x?.getQueryCache==='function');await client.refetchQueries({queryKey:['upload-processing']});})()`,
  );
try {
  await c.viewport(1920, 935);
  await c.navigate(base + "/studio/upload");
  await c.wait(
    `document.querySelector('.page-state h1')?.textContent==='Войдите, чтобы добавить видео'`,
  );
  check(
    !(await c.evaluate(`!!document.querySelector('dialog')`)),
    "Guest cannot start uploading",
  );
  await c.click(".upload-page .button");
  await c.wait(`location.pathname==='/login'`);
  const email = token + "@example.com";
  check(
    (await write("/api/v1/auth/register", {
      name: "Автор Загрузки",
      tag: "upload" + token,
      email,
      password: "abcdef",
      password_confirmation: "abcdef",
    })) === 201,
    "Isolated account registered",
  );
  await c.fill("#login-email", email);
  await c.fill("#login-password", "abcdef");
  await c.click(".auth-card button[type=submit]");
  await c.wait(`document.querySelector('.upload-dialog')?.open===true`);
  check(
    await c.evaluate(
      `document.querySelector('.upload-dialog').getBoundingClientRect().width===960 && !document.querySelector('input[type=file]').accept`,
    ),
    "Reference upload modal and no extension whitelist",
  );
  await c.screenshot(`upload-choose-${firefox ? "firefox" : "chromium"}`);
  await c.evaluate(`document.querySelector('.upload-drop button').focus()`);
  await key("Tab", "\uE004", 9);
  check(
    await c.evaluate(`document.activeElement.closest('dialog')?.open===true`),
    "Upload modal keeps keyboard focus",
  );
  await key("Escape", "\uE00C", 27);
  await c.wait(
    `location.pathname==='/studio/content/videos' && document.activeElement.classList.contains('studio-create')`,
  );
  check(true, "Escape closes upload and restores Create focus");
  await c.click(".studio-create");
  await c.click("#studio-create-menu button");
  await c.wait(`document.querySelector('.playlist-dialog')?.open`);
  await key("Escape", "\uE00C", 27);
  await c.wait(`!document.querySelector('.playlist-dialog')?.open`);
  check(true, "Playlist creation remains available beside upload");
  await c.click(".studio-create");
  await c.click("#studio-create-menu a");
  await c.wait(`document.querySelector('.upload-dialog')?.open===true`);
  check(true, "Create menu opens actual upload route");
  await c.evaluate(
    `(()=>{const files=new DataTransfer();files.items.add(new File(['x'],'zero.bin'));Object.defineProperty(files.files[0],'size',{value:5000000001});const input=document.querySelector('#video-file');input.files=files.files;input.dispatchEvent(new Event('change',{bubbles:true}));})()`,
  );
  check(
    await c.evaluate(
      `document.querySelector('.upload-drop .form-error')?.textContent.includes('5 ГБ')`,
    ),
    "Oversize metadata rejected before draft",
  );
  await selectFile();
  check(
    await c.evaluate(
      `document.querySelector('#video-title').value==='Исходник'`,
    ),
    "Filename initializes title without extension restriction",
  );
  await c.fill("#video-title", " ".repeat(3));
  check(
    await c.evaluate(
      `document.querySelector('.upload-dialog__buttons button[type=submit]').disabled`,
    ),
    "Whitespace title rejected",
  );
  await c.fill("#video-title", "😀".repeat(101));
  await c.evaluate(`document.querySelector('.upload-form').requestSubmit()`);
  check(
    await c.evaluate(
      `document.querySelector('#video-title').value.length===202 && document.activeElement.id==='video-title'`,
    ),
    "Overlong Unicode retained and focused",
  );
  await c.fill("#video-title", "😀".repeat(100));
  await c.fill("#video-description", "я".repeat(5001));
  await c.evaluate(`document.querySelector('.upload-form').requestSubmit()`);
  check(
    await c.evaluate(
      `document.querySelector('#video-description').value.length===5001 && document.activeElement.id==='video-description'`,
    ),
    "Overlong description retained",
  );
  check(count("create") === 0, "Invalid metadata never creates draft");
  await c.fill("#video-description", "я".repeat(5000));
  await c.click(".upload-dialog__buttons button[type=submit]");
  await c.wait(`!!document.querySelector('#video-private')`);
  check(
    await c.evaluate(
      `!document.querySelector('#video-private').checked && !document.querySelector('#video-public').checked && document.querySelectorAll('[name=visibility]').length===2`,
    ),
    "Explicit public/private choice; no unlisted",
  );
  await c.click(".upload-dialog__buttons button[type=submit]");
  await c.wait(`!!document.querySelector('#video-visibility-error')`);
  check(
    await c.evaluate(`document.activeElement.id==='video-private'`),
    "Missing access focuses choice",
  );
  await c.click("#video-private");
  fail = "create";
  await c.click(".upload-dialog__buttons button[type=submit]");
  await c.wait(`!!document.querySelector('.upload-error')`);
  check(
    await c.evaluate(
      `!new URLSearchParams(location.search).has('video') && document.querySelector('#video-private').checked`,
    ),
    "Draft failure preserves inputs and does not claim saved",
  );
  fail = "part";
  const before = count("create");
  await c.evaluate(
    `(()=>{const f=document.querySelector('.upload-form');f.requestSubmit();f.requestSubmit();})()`,
  );
  await c.wait(
    `!!document.querySelector('.upload-error') && new URLSearchParams(location.search).has('video')`,
  );
  check(count("create") === before + 1, "Double submit creates one draft");
  const id = await c.evaluate(
    `new URLSearchParams(location.search).get('video')`,
  );
  check(BigInt(id) > 9007199254740991n, "Exact large video identifier in URL");
  check(
    !(await c.evaluate(
      `!!document.querySelector('.upload-watch-link') || !!document.querySelector('.upload-dialog__saved')`,
    )),
    "Part failure does not claim completion or viewing",
  );
  fail = "complete";
  slow = true;
  await c.click(".upload-dialog__buttons button[type=submit]");
  await c.wait(
    `document.querySelector('.upload-progress').textContent.includes('Подтверждаем загрузку')`,
  );
  check(
    count("publish") === 0 &&
      !(await c.evaluate(`!!document.querySelector('.upload-watch-link')`)),
    "100 percent transfer is not accepted or published",
  );
  await c.wait(
    `!!document.querySelector('.upload-error') && document.querySelector('.upload-form').getAttribute('aria-busy')==='false'`,
  );
  const partsBefore = count("part");
  fail = "publish";
  slow = false;
  await c.click(".upload-dialog__buttons button[type=submit]");
  await c.wait(
    `!!document.querySelector('.upload-error') && document.querySelector('.upload-form').getAttribute('aria-busy')==='false' && document.querySelector('.upload-progress').textContent.includes('Загрузка видео завершена')`,
  );
  check(
    count("part") === partsBefore &&
      !(await c.evaluate(`!!document.querySelector('.upload-dialog__saved')`)),
    "Complete retry reuses acknowledged parts; failed publication is not saved",
  );
  fail = "";
  await c.click(".upload-dialog__buttons button[type=submit]");
  await c.wait(`!!document.querySelector('.upload-dialog__saved')`);
  await c.wait(`!!document.querySelector('.upload-watch-link')`, 120000);
  check(
    await c.evaluate(
      `document.querySelector('.upload-watch-link').getAttribute('href')==='/watch?v=${id}' && document.querySelector('.upload-result').textContent.includes('доступно для просмотра')`,
    ),
    "Real downloaded silent media is processed and private video becomes viewable",
  );
  check(
    count("create") === before + 1 && count("part") === partsBefore,
    "Retries preserve one draft and one source",
  );
  check(
    calls
      .filter((call) => ["create", "complete", "publish"].includes(call.kind))
      .every((call) => call.csrf),
    "Control writes use CSRF",
  );
  const added = await c.evaluate(
    `(async()=>{const r=await fetch('/api/v1/videos/${id}/settings');return (await r.json()).added_at})()`,
  );
  await c.reload();
  await c.wait(`!!document.querySelector('.upload-dialog__saved')`);
  check(
    await c.evaluate(
      `document.querySelector('#video-title').value===${JSON.stringify("😀".repeat(100))} && document.querySelector('#video-description').value.length===5000`,
    ),
    "Reload restores server metadata and publication",
  );
  check(
    added ===
      (await c.evaluate(
        `(async()=>{const r=await fetch('/api/v1/videos/${id}/settings');return (await r.json()).added_at})()`,
      )),
    "Reload and repeats preserve addition date",
  );
  processingState = "partial";
  await refetchProcessing();
  check(
    await c.evaluate(
      `document.querySelector('.upload-processing').textContent.includes('Часть вариантов') && !!document.querySelector('.upload-watch-link')`,
    ),
    "Partial response retains available quality with failed rung",
  );
  processingState = "failed";
  await refetchProcessing();
  check(
    await c.evaluate(
      `!document.querySelector('.upload-watch-link') && document.querySelector('.upload-processing').textContent.includes('Просмотр недоступен')`,
    ),
    "Full processing failure never exposes viewing",
  );
  processingState = "";
  for (const [width, height] of [
    [390, 844],
    [320, 568],
  ]) {
    await c.viewport(width, height, true);
    await c.navigate(base + "/studio/upload");
    await c.wait(`document.querySelector('.upload-dialog')?.open===true`);
    await selectFile();
    check(
      await c.evaluate(
        `document.documentElement.scrollWidth<=innerWidth && document.querySelector('.upload-dialog').getBoundingClientRect().width<=innerWidth-24`,
      ),
      "Phone modal fits without horizontal page overflow",
    );
    await c.click(".upload-dialog__buttons button[type=submit]");
    await c.wait(`!!document.querySelector('#video-private')`);
    check(
      await c.evaluate(
        `document.querySelector('.upload-dialog__buttons button[type=submit]').getBoundingClientRect().bottom<=innerHeight`,
      ),
      "Phone action stays reachable while body scrolls",
    );
    await c.screenshot(
      `upload-access-${firefox ? "firefox" : "chromium"}-${width}`,
    );
  }
  await c.viewport(1920, 935);
  await c.click("#video-private");
  fail = "part";
  await c.click(".upload-dialog__buttons button[type=submit]");
  await c.wait(`!!document.querySelector('.upload-error')`);
  const interrupted = count("part");
  await c.reload();
  await c.wait(
    `document.querySelector('.upload-dialog .page-state h1')?.textContent==='Передача файла не завершена'`,
  );
  check(
    count("part") === interrupted &&
      !(await c.evaluate(`!!document.querySelector('#video-file')`)),
    "Reload cannot mix a different source with acknowledged parts",
  );
  await c.click(".upload-dialog .page-state a");
  await c.wait(`!!document.querySelector('.upload-drop')`);
  check(
    !(await c.evaluate(`new URLSearchParams(location.search).has('video')`)),
    "Interrupted reload can begin a separate upload",
  );
  fail = "";
  await selectFile();
  await c.fill("#video-title", "<img src=x onerror=window.uploadXss=1>");
  await c.click(".upload-dialog__buttons button[type=submit]");
  await c.wait(`!!document.querySelector('#video-public')`);
  await c.click("#video-public");
  await c.click(".upload-dialog__buttons button[type=submit]");
  await c.wait(`!!document.querySelector('.upload-watch-link')`, 120000);
  const publicId = await c.evaluate(
    `new URLSearchParams(location.search).get('video')`,
  );
  check(
    await c.evaluate(
      `document.querySelector('#upload-dialog-title').textContent==='<img src=x onerror=window.uploadXss=1>' && !document.querySelector('#upload-dialog-title img') && !window.uploadXss`,
    ),
    "Public metadata stays text after real publication",
  );
  await c.navigate(base + "/studio/upload");
  await c.wait(`!!document.querySelector('.upload-drop')`);
  await selectFile();
  await c.click(".upload-dialog__buttons button[type=submit]");
  await c.wait(`!!document.querySelector('#video-private')`);
  await c.click("#video-private");
  slow = true;
  await c.click(".upload-dialog__buttons button[type=submit]");
  await c.wait(
    `document.querySelector('.upload-progress').textContent.includes('Передача файла')`,
  );
  const beforeLeave = count("complete");
  await key("Escape", "\uE00C", 27);
  await c.wait(`location.pathname==='/studio/content/videos'`);
  await pause(900);
  slow = false;
  check(
    count("complete") === beforeLeave &&
      !(await c.evaluate(`!!document.querySelector('.upload-dialog')`)),
    "Leaving aborts transfer and prevents late completion or redirect",
  );
  await c.navigate(base + "/studio/upload?video=" + id);
  await c.wait(`!!document.querySelector('#video-title')`);
  check(
    (await write("/api/v1/auth/logout", {})) === 204,
    "Real owner session ended",
  );
  await refetchProcessing();
  await c.wait(`!document.querySelector('.upload-dialog')`);
  check(
    !(await c.evaluate(
      `document.body.textContent.includes(${JSON.stringify("😀".repeat(100))})`,
    )),
    "401 clears file, private metadata and dialog",
  );
  await c.navigate(base + "/studio/upload?video=" + id);
  check(
    !(await c.evaluate(`!!document.querySelector('.upload-dialog')`)),
    "Guest cannot restore private upload from URL",
  );
  check(
    await c.evaluate(
      `(async()=>{const pub=await fetch('/api/v1/videos/${publicId}');const priv=await fetch('/api/v1/videos/${id}');return pub.status===200 && priv.status===404;})()`,
    ),
    "Public is viewable by guest; private remains protected",
  );
  check(c.errors.length === 0, "No uncaught runtime errors");
  console.log(
    JSON.stringify(
      {
        status: "pass",
        assertions,
        engine: firefox ? "Firefox" : "Chromium",
        backend: "real isolated API/PostgreSQL/Nginx/RustFS/media-worker",
        processingFailureStates: "injected HTTP DTOs",
        screens: ["1920x935", "390x844", "320x568"],
      },
      null,
      2,
    ),
  );
} catch (error) {
  await c.screenshot(`upload-failure-${firefox ? "firefox" : "chromium"}`);
  console.error({ runtimeErrors: c.errors });
  throw error;
} finally {
  await c.close();
  for (const socket of sockets) socket.destroy();
  server.closeAllConnections();
  server.close();
}
