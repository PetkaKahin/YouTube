import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { browser, pause } from "./cdp.mjs";

let fixture;
for (let attempt = 0; attempt < 600; attempt++) {
  try {
    fixture = JSON.parse(await readFile("/work/watch-fixture.json", "utf8"));
    break;
  } catch {
    await pause(250);
  }
}
if (!fixture) throw new Error("Watch fixture did not become ready");
let offline = false;
let delayMedia = false;
let segmentFailure = false;
let mediaRequests = 0;
const server = createServer(async (request, response) => {
  try {
    const api = request.url.startsWith("/api/");
    const media = api && request.url.includes("/hls/");
    if (media) mediaRequests++;
    if (media && delayMedia) await pause(1200);
    if (
      (api && offline) ||
      (media && segmentFailure && request.url.endsWith(".m4s"))
    ) {
      response
        .writeHead(503, { "content-type": "application/problem+json" })
        .end('{"code":"media_unavailable"}');
      return;
    }
    const headers = new Headers();
    for (const name of [
      "content-type",
      "cookie",
      "x-csrftoken",
      "accept",
      "range",
    ])
      if (request.headers[name]) headers.set(name, request.headers[name]);
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    const upstream = await fetch(
      api
        ? `http://youtube-frontend-watch-gateway:8080${request.url}`
        : `http://node:5173${request.url}`,
      {
        method: request.method,
        headers,
        body: chunks.length ? Buffer.concat(chunks) : undefined,
        redirect: "manual",
      },
    );
    response.statusCode = upstream.status;
    for (const name of [
      "content-type",
      "cache-control",
      "location",
      "content-range",
      "accept-ranges",
    ])
      if (upstream.headers.has(name))
        response.setHeader(name, upstream.headers.get(name));
    if (upstream.headers.getSetCookie().length)
      response.setHeader("set-cookie", upstream.headers.getSetCookie());
    response.end(Buffer.from(await upstream.arrayBuffer()));
  } catch {
    response
      .writeHead(502, { "content-type": "application/problem+json" })
      .end('{"code":"test_upstream"}');
  }
});
await new Promise((resolve) => server.listen(4181, "127.0.0.1", resolve));
const c = await browser();
const base = "http://127.0.0.1:4181";
let assertions = 0;
const check = (value, message) => {
  assert.ok(value, message);
  assertions++;
};
const v = fixture.videos;
const watchUrl = (id) => `${base}/watch?v=${id}`;
async function click(selector) {
  await c.wait(`!!document.querySelector(${JSON.stringify(selector)})`);
  await c.evaluate(
    `document.querySelector(${JSON.stringify(selector)}).scrollIntoView({block:'center'})`,
  );
  const p = await c.evaluate(
    `(()=>{const r=document.querySelector(${JSON.stringify(selector)}).getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()`,
  );
  await c.send("Input.dispatchMouseEvent", {
    type: "mousePressed",
    button: "left",
    clickCount: 1,
    ...p,
  });
  await c.send("Input.dispatchMouseEvent", {
    type: "mouseReleased",
    button: "left",
    clickCount: 1,
    ...p,
  });
}
async function openVideo(id) {
  await c.navigate(watchUrl(id));
  await c.wait(
    `!!document.querySelector('.player') || document.querySelector('.page-state h1')?.textContent === 'Видео недоступно'`,
  );
}
async function login(role) {
  await c.navigate(
    `${base}/login?returnTo=${encodeURIComponent(`/watch?v=${v.private}`)}`,
  );
  await c.wait(`!!document.querySelector('#login-email')`);
  await c.fill("#login-email", fixture.people[role].email);
  await c.fill("#login-password", "abcdef");
  await c.click(".auth-card button[type=submit]");
  await c.wait(
    `location.pathname === '/watch' && !document.querySelector('.account-skeleton')`,
  );
}
let ownerCookie;
async function ownerRequest(path, body) {
  if (!ownerCookie) {
    const initial = await fetch(`${base}/api/v1/auth/me`);
    const cookies = initial.headers
      .getSetCookie()
      .map((item) => item.split(";")[0]);
    const csrf = cookies
      .find((item) => item.startsWith("csrftoken="))
      ?.slice(10);
    const result = await fetch(`${base}/api/v1/auth/login`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        cookie: cookies.join("; "),
        "X-CSRFToken": decodeURIComponent(csrf),
      },
      body: JSON.stringify({
        email: fixture.people.owner.email,
        password: "abcdef",
      }),
    });
    check(result.ok, "Independent owner session");
    ownerCookie = [
      ...cookies,
      ...result.headers.getSetCookie().map((item) => item.split(";")[0]),
    ].join("; ");
  }
  const csrf = ownerCookie
    .split("; ")
    .find((item) => item.startsWith("csrftoken="))
    ?.slice(10);
  const result = await fetch(`${base}${path}`, {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
      cookie: ownerCookie,
      "X-CSRFToken": decodeURIComponent(csrf),
    },
    body: JSON.stringify(body),
  });
  assert.ok(result.ok, `Owner API returned ${result.status}`);
}
try {
  await c.viewport(1920, 935);
  await openVideo(v.public);
  await c.wait(`document.querySelector('.player__start')?.disabled === false`);
  check(
    await c.evaluate(
      `document.querySelector('.watch-page h1')?.textContent === 'Открытое видео <script>alert(1)</script>' && !window.frontendInject`,
    ),
    "Metadata is text, exact direct ID works",
  );
  check(
    await c.evaluate(
      `document.querySelector('.watch-page__author')?.getAttribute('href') === '/@${fixture.people.owner.tag}'`,
    ),
    "Author link from new API channel",
  );
  check(
    await c.evaluate(
      `document.querySelector('.watch-page__description p').textContent.includes('<img') && !document.querySelector('.watch-page__description img')`,
    ),
    "Description HTML stays data",
  );
  await click(".watch-page__description button");
  await c.wait(
    `document.querySelector('.watch-page__description button').getAttribute('aria-expanded') === 'true'`,
  );
  check(
    await c.evaluate(
      `document.querySelector('.watch-page__description button').getAttribute('aria-expanded') === 'true'`,
    ),
    "Description expands",
  );
  check(
    await c.evaluate(
      `document.documentElement.scrollWidth <= innerWidth && document.querySelector('.app-shell__content').getBoundingClientRect().x === 0`,
    ),
    "Watch layout without sidebar offset",
  );
  await click(".masthead__menu");
  check(
    await c.evaluate(
      `document.querySelector('#main-navigation').getAttribute('aria-modal') === 'true' && document.querySelector('.app-shell__content').inert`,
    ),
    "Desktop watch drawer opens",
  );
  await c.evaluate(
    `document.querySelector('.navigation__header button').click()`,
  );
  await click(".player__start");
  await c.wait(
    `document.querySelector('video').currentTime > 0.5 && !document.querySelector('video').paused`,
  );
  check(
    await c.evaluate(
      `document.querySelector('video').videoWidth > 0 && document.querySelector('video').duration >= 11`,
    ),
    "Actual AV1/AAC media plays through Nginx/RustFS",
  );
  await c.evaluate(`document.querySelector('.player').focus()`);
  await c.send("Input.dispatchKeyEvent", {
    type: "keyDown",
    key: "k",
    code: "KeyK",
  });
  await c.send("Input.dispatchKeyEvent", {
    type: "keyUp",
    key: "k",
    code: "KeyK",
  });
  check(
    await c.evaluate(`document.querySelector('video').paused`),
    "Keyboard pause",
  );
  await c.evaluate(
    `document.querySelector('.player__seek').value=5;document.querySelector('.player__seek').dispatchEvent(new Event('input',{bubbles:true}))`,
  );
  await c.wait(`document.querySelector('video').currentTime >= 4.9`);
  check(
    await c.evaluate(`document.querySelector('video').currentTime >= 4.9`),
    "Seek position",
  );
  await click('[aria-label="Настройки"]');
  await c.evaluate(
    `[...document.querySelectorAll('.player__menu button')].find(e=>e.textContent.includes('Качество')).click()`,
  );
  check(
    await c.evaluate(
      `document.querySelector('.player__menu').textContent.includes('360p') && document.querySelector('.player__menu').textContent.includes('480p') && !document.querySelector('.player__menu').textContent.includes('720p') && !document.querySelector('.player__menu').textContent.includes('1080p')`,
    ),
    "Only ready quality levels",
  );
  await c.screenshot("player-quality-desktop");
  await c.evaluate(
    `[...document.querySelectorAll('.player__menu button')].find(e=>e.textContent.trim()==='360p').click()`,
  );
  await c.wait(`!document.querySelector('.player__menu')`);
  check(
    await c.evaluate(
      `Math.abs(document.querySelector('video').currentTime-5) < 1 && document.querySelector('video').paused`,
    ),
    "Quality switch preserves position and paused state",
  );
  await click('[aria-label="Настройки"]');
  await c.evaluate(
    `[...document.querySelectorAll('.player__menu button')].find(e=>e.textContent.includes('Субтитры')).click()`,
  );
  check(
    await c.evaluate(
      `document.querySelectorAll('.player__menu button').length >= 3 && !document.querySelector('.player__menu').textContent.includes('Автоматический')`,
    ),
    "Only source subtitle tracks",
  );
  await c.evaluate(
    `[...document.querySelectorAll('.player__menu button')].at(-1).click()`,
  );
  await c.wait(
    `[...document.querySelector('video').textTracks].some(t=>t.mode==='showing')`,
  );
  await c.wait(
    `[...document.querySelector('video').textTracks].some(t=>t.cues?.length)`,
  );
  check(
    await c.evaluate(
      `[...document.querySelector('video').textTracks].some(t=>[...(t.cues||[])].some(c=>c.text.trim().length > 0)) `,
    ),
    "Actual downloaded WebVTT loaded",
  );
  await click(".player__captions");
  check(
    await c.evaluate(
      `[...document.querySelector('video').textTracks].every(t=>t.mode!=='showing')`,
    ),
    "Subtitles off",
  );
  await click(".player__volume button");
  check(await c.evaluate(`document.querySelector('video').muted`), "Mute");
  await click('[aria-label="Во весь экран (f)"]');
  await c.wait(`!!document.fullscreenElement`);
  check(
    await c.evaluate(`document.fullscreenElement.classList.contains('player')`),
    "Fullscreen uses player",
  );
  await click('[aria-label="Выйти из полноэкранного режима (f)"]');
  await c.wait(`!document.fullscreenElement`);
  await c.screenshot("watch-desktop-local");
  await c.evaluate(
    `window.oldVideo=document.querySelector('video');document.querySelector('.masthead__brand').click()`,
  );
  await c.wait(`location.pathname === '/'`);
  check(
    await c.evaluate(
      `window.oldVideo.paused && !window.oldVideo.getAttribute('src') && !window.oldVideo.isConnected`,
    ),
    "Leaving watch detaches video and stops audio",
  );
  for (const kind of ["private", "hidden", "draft", "pending"]) {
    await openVideo(v[kind]);
    check(
      await c.evaluate(
        `!document.querySelector('video') && !document.querySelector('.watch-page__metadata') && !document.querySelector('.watch-page__avatar')`,
      ),
      `${kind} metadata is not shown to guest`,
    );
  }
  await c.navigate(`${base}/watch?v=9223372036854775808`);
  await c.wait(
    `document.querySelector('.page-state h1')?.textContent==='Видео недоступно'`,
  );
  check(
    await c.evaluate(`!document.querySelector('video')`),
    "Out of range ID rejected",
  );
  await login("stranger");
  await c.wait(
    `document.querySelector('.page-state h1')?.textContent==='Видео недоступно'`,
  );
  check(
    await c.evaluate(`!document.querySelector('.watch-page__metadata')`),
    "Uninvited account has no private metadata",
  );
  await login("invited");
  await c.wait(
    `!!document.querySelector('video') && document.querySelector('.player__start')?.disabled===false`,
  );
  check(
    await c.evaluate(
      `document.querySelector('.watch-page h1').textContent==='Приватное видео'`,
    ),
    "Invited viewer can open private video",
  );
  await click(".player__start");
  await c.wait(`document.querySelector('video').currentTime > 0.3`);
  await c.evaluate(
    `window.privateVideo=document.querySelector('video');document.querySelector('video').pause()`,
  );
  await ownerRequest(`/api/v1/videos/${v.private}/permissions`, {
    email: fixture.people.invited.email,
    allowed: false,
  });
  // A different quality must request fresh protected media even if the first is buffered.
  await click('[aria-label="Настройки"]');
  await c.evaluate(
    `[...document.querySelectorAll('.player__menu button')].find(e=>e.textContent.includes('Качество')).click()`,
  );
  await c.evaluate(
    `[...document.querySelectorAll('.player__menu button')].find(e=>e.textContent.trim()==='360p').click()`,
  );
  await c.wait(
    `document.querySelector('.page-state h1')?.textContent==='Видео недоступно'`,
  );
  check(
    await c.evaluate(
      `!document.querySelector('video') && !document.querySelector('.watch-page__metadata') && window.privateVideo.paused && !window.privateVideo.getAttribute('src')`,
    ),
    "Media denial clears private title, player, source and sound",
  );
  await c.reload();
  await c.wait(
    `document.querySelector('.page-state h1')?.textContent==='Видео недоступно'`,
  );
  check(
    await c.evaluate(
      `!document.querySelector('video') && !document.querySelector('.watch-page__metadata')`,
    ),
    "Revoked access blocks fresh request and metadata",
  );
  await ownerRequest(`/api/v1/videos/${v.private}/permissions`, {
    email: fixture.people.invited.email,
    allowed: true,
  });
  await login("owner");
  await c.wait(`!!document.querySelector('video')`);
  check(
    await c.evaluate(
      `document.querySelector('.watch-page h1').textContent==='Приватное видео'`,
    ),
    "Owner can view private video",
  );
  await c.wait(`document.querySelector('.player__start')?.disabled===false`);
  await click(".player__start");
  await c.wait(`document.querySelector('video').currentTime>0.2`);
  await c.evaluate(`window.signoutVideo=document.querySelector('video')`);
  await click(".avatar-button");
  await click("#logout-button");
  await c.wait(
    `!!document.querySelector('.masthead .sign-in') && !document.querySelector('video')`,
  );
  check(
    await c.evaluate(
      `window.signoutVideo.paused && !window.signoutVideo.getAttribute('src') && !document.querySelector('.watch-page__metadata')`,
    ),
    "Logout removes private media and metadata",
  );
  await c.viewport(390, 844, true);
  await openVideo(v.silent);
  await c.wait(`document.querySelector('.player__start')?.disabled===false`);
  check(
    await c.evaluate(
      `document.documentElement.scrollWidth <= innerWidth && document.querySelector('.player').getBoundingClientRect().width===390 && getComputedStyle(document.querySelector('.mobile-navigation')).display==='none'`,
    ),
    "Phone width and watch navigation",
  );
  await click(".player__start");
  await c.wait(`document.querySelector('video').currentTime>0.3`);
  check(
    await c.evaluate(
      `document.querySelector('video').videoHeight > document.querySelector('video').videoWidth && getComputedStyle(document.querySelector('video')).objectFit==='contain'`,
    ),
    "Silent vertical AV1 plays without stretching",
  );
  check(
    await c.evaluate(`!document.querySelector('.player__captions')`),
    "No invented subtitles for silent video",
  );
  await c.screenshot("watch-mobile-local");
  delayMedia = true;
  await openVideo(v.public);
  check(
    await c.evaluate(`!!document.querySelector('.player__buffer')`),
    "Loading/buffering is visible",
  );
  delayMedia = false;
  await c.wait(`document.querySelector('.player__start')?.disabled===false`);
  await click('[aria-label="Настройки"]');
  check(
    await c.evaluate(
      `document.querySelector('.player__menu').getBoundingClientRect().left>=0 && document.querySelector('.player__menu').getBoundingClientRect().right<=innerWidth`,
    ),
    "Phone settings menu fits viewport",
  );
  await c.send("Input.dispatchKeyEvent", {
    type: "keyDown",
    key: "Escape",
    code: "Escape",
  });
  check(
    await c.evaluate(
      `!document.querySelector('.player__menu') && document.activeElement?.getAttribute('aria-label')==='Настройки'`,
    ),
    "Escape closes settings and restores focus",
  );
  offline = true;
  await c.navigate(watchUrl(v.silent));
  await c.wait(
    `document.querySelector('.page-state h1')?.textContent==='Не удалось загрузить видео'`,
  );
  check(
    await c.evaluate(`!document.querySelector('.watch-page__metadata')`),
    "Failed metadata request removes previous title/player",
  );
  offline = false;
  await click(".page-state button");
  await c.wait(`!!document.querySelector('video')`);
  check(
    await c.evaluate(
      `document.querySelector('.watch-page h1').textContent==='Вертикальное видео без звука'`,
    ),
    "Retry recovers correct video",
  );
  segmentFailure = true;
  await openVideo(v.public);
  await c.wait(`!!document.querySelector('.player__error')`, 120000);
  check(
    await c.evaluate(
      `document.querySelector('video').paused && !document.querySelector('video').getAttribute('src') && !document.querySelector('.player__buffer')`,
    ),
    "Failed segment ends loading and stops playback",
  );
  segmentFailure = false;
  await click(".player__error button");
  await c.wait(`document.querySelector('.player__start')?.disabled===false`);
  await click(".player__start");
  await c.wait(`document.querySelector('video').currentTime>0.2`);
  check(
    await c.evaluate(
      `!document.querySelector('.player__error') && !document.querySelector('video').paused`,
    ),
    "Segment failure retry recovers playback",
  );
  check(c.errors.length === 0, "No uncaught frontend exceptions");
  console.log(
    JSON.stringify(
      {
        status: "pass",
        assertions,
        engine: "Chromium",
        mediaRequests,
        backend:
          "isolated actual PostgreSQL/API/Nginx/RustFS, encoded AV1/AAC/WebVTT",
      },
      null,
      2,
    ),
  );
} catch (error) {
  await c.screenshot("watch-failure");
  console.error({ runtimeErrors: c.errors });
  throw error;
} finally {
  await c.close();
  server.closeAllConnections();
  server.close();
}
