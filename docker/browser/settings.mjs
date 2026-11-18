import assert from "node:assert/strict";
import { createServer, request as httpRequest } from "node:http";
import { readFile } from "node:fs/promises";
import { randomUUID } from "node:crypto";
import { browser, pause } from "./cdp.mjs";
import { firefoxBrowser } from "./bidi.mjs";

const firefox = process.argv.includes("--firefox");
let fixture;
for (let attempt = 0; attempt < 900; attempt++) {
  try {
    fixture = JSON.parse(await readFile("/work/watch-fixture.json", "utf8"));
    if (fixture.people) break;
  } catch {}
  await pause(250);
}
if (!fixture?.people) throw new Error("Settings fixture unavailable");
const source = await readFile("/media/portrait.mp4");
const image = await readFile("/media/thumbnail.png");
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
    if (req.url === "/fixture-image") {
      res
        .writeHead(200, { "content-type": "application/octet-stream" })
        .end(image);
      return;
    }
    const api = req.url.startsWith("/api/"),
      storage = req.url.startsWith("/uploads/");
    // Never retain bearer URLs or object keys in test logs/results.
    const kind = req.url.startsWith("/api/v1/videos/mine")
      ? "mine"
      : req.url.endsWith("/settings")
        ? "settings"
        : req.url.endsWith("/permissions")
          ? "permissions"
          : req.url.endsWith("/playlists") && req.method === "PUT"
            ? "playlists"
            : req.url.endsWith("/thumbnail") && req.method === "PUT"
              ? "thumbnail"
              : storage
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
        path: api ? req.url : "storage-part",
        csrf: !!req.headers["x-csrftoken"],
      });
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    if (slow && (kind === "part" || kind === "complete")) await pause(650);
    if (
      fail === kind &&
      kind !== "other" &&
      (kind === "mine" || req.method !== "GET")
    ) {
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
    if (
      slow &&
      ["permissions", "playlists", "thumbnail", "settings", "mine"].includes(
        kind,
      ) &&
      req.method === "PUT"
    )
      await pause(650);
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
const request = (url, method, body) =>
  c.evaluate(
    `(async()=>{const csrf=decodeURIComponent(document.cookie.split('; ').find(x=>x.startsWith('csrftoken=')).slice(10));const r=await fetch(${JSON.stringify(url)},{method:${JSON.stringify(method)},headers:{'content-type':'application/json','x-csrftoken':csrf},body:${body === undefined ? "undefined" : JSON.stringify(JSON.stringify(body))}});return JSON.stringify({status:r.status,text:await r.text()})})()`,
  );
const read = async (url) => JSON.parse(await request(url, "GET"));
const put = async (url, body) => JSON.parse(await request(url, "PUT", body));
async function selectImage() {
  await c.evaluate(
    `(async()=>{const bytes=await (await fetch('/fixture-image')).arrayBuffer();const files=new DataTransfer();files.items.add(new File([bytes],'Превью.unusual',{type:'application/octet-stream'}));const input=document.querySelector('#thumbnail-file');input.files=files.files;input.dispatchEvent(new Event('change',{bubbles:true}));})()`,
  );
}
const settingsPath = (id) => `/studio/video/${id}/edit`;
const saves = () =>
  calls.filter((x) => x.kind === "settings" && x.method === "PUT").length;
async function login(role, target = "/studio/content/videos") {
  await c.navigate(base + "/login?returnTo=" + encodeURIComponent(target));
  await c.fill("#login-email", fixture.people[role].email);
  await c.fill("#login-password", "abcdef");
  await c.click(".auth-card button[type=submit]");
  await c.wait(
    `location.pathname===${JSON.stringify(target.split("?")[0])} && !document.querySelector('.auth-card')`,
  );
}
async function all(publication) {
  let result = [],
    cursor;
  do {
    const page = JSON.parse(
      (
        await read(
          `/api/v1/videos/mine?publication=${publication}${cursor ? "&cursor=" + encodeURIComponent(cursor) : ""}`,
        )
      ).text,
    );
    result.push(...page.items);
    cursor = page.next_cursor;
  } while (cursor);
  return result;
}
async function sourceVideo(title) {
  return c.evaluate(
    `(async()=>{const csrf=decodeURIComponent(document.cookie.split('; ').find(x=>x.startsWith('csrftoken=')).slice(10));const headers={'content-type':'application/json','x-csrftoken':csrf};const draft=await fetch('/api/v1/videos',{method:'POST',headers,body:JSON.stringify({title:${JSON.stringify(title)},description:'Описание',visibility:'private'})});if(draft.status!==201)throw new Error('Seed draft failed');const id=(await draft.text()).match(/"id":(\\d+)/)[1];const bytes=await (await fetch('/fixture-source')).arrayBuffer();const plan=await (await fetch('/api/v1/videos/'+id+'/upload',{method:'POST',headers,body:JSON.stringify({size:bytes.byteLength})})).json();for(const part of plan.parts){const from=(part.number-1)*plan.part_size;const r=await fetch(part.url,{method:'PUT',credentials:'omit',body:bytes.slice(from,from+part.size)});if(!r.ok)throw new Error('Seed PUT failed');}const done=await fetch('/api/v1/videos/'+id+'/upload/'+plan.id+'/complete',{method:'POST',headers});if(!done.ok)throw new Error('Seed complete failed');const pub=await fetch('/api/v1/videos/'+id+'/publish',{method:'POST',headers});if(!pub.ok)throw new Error('Seed publish failed');return id;})()`,
  );
}
async function loadAll() {
  await c.wait(
    `document.querySelectorAll('.own-video-table tbody tr').length>=20`,
  );
  for (let page = 0; page < 4; page++) {
    if (
      await c.evaluate(
        `document.querySelectorAll('.own-video-table tbody tr').length===41`,
      )
    )
      break;
    await c.evaluate(
      `window.scrollTo(0,document.body.scrollHeight);document.querySelector('.studio-videos__more button')?.click()`,
    );
    await pause(250);
  }
  await c.wait(
    `document.querySelectorAll('.own-video-table tbody tr').length===41`,
  );
}
async function openSettings(id) {
  await c.navigate(base + settingsPath(id));
  await c.wait(`!!document.querySelector('#settings-title')`);
}
try {
  await c.viewport(1920, 935);
  await c.navigate(base + "/studio");
  await c.wait(
    `location.pathname==='/studio/content/videos' && !!document.querySelector('.studio-videos .page-state a')`,
  );
  check(
    !calls.some((x) => x.kind === "mine"),
    "Guest does not request own lists",
  );
  await login("owner");
  await c.wait(`!!document.querySelector('.own-video-table')`);
  let published = await all("published"),
    drafts = await all("draft");
  for (let i = published.length; i < 41; i++)
    await sourceVideo("Свой опубликованный " + i);
  for (let i = drafts.length; i < 41; i++)
    assert.equal(
      JSON.parse(
        await request("/api/v1/videos", "POST", {
          title: "Черновик " + i,
          description: "Описание",
          visibility: "private",
        }),
      ).status,
      201,
    );
  await c.reload();
  await loadAll();
  check(
    await c.evaluate(
      `new Set([...document.querySelectorAll('.own-video-table tbody tr')].map(x=>x.dataset.videoId)).size===41`,
    ),
    "Published cursor 20/20/1 without duplicates",
  );
  check(
    calls.some((x) => x.kind === "mine" && x.path.includes("cursor=")),
    "Own list uses server cursor",
  );
  const hidden = fixture.videos.hidden,
    first = fixture.videos.public,
    privateId = fixture.videos.private;
  check(
    await c.evaluate(
      `document.querySelector('[data-video-id="${hidden}"]').textContent.includes('Скрыто') && ![...document.querySelectorAll('[data-video-id="${hidden}"] .own-video-actions a')].some(x=>x.textContent==='Смотреть')`,
    ),
    "Own hidden video is listed without watch action",
  );
  const beforeOrder = await c.evaluate(
    `JSON.stringify([...document.querySelectorAll('.own-video-table tbody tr')].map(x=>x.dataset.videoId))`,
  );
  await c.screenshot(`settings-list-${firefox ? "firefox" : "chromium"}`);
  await c.click(".publication-tabs a:nth-child(2)");
  await loadAll();
  check(
    await c.evaluate(
      `new URLSearchParams(location.search).get('publication')==='draft' && !document.querySelector('[data-video-id="${first}"]')`,
    ),
    "Drafts have independent URL and rows",
  );
  check(
    calls.some(
      (x) => x.kind === "mine" && x.path.includes("publication=draft"),
    ),
    "Draft endpoint parameter applied",
  );
  await c.reload();
  await c.wait(
    `document.querySelectorAll('.own-video-table tbody tr').length>=20`,
  );
  check(
    await c.evaluate(
      `new URLSearchParams(location.search).get('publication')==='draft'`,
    ),
    "Reload retains draft section",
  );
  const draftId = await c.evaluate(
    `document.querySelector('.own-video-table tbody tr').dataset.videoId`,
  );
  await openSettings(draftId);
  const publishesBefore = calls.filter((x) => x.kind === "publish").length;
  await c.fill("#settings-title", "Черновик исправлен " + token);
  await c.click(".settings-save");
  await c.wait(
    `document.querySelector('.settings-notice')?.textContent==='Изменения сохранены'`,
  );
  check(
    JSON.parse((await read(`/api/v1/videos/${draftId}/settings`)).text)
      .publication === "draft" &&
      calls.filter((x) => x.kind === "publish").length === publishesBefore,
    "Editing draft does not publish it",
  );
  const confirmed = JSON.parse(
    (await read(`/api/v1/videos/${privateId}/settings`)).text,
  );
  await openSettings(privateId);
  const noOp = saves();
  check(
    await c.evaluate(`document.querySelector('.settings-save').disabled`),
    "Unchanged settings cannot submit",
  );
  await c.fill("#settings-title", " " + confirmed.title + " ");
  await c.evaluate(
    `document.querySelector('.video-settings__form').requestSubmit()`,
  );
  check(saves() === noOp, "Trim-equivalent edit sends no mutation");
  await c.fill("#settings-title", "😀".repeat(101));
  await c.click(".settings-save");
  check(
    await c.evaluate(
      `document.querySelector('#settings-title').value.length===202 && document.activeElement.id==='settings-title'`,
    ),
    "Overlong Unicode title retained and focused",
  );
  await c.fill("#settings-title", "😀".repeat(100));
  await c.fill("#settings-description", "я".repeat(5001));
  await c.click(".settings-save");
  check(
    await c.evaluate(
      `document.querySelector('#settings-description').value.length===5001 && document.activeElement.id==='settings-description'`,
    ),
    "Overlong description retained and focused",
  );
  check(saves() === noOp, "Invalid fields do not reach API");
  await c.fill("#settings-description", "я".repeat(5000));
  fail = "settings";
  await c.click(".settings-save");
  await c.wait(
    `!!document.querySelector('.video-settings__form > .form-error')`,
  );
  check(
    JSON.parse((await read(`/api/v1/videos/${privateId}/settings`)).text)
      .title === confirmed.title &&
      (await c.evaluate(
        `document.querySelector('#settings-title').value===${JSON.stringify("😀".repeat(100))} && !document.querySelector('.settings-notice')`,
      )),
    "Save failure preserves draft without false server/cache success",
  );
  fail = "";
  slow = true;
  const single = saves();
  await c.evaluate(
    `(()=>{const f=document.querySelector('.video-settings__form');f.requestSubmit();f.requestSubmit()})()`,
  );
  await c.wait(
    `document.querySelector('.video-settings__form').getAttribute('aria-busy')==='true'`,
  );
  check(
    await c.evaluate(
      `document.querySelector('#settings-title').disabled && document.querySelector('.settings-save').disabled`,
    ),
    "Pending disables duplicate editing",
  );
  await c.wait(
    `document.querySelector('.settings-notice')?.textContent==='Изменения сохранены'`,
  );
  slow = false;
  check(saves() === single + 1, "Double submit sends one settings write");
  let saved = JSON.parse(
    (await read(`/api/v1/videos/${privateId}/settings`)).text,
  );
  check(
    saved.title === "😀".repeat(100) &&
      saved.description.length === 5000 &&
      saved.added_at === confirmed.added_at &&
      saved.publication === confirmed.publication &&
      saved.moderation === confirmed.moderation,
    "Unicode boundaries saved without changing source date/publication/moderation",
  );
  await c.fill("#settings-title", "Несохранённая правка");
  await c.click(".video-settings__actions .button--secondary");
  check(
    await c.evaluate(
      `document.querySelector('#settings-title').value===${JSON.stringify("😀".repeat(100))} && document.querySelector('.settings-save').disabled`,
    ),
    "Cancel restores last server-confirmed metadata",
  );
  await c.navigate(base + "/studio/content/videos");
  await loadAll();
  check(
    (await c.evaluate(
      `JSON.stringify([...document.querySelectorAll('.own-video-table tbody tr')].map(x=>x.dataset.videoId))`,
    )) === beforeOrder,
    "Editing preserves list order by creation",
  );
  const exactLink = await c.evaluate(
    `document.querySelector('[data-video-id="${privateId}"] .own-video-title').getAttribute('href')`,
  );
  check(
    exactLink === settingsPath(privateId) &&
      BigInt(privateId) > 9007199254740991n,
    "List edit link preserves int64",
  );
  await openSettings(hidden);
  await c.fill("#settings-title", "Скрытое видео " + token);
  await c.click(".settings-save");
  await c.wait(`!!document.querySelector('.settings-notice')`);
  check(
    !(await c.evaluate(`!!document.querySelector('.settings-watch')`)) &&
      JSON.parse((await read(`/api/v1/videos/${hidden}/settings`)).text)
        .moderation === "hidden",
    "Metadata edit cannot lift hidden state",
  );
  await openSettings(first);
  const publicBefore = JSON.parse(
    (await read(`/api/v1/videos/${first}/settings`)).text,
  );
  const visibility =
    publicBefore.visibility === "public" ? "private" : "public";
  await c.evaluate(
    `(()=>{const select=document.querySelector('#settings-visibility');select.value=${JSON.stringify(visibility)};select.dispatchEvent(new Event('change',{bubbles:true}));})()`,
  );
  await c.fill("#settings-title", "<img src=x onerror=window.settingsXss=1>");
  await c.click(".settings-save");
  await c.wait(`!!document.querySelector('.settings-notice')`);
  await c.reload();
  await c.wait(`!!document.querySelector('#settings-title')`);
  check(
    await c.evaluate(
      `document.querySelector('#settings-title').value==='<img src=x onerror=window.settingsXss=1>' && !window.settingsXss && document.querySelector('#settings-visibility').value===${JSON.stringify(visibility)}`,
    ),
    "Metadata/visibility restored as safe text",
  );
  await c.click(".playlist-choose");
  await c.wait(`document.querySelector('dialog[aria-label="Плейлисты"]').open`);
  await key("Escape", "\uE00C", 27);
  check(
    await c.evaluate(
      `!document.querySelector('dialog[aria-label="Плейлисты"]').open`,
    ),
    "Reusable playlist selection opens in settings",
  );
  if (visibility === "private") {
    await c.click(".video-permissions > button");
    await c.wait(`document.querySelector('dialog[aria-label^="Кому"]').open`);
    await key("Escape", "\uE00C", 27);
    check(true, "Reusable email modal opens in settings");
  }
  const invalidBefore = calls.filter(
    (x) => x.kind === "settings" && x.method === "GET",
  ).length;
  for (const id of [
    "0",
    "-1",
    "01",
    "9223372036854775808",
    "javascript:alert(1)",
  ]) {
    await c.navigate(base + settingsPath(id));
    await c.wait(
      `document.querySelector('.video-settings .page-state h1')?.textContent==='Видео недоступно'`,
    );
  }
  check(
    calls.filter((x) => x.kind === "settings" && x.method === "GET").length ===
      invalidBefore,
    "Invalid IDs rejected before API",
  );
  fail = "mine";
  await c.navigate(base + "/studio/content/videos");
  await c.wait(
    `document.querySelector('.studio-videos .page-state h1')?.textContent==='Не удалось загрузить видео'`,
  );
  check(
    !(await c.evaluate(`!!document.querySelector('.own-video-table')`)),
    "List error has no fabricated rows",
  );
  fail = "";
  await c.click(".studio-videos .page-state button");
  await c.wait(`!!document.querySelector('.own-video-table')`);
  check(true, "List retry recovers");
  await c.navigate(base + "/");
  await c.wait(`!!document.querySelector('.masthead .studio-create')`);
  check(
    await c.evaluate(
      `document.querySelector('.masthead__end .create-playlist').nextElementSibling.matches('.avatar-button')`,
    ),
    "Create is next to avatar in public header",
  );
  await c.click(".masthead .studio-create");
  await c.click("#studio-create-menu button");
  await c.wait(
    `location.pathname==='/studio/content/playlists' && document.querySelector('.playlist-dialog')?.open`,
  );
  check(
    await c.evaluate(`!new URLSearchParams(location.search).has('create')`),
    "Public create playlist opens actual Studio modal",
  );
  await key("Escape", "\uE00C", 27);
  await c.reload();
  await c.wait(`!!document.querySelector('.studio-playlists')`);
  check(
    !(await c.evaluate(`document.querySelector('.playlist-dialog').open`)),
    "Consumed creation intent does not reopen on reload",
  );
  await c.click(".studio-header .studio-create");
  await c.click("#studio-create-menu a");
  await c.wait(`document.querySelector('.upload-dialog')?.open===true`);
  check(
    await c.evaluate(
      `!!document.querySelector('.studio-header .studio-create')`,
    ),
    "Create also present in upload header",
  );
  await key("Escape", "\uE00C", 27);
  await c.wait(
    `location.pathname==='/studio/content/videos' && document.activeElement.classList.contains('studio-create')`,
  );
  for (const [width, height] of [
    [390, 844],
    [320, 568],
  ]) {
    await c.viewport(width, height, true);
    await c.navigate(base + "/");
    await c.wait(`!!document.querySelector('.masthead .studio-create')`);
    check(
      await c.evaluate(
        `document.documentElement.scrollWidth<=innerWidth && document.querySelector('.masthead .studio-create').getBoundingClientRect().width===40`,
      ),
      "Phone header create fits beside search/avatar",
    );
    await c.click(".masthead .studio-create");
    await key("Escape", "\uE00C", 27);
    check(
      await c.evaluate(
        `document.activeElement.classList.contains('studio-create')`,
      ),
      "Menu Escape restores create focus",
    );
    await openSettings(first);
    check(
      await c.evaluate(
        `document.documentElement.scrollWidth<=innerWidth && document.querySelector('.settings-save').getBoundingClientRect().right<=innerWidth`,
      ),
      "Phone settings and save action fit",
    );
    await c.screenshot(
      `settings-phone-${firefox ? "firefox" : "chromium"}-${width}`,
    );
    await c.navigate(base + "/studio/content/videos");
    await c.wait(`!!document.querySelector('.own-video-table')`);
    check(
      await c.evaluate(`document.documentElement.scrollWidth<=innerWidth`),
      "Phone table scroll stays inside container",
    );
  }
  await c.viewport(1920, 935);
  await openSettings(privateId);
  slow = true;
  await c.fill("#settings-title", "Позднее сохранение " + token);
  await c.click(".settings-save");
  await c.wait(
    `document.querySelector('.video-settings__form').getAttribute('aria-busy')==='true'`,
  );
  await c.evaluate(
    `document.querySelector('.studio-navigation__exit').click()`,
  );
  await c.wait(`location.pathname==='/'`);
  await pause(900);
  slow = false;
  check(
    !(await c.evaluate(
      `!!document.querySelector('.settings-notice') || !!document.querySelector('#settings-title')`,
    )),
    "Late save cannot restore old settings after leaving",
  );
  await c.click(".avatar-button");
  await c.click("#logout-button");
  await c.wait(`!!document.querySelector('.masthead .sign-in')`);
  check(
    !(await c.evaluate(`!!document.querySelector('.masthead .studio-create')`)),
    "Logout removes creator action",
  );
  await login("invited", settingsPath(privateId));
  await c.wait(
    `document.querySelector('.video-settings .page-state h1')?.textContent==='Видео недоступно'`,
  );
  check(
    !(await c.evaluate(
      `!!document.querySelector('#settings-title') || document.body.textContent.includes('Позднее сохранение')`,
    )),
    "Invited viewer cannot read owner settings",
  );
  await c.navigate(base + "/studio/content/videos");
  await c.wait(
    `document.querySelector('.studio-videos .page-state h1')?.textContent==='Пока нет опубликованных видео'`,
  );
  check(
    !(await c.evaluate(`!!document.querySelector('.own-video-table')`)),
    "Different account does not inherit owner rows",
  );
  await login("owner", settingsPath(privateId));
  await c.wait(`!!document.querySelector('#settings-title')`);
  assert.equal(await write("/api/v1/auth/logout", {}), 204);
  await c.evaluate(
    `(async()=>{const client=Object.values(document.querySelector('#app').__vue_app__._context.provides).find(x=>typeof x?.getQueryCache==='function');await client.refetchQueries({queryKey:['video-settings']});})()`,
  );
  await c.wait(`!document.querySelector('#settings-title')`);
  check(
    await c.evaluate(
      `(()=>{const client=Object.values(document.querySelector('#app').__vue_app__._context.provides).find(x=>typeof x?.getQueryCache==='function');return !JSON.stringify(client.getQueryCache().getAll().map(q=>q.state.data)).includes('Позднее сохранение');})()`,
    ),
    "Expired session clears private settings cache",
  );
  check(
    calls
      .filter((x) => x.kind === "settings" && x.method === "PUT")
      .every((x) => x.csrf),
    "Settings writes include CSRF",
  );
  check(c.errors.length === 0, "No uncaught runtime errors");
  console.log(
    JSON.stringify(
      {
        status: "pass",
        assertions,
        engine: firefox ? "Firefox" : "Chromium",
        backend: "real isolated API/PostgreSQL/Nginx/RustFS/media-worker",
        screens: ["1920x935", "390x844", "320x568"],
      },
      null,
      2,
    ),
  );
} catch (error) {
  await c.screenshot(`settings-failure-${firefox ? "firefox" : "chromium"}`);
  console.error({
    runtimeErrors: c.errors,
    queries: await c.evaluate(
      `JSON.stringify(Object.values(document.querySelector('#app').__vue_app__._context.provides).find(x=>typeof x?.getQueryCache==='function').getQueryCache().getAll().filter(q=>q.state.error).map(q=>({key:q.queryKey,error:q.state.error?.message})))`,
    ),
  });
  throw error;
} finally {
  await c.close();
  for (const socket of sockets) socket.destroy();
  server.closeAllConnections();
  server.close();
}
