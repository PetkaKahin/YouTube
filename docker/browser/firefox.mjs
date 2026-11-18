import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { argv } from "node:process";
import { pause } from "./cdp.mjs";

await mkdir("/tmp/firefox-frontend", { recursive: true });
const process = spawn(
  "firefox-esr",
  [
    "--headless",
    "--remote-debugging-port=9227",
    "--profile",
    "/tmp/firefox-frontend",
    "about:blank",
  ],
  { stdio: "ignore" },
);
let socket;
try {
  for (let attempt = 0; attempt < 100; attempt++) {
    try {
      const candidate = new WebSocket("ws://127.0.0.1:9227/session");
      await new Promise((resolve, reject) => {
        candidate.addEventListener("open", resolve, { once: true });
        candidate.addEventListener("error", reject, { once: true });
      });
      socket = candidate;
      break;
    } catch {
      await pause(100);
    }
  }
  if (!socket) throw new Error("Firefox did not start");
  let sequence = 0;
  const calls = new Map();
  socket.addEventListener("message", ({ data }) => {
    const message = JSON.parse(data);
    const call = calls.get(message.id);
    if (call) {
      clearTimeout(call.timer);
      calls.delete(message.id);
      if (message.type === "error")
        call.reject(new Error(JSON.stringify(message)));
      else call.resolve(message.result);
    }
  });
  const send = (method, params = {}) =>
    new Promise((resolve, reject) => {
      const id = ++sequence;
      const timer = setTimeout(() => {
        calls.delete(id);
        reject(new Error(`Firefox timeout: ${method}`));
      }, 20000);
      calls.set(id, { resolve, reject, timer });
      socket.send(JSON.stringify({ id, method, params }));
    });
  await send("session.new", { capabilities: {} });
  const { context } = await send("browsingContext.create", { type: "tab" });
  const evaluate = async (expression) => {
    const result = await send("script.evaluate", {
      expression,
      target: { context },
      awaitPromise: true,
    });
    if (result.type === "exception")
      throw new Error(JSON.stringify(result.exceptionDetails));
    return result.result.value;
  };
  const wait = async (expression) => {
    for (let attempt = 0; attempt < 100; attempt++) {
      if (await evaluate(expression)) return;
      await pause(100);
    }
    const shot = await send("browsingContext.captureScreenshot", {
      context,
      origin: "viewport",
    });
    await writeFile(
      "/work/firefox-failure.png",
      Buffer.from(shot.data, "base64"),
    );
    console.error(
      await evaluate(
        `JSON.stringify({url:location.href,state:document.querySelector('.page-state')?.innerText,error:document.querySelector('.player__error')?.innerText,body:document.body.innerText.slice(0,400),video:document.querySelector('video')?.error?.code,av1:MediaSource.isTypeSupported('video/mp4;codecs="av01.0.04M.08"'),aac:MediaSource.isTypeSupported('audio/mp4;codecs="mp4a.40.2"')})`,
      ),
    );
    throw new Error(`Firefox DOM condition: ${expression}`);
  };
  let checks = 0;
  const check = (value, message) => {
    assert.ok(value, message);
    checks++;
  };
  for (const [width, height] of [
    [1920, 935],
    [390, 844],
  ]) {
    await send("browsingContext.setViewport", {
      context,
      viewport: { width, height },
      devicePixelRatio: 1,
    });
    await send("browsingContext.navigate", {
      context,
      url: "http://nginx:8080/",
      wait: "complete",
    });
    await wait(`!!document.querySelector('.masthead')`);
    check(
      await evaluate(
        `document.querySelector('.masthead').getBoundingClientRect().height === ${width === 390 ? 48 : 56}`,
      ),
      "Firefox header height",
    );
    check(
      await evaluate(`document.documentElement.scrollWidth <= innerWidth`),
      "Firefox no horizontal overflow",
    );
    await send("browsingContext.navigate", {
      context,
      url: "http://nginx:8080/register",
      wait: "complete",
    });
    await wait(`!!document.querySelector('#register-name')`);
    check(
      await evaluate(
        `document.documentElement.scrollWidth <= innerWidth && document.querySelectorAll('.field input').length === 5`,
      ),
      "Firefox registration layout",
    );
    await evaluate(`document.fonts.ready.then(()=>true)`);
    const { data } = await send("browsingContext.captureScreenshot", {
      context,
      origin: "viewport",
    });
    await writeFile(
      `/work/firefox-register-${width}.png`,
      Buffer.from(data, "base64"),
    );
  }
  if (argv.includes("--watch")) {
    const fixture = JSON.parse(
      await readFile("/work/watch-fixture.json", "utf8"),
    );
    const base = "http://youtube-frontend-watch-gateway:8080";
    const click = async (selector) => {
      await evaluate(
        `document.querySelector(${JSON.stringify(selector)}).scrollIntoView({block:'center'})`,
      );
      const point = JSON.parse(
        await evaluate(
          `JSON.stringify((()=>{const r=document.querySelector(${JSON.stringify(selector)}).getBoundingClientRect();return {x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)}})())`,
        ),
      );
      await send("input.performActions", {
        context,
        actions: [
          {
            type: "pointer",
            id: "mouse",
            parameters: { pointerType: "mouse" },
            actions: [
              { type: "pointerMove", ...point, duration: 0 },
              { type: "pointerDown", button: 0 },
              { type: "pointerUp", button: 0 },
            ],
          },
        ],
      });
    };
    for (const [width, height] of [
      [1920, 935],
      [390, 844],
    ]) {
      await send("browsingContext.setViewport", {
        context,
        viewport: { width, height },
        devicePixelRatio: 1,
      });
      await send("browsingContext.navigate", {
        context,
        url: `${base}/watch?v=${fixture.videos.public}`,
        wait: "complete",
      });
      await wait(
        `document.querySelector('.player__start')?.disabled===false && !!document.querySelector('.watch-page h1')`,
      );
      check(
        await evaluate(
          `document.documentElement.scrollWidth <= innerWidth && document.querySelector('.watch-page__author').getAttribute('href')==='/@${fixture.people.owner.tag}' && !window.frontendInject`,
        ),
        "Firefox watch layout and new channel contract",
      );
      await click(".player__start");
      await wait(`document.querySelector('video').currentTime>0.3`);
      check(
        await evaluate(
          `document.querySelector('video').videoWidth>0 && !document.querySelector('video').paused`,
        ),
        "Firefox actual AV1/AAC playback",
      );
      await evaluate(`document.querySelector('.player').focus()`);
      await send("input.performActions", {
        context,
        actions: [
          {
            type: "key",
            id: "keyboard",
            actions: [
              { type: "keyDown", value: "k" },
              { type: "keyUp", value: "k" },
            ],
          },
        ],
      });
      check(
        await evaluate(`document.querySelector('video').paused`),
        "Firefox keyboard pause",
      );
      await evaluate(
        `document.querySelector('.player__seek').value=5;document.querySelector('.player__seek').dispatchEvent(new Event('input',{bubbles:true}))`,
      );
      await click('[aria-label="Настройки"]');
      await evaluate(
        `[...document.querySelectorAll('.player__menu button')].find(e=>e.textContent.includes('Качество')).click()`,
      );
      await wait(
        `document.querySelector('.player__menu')?.textContent.includes('360p')`,
      );
      await evaluate(
        `[...document.querySelectorAll('.player__menu button')].find(e=>e.textContent.trim()==='360p').click()`,
      );
      await wait(`!document.querySelector('.player__menu')`);
      check(
        await evaluate(
          `Math.abs(document.querySelector('video').currentTime-5)<1 && document.querySelector('video').paused`,
        ),
        "Firefox quality preserves position",
      );
      await click('[aria-label="Настройки"]');
      await evaluate(
        `[...document.querySelectorAll('.player__menu button')].find(e=>e.textContent.includes('Субтитры')).click()`,
      );
      await evaluate(
        `[...document.querySelectorAll('.player__menu button')].at(-1).click()`,
      );
      await wait(
        `[...document.querySelector('video').textTracks].some(t=>t.cues?.length)`,
      );
      check(
        await evaluate(
          `[...document.querySelector('video').textTracks].some(t=>t.mode==='showing' && t.cues.length)`,
        ),
        "Firefox actual WebVTT cues",
      );
      await click(".player__volume button");
      check(
        await evaluate(`document.querySelector('video').muted`),
        "Firefox mute",
      );
      const shot = await send("browsingContext.captureScreenshot", {
        context,
        origin: "viewport",
      });
      await writeFile(
        `/work/firefox-watch-${width}.png`,
        Buffer.from(shot.data, "base64"),
      );
      await send("browsingContext.navigate", {
        context,
        url: `${base}/watch?v=${fixture.videos.silent}`,
        wait: "complete",
      });
      await wait(`document.querySelector('.player__start')?.disabled===false`);
      await click(".player__start");
      await wait(`document.querySelector('video').currentTime>0.3`);
      check(
        await evaluate(
          `document.querySelector('video').videoHeight>document.querySelector('video').videoWidth && !document.querySelector('.player__captions')`,
        ),
        "Firefox silent portrait playback",
      );
      await evaluate(
        `window.oldVideo=document.querySelector('video');document.querySelector('.masthead__brand').click()`,
      );
      await wait(`location.pathname==='/'`);
      check(
        await evaluate(
          `window.oldVideo.paused && !window.oldVideo.getAttribute('src') && !window.oldVideo.isConnected && document.title==='YouTube'`,
        ),
        "Firefox player and title cleanup",
      );
    }
  }
  if (argv.includes("--watch")) {
    const fixture = JSON.parse(
      await readFile("/work/watch-fixture.json", "utf8"),
    );
    const base = "http://youtube-frontend-watch-gateway:8080";
    await send("browsingContext.navigate", {
      context,
      url: `${base}/login?returnTo=${encodeURIComponent(`/watch?v=${fixture.videos.public}`)}`,
      wait: "complete",
    });
    await wait(`!!document.querySelector('#login-email')`);
    await evaluate(
      `(()=>{for(const [id,value] of [['login-email',${JSON.stringify(fixture.people.stranger.email)}],['login-password','abcdef']]){const e=document.getElementById(id);e.value=value;e.dispatchEvent(new Event('input',{bubbles:true}));}document.querySelector('.auth-card button[type=submit]').click()})()`,
    );
    await wait(
      `!!document.querySelector('.video-like button[aria-pressed]') && !!document.querySelector('#new-comment') && !!document.querySelector('.subscribe button')`,
    );
    const previous = await evaluate(
      `document.querySelector('.video-like button').getAttribute('aria-pressed')`,
    );
    await evaluate(`document.querySelector('.video-like button').click()`);
    await wait(
      `document.querySelector('.video-like button').getAttribute('aria-pressed')!==${JSON.stringify(previous)}`,
    );
    check(true, "Firefox video like command");
    const subscribed = await evaluate(
      `document.querySelector('.subscribe button').getAttribute('aria-pressed')`,
    );
    await evaluate(`document.querySelector('.subscribe button').click()`);
    await wait(
      `document.querySelector('.subscribe button').getAttribute('aria-pressed')!==${JSON.stringify(subscribed)}`,
    );
    check(true, "Firefox subscription command");
    await evaluate(
      `(()=>{const e=document.querySelector('#new-comment');e.value='Комментарий Firefox';e.dispatchEvent(new Event('input',{bubbles:true}));})()`,
    );
    await wait(
      `!document.querySelector('.discussion button[type=submit]').disabled`,
    );
    await evaluate(
      `document.querySelector('.discussion button[type=submit]').click()`,
    );
    await wait(
      `document.querySelector('.comment__text')?.textContent==='Комментарий Firefox' && document.querySelector('#new-comment').value===''`,
    );
    check(true, "Firefox real comment publication");
    await evaluate(`document.querySelector('.comment .icon-button').click()`);
    await wait(
      `document.querySelector('.comment .icon-button').getAttribute('aria-pressed')==='true'`,
    );
    check(
      await evaluate(`document.documentElement.scrollWidth<=innerWidth`),
      "Firefox comment like and phone width",
    );
  }
  if (argv.includes("--channel")) {
    const fixture = JSON.parse(
      await readFile("/work/watch-fixture.json", "utf8"),
    );
    const base = "http://youtube-frontend-watch-gateway:8080";
    const tag = fixture.people.owner.tag;
    for (const [width, height] of [
      [1920, 935],
      [390, 844],
    ]) {
      await send("browsingContext.setViewport", {
        context,
        viewport: { width, height },
        devicePixelRatio: 1,
      });
      await send("browsingContext.navigate", {
        context,
        url: `${base}/@${tag}/videos`,
        wait: "complete",
      });
      await wait(
        `document.querySelectorAll('.channel-page__grid--videos .video-card').length>=20`,
      );
      check(
        await evaluate(
          `document.querySelector('.channel-page h1').textContent==='Общий автор' && !document.querySelector('.channel-page h1 script') && document.querySelector('.channel-page__identity p').textContent==='@${tag}'`,
        ),
        "Firefox channel profile and safe text",
      );
      check(
        await evaluate(`document.documentElement.scrollWidth<=innerWidth`),
        "Firefox channel width",
      );
      await evaluate(
        `document.querySelector('.channel-page__tabs a:last-child').click()`,
      );
      await wait(`document.querySelectorAll('.playlist-card').length>=20`);
      check(
        await evaluate(
          `location.pathname.endsWith('/playlists') && document.documentElement.scrollWidth<=innerWidth`,
        ),
        "Firefox playlist tab and layout",
      );
      await evaluate(
        `document.querySelector('.playlist-card__preview').click()`,
      );
      await wait(`!!document.querySelector('.playlist-contents__header')`);
      check(
        await evaluate(
          `document.querySelector('.playlist-contents .page-state h1')?.textContent==='Здесь пока нет доступных видео'`,
        ),
        "Firefox empty playlist",
      );
      await send("browsingContext.navigate", {
        context,
        url: `${base}/@${tag}/playlists?list=${fixture.playlists.public}`,
        wait: "complete",
      });
      await wait(
        `document.querySelectorAll('.playlist-contents__videos li').length>=20`,
      );
      check(
        await evaluate(
          `document.querySelector('.playlist-contents__header h2').textContent.includes('<img') && !document.querySelector('.playlist-contents__header img') && !window.frontendInject`,
        ),
        "Firefox playlist HTML stays text",
      );
      check(
        await evaluate(
          `document.documentElement.scrollWidth<=innerWidth && [...document.querySelectorAll('.playlist-contents .video-card__preview')].every(e=>!e.getAttribute('href').endsWith('v=${fixture.videos.private}'))`,
        ),
        "Firefox playlist filtering and width",
      );
      const shot = await send("browsingContext.captureScreenshot", {
        context,
        origin: "viewport",
      });
      await writeFile(
        `/work/firefox-channel-${width}.png`,
        Buffer.from(shot.data, "base64"),
      );
      await send("browsingContext.navigate", {
        context,
        url: `${base}/@${tag}/playlists?list=${fixture.playlists.private}`,
        wait: "complete",
      });
      await wait(
        `document.querySelector('.playlist-contents .page-state h1')?.textContent==='Плейлист недоступен'`,
      );
      check(
        await evaluate(
          `!document.querySelector('.playlist-contents__header') && !document.querySelector('.playlist-contents__videos')`,
        ),
        "Firefox private playlist without metadata",
      );
    }
  }
  if (argv.includes("--playlist-link")) {
    const fixture = JSON.parse(
      await readFile("/work/watch-fixture.json", "utf8"),
    );
    const base = "http://youtube-frontend-watch-gateway:8080";
    const tag = fixture.people.owner.tag;
    for (const [width, height] of [
      [1920, 935],
      [390, 844],
    ]) {
      await send("browsingContext.setViewport", {
        context,
        viewport: { width, height },
        devicePixelRatio: 1,
      });
      for (const kind of ["public", "empty_unlisted"]) {
        const id = fixture.playlists[kind];
        await send("browsingContext.navigate", {
          context,
          url: `${base}/playlist?list=${id}`,
          wait: "complete",
        });
        await wait(
          `location.pathname.startsWith('/@') && !!document.querySelector('.playlist-contents__header')`,
        );
        check(
          await evaluate(
            `location.pathname==='/@${tag}/playlists' && new URLSearchParams(location.search).get('list')==='${id}' && document.documentElement.scrollWidth<=innerWidth`,
          ),
          "Firefox direct link resolves exact ID and channel",
        );
        if (kind === "empty_unlisted")
          check(
            await evaluate(
              `document.querySelector('.playlist-contents .page-state h1')?.textContent==='Здесь пока нет доступных видео'`,
            ),
            "Firefox resolves empty unlisted without first video",
          );
      }
      await send("browsingContext.navigate", {
        context,
        url: `${base}/playlist?list=${fixture.playlists.empty_private}`,
        wait: "complete",
      });
      await wait(
        `document.querySelector('.playlist-link-page .page-state h1')?.textContent==='Плейлист недоступен'`,
      );
      check(
        await evaluate(
          `!document.querySelector('.channel-page__header') && !document.querySelector('.playlist-contents__header')`,
        ),
        "Firefox guest private link does not expose owner",
      );
    }
    await evaluate(
      `document.querySelector('.playlist-link-page__login').click()`,
    );
    await wait(`!!document.querySelector('#login-email')`);
    await evaluate(
      `(()=>{for(const [id,value] of [['login-email',${JSON.stringify(fixture.people.owner.email)}],['login-password','abcdef']]){const e=document.getElementById(id);e.value=value;e.dispatchEvent(new Event('input',{bubbles:true}));}document.querySelector('.auth-card button[type=submit]').click()})()`,
    );
    await wait(
      `location.pathname.startsWith('/@') && !!document.querySelector('.playlist-contents__header')`,
    );
    check(
      await evaluate(
        `new URLSearchParams(location.search).get('list')==='${fixture.playlists.empty_private}' && document.querySelector('.playlist-contents__visibility').textContent==='Приватный плейлист'`,
      ),
      "Firefox private login returns to exact original playlist",
    );
  }
  await send("session.end");
  console.log(
    JSON.stringify(
      {
        status: "pass",
        checks,
        engine: "Firefox",
        screens: ["1920x935", "390x844"],
      },
      null,
      2,
    ),
  );
} finally {
  socket?.close();
  process.kill("SIGTERM");
}
