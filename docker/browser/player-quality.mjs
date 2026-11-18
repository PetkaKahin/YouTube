import assert from "node:assert/strict";
import { createServer, request as httpRequest } from "node:http";
import { readFile } from "node:fs/promises";
import { browser, pause } from "./cdp.mjs";
import { firefoxBrowser } from "./bidi.mjs";
let fixture;
for (let i = 0; i < 1200; i++) {
  try {
    fixture = JSON.parse(await readFile("/work/watch-fixture.json", "utf8"));
    if (fixture.videos?.low) break;
  } catch {}
  await pause(250);
}
if (!fixture?.videos?.low) throw new Error("Player fixture not ready");
const firefox = process.argv.includes("--firefox");
let segmentFailure = false,
  holdSegments = false,
  limited = false,
  subtitleFailure = false,
  foreignSubtitle = false,
  holdSubtitle = false;
const pendingSubtitles = new Set();
const pendingSegments = new Set();
const calls = [],
  sockets = new Set();
const server = createServer(async (req, res) => {
  try {
    const api = req.url.startsWith("/api/"),
      segment = req.url.includes("/hls/") && req.url.endsWith(".m4s"),
      subtitle = req.url.includes("/hls/") && req.url.endsWith(".vtt"),
      master =
        req.url ===
        "/api/v1/videos/" + fixture.videos.public + "/hls/master.m3u8",
      detail = req.url === "/api/v1/videos/" + fixture.videos.public;
    if (api) calls.push(req.url);
    if ((segment && segmentFailure) || (subtitle && subtitleFailure)) {
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
    if (subtitle && holdSubtitle)
      await new Promise((resolve) => pendingSubtitles.add(resolve));
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
    let body = Buffer.from(await r.arrayBuffer());
    if (r.status === 200 && detail && limited) {
      const text = body.toString();
      body = Buffer.from(
        text.replace(/"qualities":\[[^\]]*\]/, (match) => {
          const qualities = JSON.parse("{" + match + "}").qualities;
          return (
            '"qualities":' +
            JSON.stringify(qualities.filter((x) => x.quality === 360))
          );
        }),
      );
    }
    if (r.status === 200 && master && limited) {
      const lines = body.toString().split("\n"),
        result = [];
      for (let i = 0; i < lines.length; i++) {
        if (
          lines[i].startsWith("#EXT-X-STREAM-INF") &&
          !lines[i].includes("x360")
        ) {
          i++;
          continue;
        }
        result.push(lines[i]);
      }
      body = Buffer.from(result.join("\n"));
    }
    if (r.status === 200 && master && foreignSubtitle)
      body = Buffer.from(
        body
          .toString()
          .replace(
            /(#EXT-X-MEDIA:TYPE=SUBTITLES[^\n]*URI=")[^"]+"/g,
            '$1https://invalid.example/captions.m3u8"',
          ),
      );

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
    `document.querySelector('.player video')?.duration>${minimum}&&!document.querySelector('.player__error')`,
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
async function captions() {
  await c.wait(`!!document.querySelector('.player__captions')`);
  await c.click(".player__captions");
  await c.wait(
    `[...document.querySelector('.player video').textTracks].some(x=>x.mode==='showing')`,
  );
}
async function paused() {
  await c.evaluate(`document.querySelector('.player video').pause()`);
}
async function loadedCues() {
  await c.wait(
    `[...document.querySelector('.player video').textTracks].some(x=>x.mode==='showing'&&x.cues?.length>0)`,
    30000,
  );
}
async function choose(label) {
  await menuQuality();
  await c.evaluate(
    `[...document.querySelectorAll('.player__menu button')].find(x=>x.textContent.trim()===${JSON.stringify(label)}).click()`,
  );
  await c.wait(`!document.querySelector('.player__menu')`);
}
async function options() {
  await menuQuality();
  return JSON.parse(
    await c.evaluate(
      `JSON.stringify([...document.querySelectorAll('.player__menu button')].map(x=>x.textContent.trim()).filter(x=>/^\\d+p/.test(x)))`,
    ),
  );
}
async function close() {
  await c.evaluate(
    `document.querySelector('.player').dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))`,
  );
  await c.wait(`!document.querySelector('.player__menu')`);
}
async function refresh() {
  await c.evaluate(
    `(async()=>{const client=Object.values(document.querySelector('#app').__vue_app__._context.provides).find(x=>typeof x?.getQueryCache==='function');await client.refetchQueries({predicate:q=>q.queryKey[0]==='video'})})()`,
  );
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
async function extraChecks() {
  limited = true;
  await navigate();
  await ready();
  await play();
  await c.wait(`document.querySelector('.player video').currentTime>1`);

  await choose("360p");
  await captions();
  await loadedCues();
  const before = await c.evaluate(
    `document.querySelector('.player video').currentTime`,
  );
  limited = false;
  await refresh();
  await c.wait(
    `document.querySelector('.player video').readyState>=2&&!document.querySelector('.player video').paused&&document.querySelector('.player video').currentTime>=${before}-0.3`,
    30000,
  );
  check(true, "Readiness update preserves ongoing playback and time");
  await options();
  check(
    await c.evaluate(
      `[...document.querySelectorAll('.player__menu button')].find(x=>x.textContent.trim()==='360p').getAttribute('aria-pressed')==='true'`,
    ),
    "Explicit quality survives readiness update by label, not old index",
  );
  await close();
  await loadedCues();
  check(
    await c.evaluate(
      `document.querySelector('.player__captions').getAttribute('aria-pressed')==='true'`,
    ),
    "Readiness update retains captions while playing",
  );
  await paused();
  await choose("480p");
  await play();
  await c.wait(
    `document.querySelector('.player video').videoHeight===480`,
    30000,
  );
  await paused();
  limited = true;
  await refresh();
  await c.wait(`document.querySelector('.player video').readyState>=2`, 30000);
  check(
    JSON.stringify(await options()) === JSON.stringify(["360p"]),
    "Removed ready variant is no longer selectable",
  );
  check(
    await c.evaluate(
      `[...document.querySelectorAll('.player__menu button')].find(x=>x.textContent.trim()==='Авто').getAttribute('aria-pressed')==='true'`,
    ),
    "Unavailable preferred variant falls back to auto",
  );
  await close();
  limited = false;

  await navigate();
  await ready();
  await play();
  await paused();
  holdSubtitle = true;
  await c.click(".player__captions");
  for (let i = 0; i < 100 && !pendingSubtitles.size; i++) await pause(100);
  check(pendingSubtitles.size > 0, "Real subtitle request is held in flight");
  await navigate(v.silent);
  await ready(0);
  holdSubtitle = false;
  for (const resolve of pendingSubtitles) resolve();
  pendingSubtitles.clear();
  await pause(200);
  check(
    !(await c.evaluate(
      `!!document.querySelector('.player__captions')||!!document.querySelector('.player__notice')`,
    )),
    "Late previous subtitle response cannot affect silent video",
  );
  check(
    JSON.stringify(await options()) ===
      JSON.stringify(["720p", "480p", "360p"]),
    "Silent portrait video offers its actual ready variants",
  );
  await close();
  await c.click('[aria-label="Настройки"]');
  check(
    await c.evaluate(
      `!!document.querySelector('.player__menu')&&!document.querySelector('.player__menu').textContent.includes('Субтитры')`,
    ),
    "No caption action is invented for missing tracks",
  );
  await close();

  await navigate(v.low);
  await ready(0);
  await play();
  await paused();
  check(
    JSON.stringify(await options()) === JSON.stringify(["240p"]),
    "Single below-360p source quality is offered without upscaling",
  );
  await close();
  check(
    await c.evaluate(
      `document.querySelector('.player video').videoHeight===240&&!document.querySelector('.player__captions')`,
    ),
    "Real 240p video decodes without invented subtitles",
  );

  foreignSubtitle = true;
  await navigate();
  await ready();
  await play();
  await paused();
  await c.click(".player__captions");
  await c.wait(
    `!!document.querySelector('.player__notice')||!!document.querySelector('.player__error')`,
    30000,
  );
  check(
    await c.evaluate(
      `!performance.getEntriesByType('resource').some(x=>x.name.includes('invalid.example'))`,
    ),
    "Foreign subtitle URI is blocked before external network request",
  );
  foreignSubtitle = false;

  await navigate();
  await ready();
  await play();
  await paused();
  await visibility("private");
  await c.click(".player__captions");
  await c.wait(
    `document.querySelector('.watch-page .page-state h1')?.textContent==='Видео недоступно'`,
    30000,
  );
  check(
    !(await c.evaluate(
      `!!document.querySelector('.player')||!!document.querySelector('.watch-page__metadata')`,
    )),
    "Real subtitle access refusal removes protected view",
  );
  await visibility("public");

  await c.viewport(390, 844);
  await navigate();
  await ready();
  await play();
  await paused();
  await choose("360p");
  await captions();
  await loadedCues();
  check(
    await c.evaluate(
      `document.querySelector('.player__captions').getAttribute('aria-pressed')==='true'&&document.querySelector('.player video').paused`,
    ),
    "Phone quality and caption controls preserve pause",
  );
  await c.click(".player__captions");
  check(
    await c.evaluate(
      `[...document.querySelector('.player video').textTracks].every(t=>t.mode!=='showing')`,
    ),
    "Phone caption toggle disables actual track",
  );
}
try {
  await c.viewport(1280, 900);
  await navigate();
  await ready();
  await play();
  await paused();
  await seek(5);
  check(
    JSON.stringify(await options()) === JSON.stringify(["480p", "360p"]),
    "Only the two real ready qualities are offered",
  );
  await close();
  await choose("360p");
  check(
    await c.evaluate(
      `Math.abs(document.querySelector('.player video').currentTime-5)<0.5&&document.querySelector('.player video').paused`,
    ),
    "Quality selection keeps position and pause",
  );
  await play();
  await c.wait(
    `document.querySelector('.player video').videoHeight===360`,
    30000,
  );
  await paused();
  check(
    await c.evaluate(
      `document.querySelector('.player video').currentTime>=4.9`,
    ),
    "Chosen 360p really decodes at 360 pixels without restarting",
  );
  await captions();
  await loadedCues();
  check(
    calls.some((x) => x.endsWith(".vtt")),
    "Prepared WebVTT loaded through protected media route",
  );
  const cue = JSON.parse(
    await c.evaluate(
      `JSON.stringify((()=>{const track=[...document.querySelector('.player video').textTracks].find(x=>x.mode==='showing');const cue=[...track.cues].find(x=>x.endTime>x.startTime);return {start:cue.startTime,end:cue.endTime,text:cue.text}})())`,
    ),
  );
  const time = Math.min(
    cue.end - 0.05,
    Math.max(cue.start + 0.05, (cue.start + cue.end) / 2),
  );
  await seek(time);
  await c.wait(
    `[...document.querySelector('.player video').textTracks].some(x=>x.mode==='showing'&&[...(x.activeCues||[])].some(c=>c.text===${JSON.stringify(cue.text)}))`,
  );
  check(true, "Subtitle cue follows actual seeked media time");
  await c.click(".player__captions");
  check(
    await c.evaluate(
      `[...document.querySelector('.player video').textTracks].every(x=>x.mode!=='showing')`,
    ),
    "CC disables visible source track",
  );
  await captions();
  await loadedCues();
  await choose("480p");
  await play();
  await c.wait(
    `document.querySelector('.player video').videoHeight===480`,
    30000,
  );
  check(
    await c.evaluate(
      `document.querySelector('.player__captions').getAttribute('aria-pressed')==='true'`,
    ),
    "Switching quality keeps subtitles enabled",
  );
  await paused();
  await choose("Авто");
  await menuQuality();
  check(
    await c.evaluate(
      `[...document.querySelectorAll('.player__menu button')].find(x=>x.textContent.trim()==='Авто').getAttribute('aria-pressed')==='true'`,
    ),
    "Auto selection returns adaptive mode",
  );
  await close();

  limited = true;
  await navigate();
  await ready();
  await play();
  await paused();
  await seek(5);
  check(
    JSON.stringify(await options()) === JSON.stringify(["360p"]),
    "A temporarily limited readiness response offers only its real lower variant",
  );
  await close();
  await captions();
  await loadedCues();

  limited = false;
  await refresh();
  await c.wait(
    `document.querySelector('.player video').readyState>=2&&Math.abs(document.querySelector('.player video').currentTime-5)<0.5`,
    30000,
  );
  check(
    await c.evaluate(`document.querySelector('.player video').paused`),
    "Metadata readiness refresh retains paused position",
  );
  check(
    JSON.stringify(await options()) === JSON.stringify(["480p", "360p"]),
    "Readiness refresh adds the real higher variant",
  );
  await close();
  await loadedCues();
  check(
    await c.evaluate(
      `document.querySelector('.player__captions').getAttribute('aria-pressed')==='true'`,
    ),
    "Readiness refresh preserves selected captions",
  );

  await navigate();
  await ready();
  await play();
  await paused();
  await seek(3);
  subtitleFailure = true;
  await c.click(".player__captions");
  await c.wait(
    `document.querySelector('.player__notice')?.textContent.includes('Не удалось загрузить субтитры')`,
    30000,
  );
  check(
    await c.evaluate(
      `document.querySelector('.player__captions').getAttribute('aria-pressed')==='false'&&!document.querySelector('.player__error')`,
    ),
    "Subtitle failure turns captions off without claiming success or failing video",
  );
  subtitleFailure = false;
  await captions();
  await loadedCues();
  check(
    !(await c.evaluate(`!!document.querySelector('.player__notice')`)),
    "Successful subtitle retry clears its error notice",
  );
  await extraChecks();
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
  console.log(
    JSON.stringify(
      {
        status: "pass",
        assertions,
        engine: firefox ? "Firefox" : "Chromium",
        backend: "real HLS and source WebVTT",
      },
      null,
      2,
    ),
  );
} catch (error) {
  console.error({
    runtime: c.errors,
    state: JSON.parse(
      await c.evaluate(
        `JSON.stringify({time:document.querySelector('.player video')?.currentTime,height:document.querySelector('.player video')?.videoHeight,notice:document.querySelector('.player__notice')?.textContent,error:document.querySelector('.player__error')?.textContent,menu:document.querySelector('.player__menu')?.textContent})`,
      ),
    ),
    lastMedia: calls.filter((x) => x.includes("/hls/")).slice(-6),
  });
  throw error;
} finally {
  subtitleFailure = false;
  holdSubtitle = false;
  segmentFailure = false;
  for (const resolve of pendingSegments) resolve();
  for (const resolve of pendingSubtitles) resolve();
  try {
    await visibility("public");
  } catch {}
  await c.close();
  for (const socket of sockets) socket.destroy();
  server.closeAllConnections();
  server.close();
}
