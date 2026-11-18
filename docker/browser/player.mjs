import assert from "node:assert/strict";
import { createServer, request as httpRequest } from "node:http";
import { readFile } from "node:fs/promises";
import { browser, pause } from "./cdp.mjs";
import { firefoxBrowser } from "./bidi.mjs";
let fixture;
for (let i = 0; i < 1200; i++) {
  try {
    fixture = JSON.parse(await readFile("/work/watch-fixture.json", "utf8"));
    if (fixture.videos?.public) break;
  } catch {}
  await pause(250);
}
if (!fixture?.videos?.public) throw new Error("Player fixture not ready");
const firefox = process.argv.includes("--firefox");
let segmentFailure = false,
  holdSegments = false;
const pendingSegments = new Set();
const calls = [],
  sockets = new Set();
const server = createServer(async (req, res) => {
  try {
    const api = req.url.startsWith("/api/"),
      segment = req.url.includes("/hls/") && req.url.endsWith(".m4s");
    if (api) calls.push(req.url);
    if (segment && segmentFailure) {
      res
        .writeHead(410, {
          "content-type": "application/problem+json",
          "cache-control": "no-store",
        })
        .end('{"code":"media_unavailable"}');
      return;
    }
    if (segment && holdSegments)
      await new Promise((resolve) => pendingSegments.add(resolve));
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
async function navigate(id = v.public) {
  await c.navigate(base + "/watch?v=" + id);
  await c.wait(
    `document.querySelector('.player__start')?.disabled===false`,
    30000,
  );
}
async function ready(minimum = 9) {
  await c.wait(
    `document.querySelector('.player video')?.duration>${minimum}&&!document.querySelector('.player__error')&&!document.querySelector('.player--restoring')`,
    30000,
  );
}
async function seek(value) {
  await c.fill(".player__seek", String(value));
  await c.wait(
    `Math.abs(document.querySelector('.player video').currentTime-${value})<0.15`,
  );
}
async function play() {
  await c.evaluate(
    `document.querySelector('.player video').muted=true;document.querySelector('.player video').play()`,
  );
  await c.wait(
    `document.querySelector('.player video').currentTime>0.2&&!document.querySelector('.player video').paused`,
  );
}
async function menuQuality() {
  await c.click('[aria-label="Настройки"]');
  await c.evaluate(
    `[...document.querySelectorAll('.player__menu button')].find(x=>x.textContent.includes('Качество')).click()`,
  );
}
async function changeQuality() {
  const height = await c.evaluate(
    `document.querySelector('.player video').videoHeight`,
  );
  await menuQuality();
  await c.evaluate(
    `(()=>{const buttons=[...document.querySelectorAll('.player__menu button')].filter(x=>/^\\d+p/.test(x.textContent.trim()));const next=buttons.find(x=>!x.textContent.trim().startsWith('${height}p'))||buttons[0];next.click();})()`,
  );
}
async function key(name, value, code) {
  if (firefox) await c.key(value);
  else
    for (const type of ["keyDown", "keyUp"])
      await c.send("Input.dispatchKeyEvent", {
        type,
        key: name,
        code: name,
        windowsVirtualKeyCode: code,
        ...(name === " " && type === "keyDown" ? { text: " " } : {}),
      });
}
async function focusPlayer() {
  await c.evaluate(`document.querySelector('.player').focus()`);
}
async function phoneTap(selector) {
  if (firefox) {
    await c.evaluate(
      `document.querySelector(${JSON.stringify(selector)}).focus()`,
    );
    await c.key("\uE007");
  } else {
    await c.send("Emulation.setTouchEmulationEnabled", { enabled: true });
    await c.evaluate(
      `document.querySelector(${JSON.stringify(selector)}).scrollIntoView({block:'center'})`,
    );
    const point = JSON.parse(
      await c.evaluate(
        `JSON.stringify((()=>{const r=document.querySelector(${JSON.stringify(selector)}).getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})())`,
      ),
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
}
async function ownerRequest(path, method = "GET", body) {
  const host = "http://youtube-frontend-upload-gateway:8080/api/v1";
  const cookies = new Map();
  const absorb = (r) =>
    r.headers.getSetCookie().forEach((x) => {
      const p = x.split(";")[0],
        i = p.indexOf("=");
      cookies.set(p.slice(0, i), p.slice(i + 1));
    });
  absorb(await fetch(host + "/auth/me"));
  const headers = () => ({
    cookie: [...cookies].map(([k, x]) => k + "=" + x).join("; "),
    "x-csrftoken": decodeURIComponent(cookies.get("csrftoken")),
    "content-type": "application/json",
  });
  const response = await fetch(host + "/auth/login", {
    method: "POST",
    headers: headers(),
    body: JSON.stringify({
      email: fixture.people.owner.email,
      password: "abcdef",
    }),
  });
  assert.equal(response.status, 200);
  absorb(response);
  const result = await fetch(host + path, {
    method,
    headers: headers(),
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  assert.equal(result.status, 200);
  return result.json();
}
async function visibility(value) {
  await ownerRequest("/videos/" + v.public + "/settings", "PUT", {
    title: "Проверка позиции",
    description: "Описание",
    visibility: value,
  });
}
async function controls() {
  await focusPlayer();
  await key("k", "k", 75);
  await c.wait(`!document.querySelector('.player video').paused`);
  check(true, "K resumes media after retry");
  await key(" ", "\uE00D", 32);
  await c.wait(`document.querySelector('.player video').paused`);
  check(true, "Space pauses focused player");
  await seek(4);
  await focusPlayer();
  await key("ArrowRight", "\uE014", 39);
  await c.wait(`document.querySelector('.player video').currentTime>=8.9`);
  check(true, "Right arrow seeks forward five seconds");
  await key("ArrowLeft", "\uE012", 37);
  await c.wait(
    `Math.abs(document.querySelector('.player video').currentTime-4)<0.2`,
  );
  check(true, "Left arrow seeks back five seconds");
  await key("j", "j", 74);
  await c.wait(`document.querySelector('.player video').currentTime<0.1`);
  check(true, "J clamps backwards seek to start");
  await key("l", "l", 76);
  await c.wait(`document.querySelector('.player video').currentTime>=9.9`);
  check(true, "L seeks ten seconds");
  await key("Home", "\uE011", 36);
  await c.wait(`document.querySelector('.player video').currentTime<0.1`);
  check(true, "Home seeks to start");
  await key("End", "\uE010", 35);
  await c.wait(`document.querySelector('.player video').currentTime>12`);
  check(
    await c.evaluate(
      `Number(document.querySelector('.player__seek').value)<=Number(document.querySelector('.player__seek').max)`,
    ),
    "End stays within real duration",
  );
  await seek(2);
  await c.fill(".player__volume input", "0.4");
  await c.wait(
    `Math.abs(document.querySelector('.player video').volume-0.4)<0.01&&!document.querySelector('.player video').muted`,
  );
  await pause(100);
  check(true, "Volume range changes media volume and unmutes");
  await focusPlayer();
  await key("ArrowUp", "\uE013", 38);
  check(
    await c.evaluate(
      `Math.abs(document.querySelector('.player video').volume-0.45)<0.01`,
    ),
    "Up arrow adjusts volume",
  );
  await key("ArrowDown", "\uE015", 40);
  check(
    await c.evaluate(
      `Math.abs(document.querySelector('.player video').volume-0.4)<0.01`,
    ),
    "Down arrow adjusts volume",
  );
  await key("m", "m", 77);
  check(
    await c.evaluate(`document.querySelector('.player video').muted`),
    "M mutes",
  );
  await key("m", "m", 77);
  check(
    !(await c.evaluate(`document.querySelector('.player video').muted`)),
    "M restores sound",
  );
  await c.fill(".player__volume input", "0");
  await c.click(".player__volume button");
  check(
    await c.evaluate(
      `!document.querySelector('.player video').muted&&document.querySelector('.player video').volume>0`,
    ),
    "Unmute recovers from zero volume",
  );
  await seek(3);
  await c.evaluate(`document.querySelector('.player__seek').focus()`);
  await key("k", "k", 75);
  check(
    await c.evaluate(`document.querySelector('.player video').paused`),
    "Typing K on range does not trigger player hotkey",
  );
  await c.evaluate(`document.querySelector('.search-form input').focus()`);
  await key("k", "k", 75);
  check(
    await c.evaluate(`document.querySelector('.player video').paused`),
    "Typing in search does not control playback",
  );
  await c.click('[aria-label="Настройки"]');
  check(
    await c.evaluate(
      `document.querySelector('.player__menu').contains(document.activeElement)`,
    ),
    "Settings moves focus into menu",
  );
  await key("End", "\uE010", 35);
  check(
    await c.evaluate(
      `document.activeElement===[...document.querySelectorAll('.player__menu button:not(:disabled)')].at(-1)`,
    ),
    "Menu End focuses final item",
  );
  await key("Home", "\uE011", 36);
  check(
    await c.evaluate(
      `document.activeElement===document.querySelector('.player__menu button')`,
    ),
    "Menu Home focuses first item",
  );
  await key("Escape", "\uE00C", 27);
  check(
    await c.evaluate(
      `!document.querySelector('.player__menu')&&document.activeElement===document.querySelector('[aria-label="Настройки"]')`,
    ),
    "Escape closes menu and restores settings focus",
  );
  await focusPlayer();
  await key("f", "f", 70);
  await c.wait(
    `document.fullscreenElement===document.querySelector('.player')`,
  );
  check(true, "Trusted F key enters actual fullscreen");
  await key("f", "f", 70);
  await c.wait(`!document.fullscreenElement`);
  check(true, "F exits fullscreen");
  await seek(11.5);
  await focusPlayer();
  await key("k", "k", 75);
  await c.wait(`document.querySelector('.player video').ended`, 15000);
  check(
    await c.evaluate(
      `document.querySelector('.player video').paused&&document.querySelector('.player__controls-left button').getAttribute('aria-label').startsWith('Смотреть')`,
    ),
    "Natural end leaves replay action",
  );
  await key("k", "k", 75);
  await c.wait(
    `!document.querySelector('.player video').paused&&document.querySelector('.player video').currentTime>0.3&&document.querySelector('.player video').currentTime<2`,
  );
  check(true, "K replays ended media from start");
  await c.evaluate(
    `document.querySelector('.search-form input').focus();document.querySelector('.player').dispatchEvent(new PointerEvent('pointerleave'))`,
  );
  await c.wait(
    `document.querySelector('.player').classList.contains('player--hidden')`,
  );
  check(
    await c.evaluate(
      `document.querySelector('.player').classList.contains('player--hidden')`,
    ),
    "Controls hide on pointer leave during playback",
  );
  await c.evaluate(
    `document.querySelector('.player').dispatchEvent(new PointerEvent('pointermove'))`,
  );
  await c.wait(
    `!document.querySelector('.player').classList.contains('player--hidden')`,
  );
  check(
    !(await c.evaluate(
      `document.querySelector('.player').classList.contains('player--hidden')`,
    )),
    "Pointer motion restores controls",
  );
  await c.evaluate(
    `window.departedPlayer=document.querySelector('.player video')`,
  );
  await c.click(".masthead__brand");
  await c.wait(`location.pathname==='/'`);
  check(
    await c.evaluate(
      `window.departedPlayer.paused&&!window.departedPlayer.getAttribute('src')&&!window.departedPlayer.isConnected`,
    ),
    "Navigation releases media and sound",
  );
  // Test buffering from the start; a saved position correctly waits for seek data first.
  await c.evaluate(
    `localStorage.removeItem('youtube.guest-progress.v1:${v.public}')`,
  );
  holdSegments = true;
  await navigate();
  for (let i = 0; i < 100 && !pendingSegments.size; i++) await pause(100);
  await c.evaluate(`document.querySelector('.player video').muted=true`);
  await focusPlayer();
  await key("k", "k", 75);
  await c.wait(`!!document.querySelector('.player__buffer')`);
  check(
    !(await c.evaluate(`!!document.querySelector('.player__error')`)),
    "Held segments show buffering instead of failure",
  );
  holdSegments = false;
  for (const resolve of pendingSegments) resolve();
  pendingSegments.clear();
  await c.wait(`document.querySelector('.player video').currentTime>0.2`);
  check(
    !(await c.evaluate(`!!document.querySelector('.player__buffer')`)),
    "Real segments clear buffering and advance playback",
  );
  await c.viewport(390, 844);
  await navigate(v.silent);
  await ready(0);
  await phoneTap(".player__start");
  await c.wait(`document.querySelector('.player video').currentTime>0.2`);
  check(
    await c.evaluate(
      `document.querySelector('.player video').videoHeight>document.querySelector('.player video').videoWidth`,
    ),
    "Portrait video without audio decodes on phone viewport",
  );
  await phoneTap(".player__controls-left button");
  await c.wait(`document.querySelector('.player video').paused`);
  check(true, "Phone playback control pauses");
  await seek(1.5);
  check(
    await c.evaluate(
      `Math.abs(document.querySelector('.player video').currentTime-1.5)<0.2`,
    ),
    "Phone seek changes actual position",
  );
  await c.click(".player__volume button");
  check(
    await c.evaluate(`document.querySelector('.player video').muted`),
    "Phone mute control works for video without audio",
  );
  await navigate();
  await ready();
  await play();
  await c.evaluate(
    `document.querySelector('.player video').pause();window.revokedMedia=document.querySelector('.player video')`,
  );
  await seek(5);
  await visibility("private");
  await changeQuality();
  await c.wait(
    `document.querySelector('.watch-page .page-state h1')?.textContent==='Видео недоступно'`,
    30000,
  );
  check(
    await c.evaluate(
      `window.revokedMedia.paused&&!window.revokedMedia.getAttribute('src')&&!document.querySelector('.watch-page__metadata')`,
    ),
    "Real HLS denial after privacy change removes metadata and releases player",
  );
  await visibility("public");

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
}
try {
  await c.viewport(1280, 900);
  await navigate();
  await ready();
  await play();
  await c.evaluate(`document.querySelector('.player video').pause()`);
  await seek(5);
  await c.wait(
    `!document.querySelector('.player video').seeking&&Number(localStorage.getItem('youtube.guest-progress.v1:${v.public}'))>=4.9`,
  );
  const storedBefore = await c.evaluate(
    `Number(localStorage.getItem('youtube.guest-progress.v1:${v.public}'))`,
  );
  if (!process.argv.includes("--controls-only")) {
    segmentFailure = true;
    await changeQuality();
    await c.wait(`!!document.querySelector('.player__error')`, 30000);
    check(
      await c.evaluate(
        `document.querySelector('.player video').paused&&!document.querySelector('.player video').getAttribute('src')`,
      ),
      "Segment failure stops media and releases source",
    );
    check(
      await c.evaluate(
        `Number(localStorage.getItem('youtube.guest-progress.v1:${v.public}'))>=4.9`,
      ),
      "Resource cleanup does not overwrite saved position with zero",
    );
    segmentFailure = false;
    await c.click(".player__error button");
    await ready();
    await c.wait(
      `Math.abs(document.querySelector('.player video').currentTime-5)<0.3&&document.querySelector('.player video').paused`,
    );
    check(
      await c.evaluate(
        `Math.abs(document.querySelector('.player video').currentTime-5)<0.3&&document.querySelector('.player video').paused`,
      ),
      "Retry restores actual position and paused state",
    );
  }
  await controls();
  console.log(
    JSON.stringify(
      {
        status: "pass",
        assertions,
        engine: firefox ? "Firefox" : "Chromium",
        savedBeforeFailure: storedBefore,
      },
      null,
      2,
    ),
  );
} catch (error) {
  console.error({
    runtime: c.errors,
    lastMedia: calls.filter((x) => x.includes("/hls/")).slice(-6),
    state: JSON.parse(
      await c.evaluate(
        `JSON.stringify({time:document.querySelector('.player video')?.currentTime,paused:document.querySelector('.player video')?.paused,notice:document.querySelector('.player__notice')?.textContent,canplay:document.querySelector('.player__start')?.disabled,duration:document.querySelector('.player video')?.duration,volume:document.querySelector('.player video')?.volume,muted:document.querySelector('.player video')?.muted,error:document.querySelector('.player__error')?.textContent,stored:localStorage.getItem('youtube.guest-progress.v1:${v.public}')})`,
      ),
    ),
  });
  throw error;
} finally {
  segmentFailure = false;
  holdSegments = false;
  for (const resolve of pendingSegments) resolve();
  pendingSegments.clear();
  try {
    await visibility("public");
  } catch {}
  await c.close();
  for (const socket of sockets) socket.destroy();
  server.closeAllConnections();
  server.close();
}
