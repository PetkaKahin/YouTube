import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createServer } from "node:http";
import { browser, pause } from "./cdp.mjs";
const fixture = JSON.parse(await readFile("/work/watch-fixture.json", "utf8"));
let failing = false,
  delay = false,
  writes = 0;
const server = createServer(async (req, res) => {
  try {
    const api = req.url.startsWith("/api/");
    const changing =
      ["POST", "PUT"].includes(req.method) && !req.url.includes("/auth/");
    if (changing) {
      writes++;
      if (delay) await pause(500);
      if (failing) {
        res
          .writeHead(503, { "content-type": "application/problem+json" })
          .end('{"code":"unavailable"}');
        return;
      }
    }
    const headers = new Headers();
    for (const name of ["content-type", "cookie", "x-csrftoken", "range"])
      if (req.headers[name]) headers.set(name, req.headers[name]);
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    const r = await fetch(
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
    res.statusCode = r.status;
    for (const name of ["content-type", "location", "cache-control"])
      if (r.headers.has(name)) res.setHeader(name, r.headers.get(name));
    if (r.headers.getSetCookie().length)
      res.setHeader("set-cookie", r.headers.getSetCookie());
    res.end(Buffer.from(await r.arrayBuffer()));
  } catch {
    res.writeHead(502).end();
  }
});
await new Promise((resolve) => server.listen(4182, "127.0.0.1", resolve));
const c = await browser(),
  base = "http://127.0.0.1:4182",
  v = fixture.videos;
let assertions = 0;
const check = (value, message) => {
  assert.ok(value, message);
  assertions++;
};
async function open(id = v.public) {
  await c.navigate(`${base}/watch?v=${id}`);
  await c.wait(
    `!!document.querySelector('.watch-page h1') && !!document.querySelector('.video-like button')`,
  );
}
async function login(role) {
  await c.navigate(
    `${base}/login?returnTo=${encodeURIComponent(`/watch?v=${v.public}`)}`,
  );
  await c.fill("#login-email", fixture.people[role].email);
  await c.fill("#login-password", "abcdef");
  await c.click(".auth-card button[type=submit]");
  await c.wait(
    `location.pathname==='/watch' && !!document.querySelector('.watch-page h1') && !!document.querySelector('.avatar-button')`,
  );
}
async function submit(text) {
  await c.fill("#new-comment", text);
  await c.evaluate(
    `document.querySelector('#new-comment').dispatchEvent(new FocusEvent('focus'))`,
  );
  await c.click(".discussion__form button[type=submit]");
  await c.wait(
    `document.querySelector('.discussion__notice')?.textContent.includes('опубликован') && document.querySelector('#new-comment')?.value==='' && !document.querySelector('#new-comment')?.disabled`,
  );
  await c.wait(`!!document.querySelector('.comment')`);
}
const like = ".video-like button";
try {
  await c.viewport(1920, 935);
  await open();
  await c.wait(
    `!!document.querySelector('.discussion__guest') && !!document.querySelector('.subscribe button')`,
  );
  await c.click(like);
  await c.wait(`location.pathname==='/login'`);
  check(
    await c.evaluate(
      `new URLSearchParams(location.search).get('returnTo')==='/watch?v=${v.public}'`,
    ),
    "Guest like returns to same video after login",
  );
  await login("stranger");
  await c.wait(
    `!!document.querySelector('#new-comment') && !!document.querySelector('.subscribe button')`,
  );
  await c.wait(`!!document.querySelector('.video-like button[aria-pressed]')`);
  if (
    await c.evaluate(
      `document.querySelector('.video-like button').getAttribute('aria-pressed')==='true'`,
    )
  ) {
    await c.click(like);
    await c.wait(
      `document.querySelector('.video-like button').getAttribute('aria-pressed')==='false'`,
    );
  }
  const beforeWrites = writes;
  delay = true;
  await c.click(like);
  await c.click(like);
  await c.wait(
    `document.querySelector('.video-like button')?.getAttribute('aria-pressed')==='true' && !document.querySelector('.video-like button').disabled`,
  );
  delay = false;
  check(
    writes === beforeWrites + 1,
    "Double like while pending sends one command",
  );
  check(
    await c.evaluate(
      `document.querySelector('.video-like button span').textContent==='1'`,
    ),
    "Confirmed video count is one",
  );
  await c.click(like);
  await c.wait(
    `document.querySelector('.video-like button')?.getAttribute('aria-pressed')==='false'`,
  );
  check(
    await c.evaluate(
      `document.querySelector('.video-like button span').textContent==='0'`,
    ),
    "Unlike returns confirmed zero",
  );
  failing = true;
  await c.click(like);
  await c.wait(`!!document.querySelector('.video-like .form-error')`);
  check(
    await c.evaluate(
      `document.querySelector('.video-like button').getAttribute('aria-pressed')==='false' && document.querySelector('.video-like button span').textContent==='0'`,
    ),
    "Failed like preserves confirmed state",
  );
  failing = false;
  await c.click(".subscribe button");
  await c.wait(
    `document.querySelector('.subscribe button').getAttribute('aria-pressed')==='true'`,
  );
  check(
    await c.evaluate(
      `document.querySelector('.subscribe button').textContent==='Вы подписаны'`,
    ),
    "Subscription confirmed",
  );
  await c.click(".subscribe button");
  await c.wait(
    `document.querySelector('.subscribe button').getAttribute('aria-pressed')==='false'`,
  );
  check(
    await c.evaluate(
      `document.querySelector('.subscribe button').textContent==='Подписаться'`,
    ),
    "Subscription cancelled",
  );
  failing = true;
  await c.click(".subscribe button");
  await c.wait(`!!document.querySelector('.subscribe .form-error')`);
  check(
    await c.evaluate(
      `document.querySelector('.subscribe button').getAttribute('aria-pressed')==='false'`,
    ),
    "Subscription failure does not invent success",
  );
  failing = false;
  await c.fill("#new-comment", " ".repeat(10));
  check(
    await c.evaluate(
      `document.querySelector('.discussion button[type=submit]').disabled`,
    ),
    "Whitespace comment cannot submit",
  );
  await c.fill("#new-comment", "я".repeat(2001));
  check(
    await c.evaluate(
      `document.querySelector('#new-comment').value.length===2001 && document.querySelector('.discussion button[type=submit]').disabled`,
    ),
    "2001 characters retained with explicit limit, never truncated",
  );
  const text = "<img src=x onerror=window.frontendInject=1>" + "я".repeat(1955);
  check([...text].length <= 2000, "Boundary fixture size");
  await submit("я".repeat(2000));
  check(
    await c.evaluate(
      `document.querySelector('.comment__text').textContent.length===2000 && document.querySelector('#new-comment').value===''`,
    ),
    "2000-character comment saved and form cleared after success",
  );
  await submit(text);
  check(
    await c.evaluate(
      `document.querySelector('.comment__text').textContent.includes('<img') && !document.querySelector('.comment__text img') && !window.frontendInject`,
    ),
    "Comment HTML remains text in DOM",
  );
  const firstId = await c.evaluate(
    `document.querySelector('.comment').dataset.commentId`,
  );
  await c.click(".comment .icon-button");
  await c.wait(
    `document.querySelector('.comment .icon-button').getAttribute('aria-pressed')==='true'`,
  );
  check(
    await c.evaluate(
      `document.querySelector('.comment__actions span').textContent==='1'`,
    ),
    "Own comment like accepted",
  );
  await c.click(".comment .icon-button");
  await c.wait(
    `document.querySelector('.comment .icon-button').getAttribute('aria-pressed')==='false'`,
  );
  check(
    await c.evaluate(
      `document.querySelector('.comment__actions span').textContent==='0'`,
    ),
    "Comment unlike accepted",
  );
  failing = true;
  await c.click(".comment .icon-button");
  await c.wait(`!!document.querySelector('.comment .form-error')`);
  check(
    await c.evaluate(
      `document.querySelector('.comment .icon-button').getAttribute('aria-pressed')==='false'`,
    ),
    "Comment like failure preserves prior state",
  );
  failing = false;
  failing = true;
  await c.fill("#new-comment", "Не отправлен");
  await c.click(".discussion button[type=submit]");
  await c.wait(
    `!!document.querySelector('#comment-validation').textContent && !document.querySelector('#new-comment').disabled`,
  );
  check(
    await c.evaluate(
      `document.querySelector('#new-comment').value==='Не отправлен' && ![...document.querySelectorAll('.comment__text')].some(e=>e.textContent==='Не отправлен') && document.activeElement.id==='new-comment'`,
    ),
    "Failed publication retains draft and focuses error",
  );
  failing = false;
  await c.fill("#new-comment", "");
  // Prepare enough real comments to exercise cursor20 and server sort.
  await c.evaluate(
    `(async()=>{const csrf=decodeURIComponent(document.cookie.split('; ').find(x=>x.startsWith('csrftoken=')).slice(10));for(let i=0;i<21;i++){const r=await fetch('/api/v1/videos/${v.public}/comments',{method:'POST',headers:{'Content-Type':'application/json','X-CSRFToken':csrf},body:JSON.stringify({text:'Порция '+i})});if(!r.ok)throw new Error('Seed comment failed');}})()`,
  );
  await open();
  await c.wait(`document.querySelectorAll('.comment').length===20`);
  await c.click(".discussion__more");
  await c.wait(`document.querySelectorAll('.comment').length>=23`);
  check(
    await c.evaluate(
      `new Set([...document.querySelectorAll('.comment')].map(e=>e.dataset.commentId)).size===document.querySelectorAll('.comment').length`,
    ),
    "Cursor pages contain distinct real comments",
  );
  await c.evaluate(
    `[...document.querySelectorAll('.comment')].find(e=>e.dataset.commentId===${JSON.stringify(firstId)}).querySelector('button[aria-pressed]').click()`,
  );
  await c.wait(
    `[...document.querySelectorAll('.comment')].find(e=>e.dataset.commentId===${JSON.stringify(firstId)}).querySelector('button[aria-pressed]').getAttribute('aria-pressed')==='true'`,
  );
  await c.click('[aria-label="Упорядочить комментарии"]');
  await c.evaluate(
    `[...document.querySelectorAll('.discussion__menu button')].find(e=>e.textContent.includes('популярные')).click()`,
  );
  await c.wait(
    `document.querySelector('.comment')?.dataset.commentId===${JSON.stringify(firstId)}`,
  );
  check(
    await c.evaluate(
      `document.querySelector('.comment__actions span').textContent==='1'`,
    ),
    "Popular order comes from entire server list",
  );
  await c.screenshot("watch-comments-desktop-local");
  // Late mutation must not leak into another video.
  delay = true;
  await c.click(like);
  await c.evaluate(`document.querySelector('.masthead__brand').click()`);
  await c.wait(`location.pathname==='/'`);
  await pause(650);
  delay = false;
  await open(v.silent);
  await c.wait(
    `document.querySelector('.video-like button')?.getAttribute('aria-pressed')==='false'`,
  );
  check(
    await c.evaluate(
      `document.querySelector('.video-like button span').textContent==='0'`,
    ),
    "Late previous-video like does not change new video",
  );
  await login("owner");
  await c.wait(`!!document.querySelector('#new-comment')`);
  check(
    await c.evaluate(`!document.querySelector('.subscribe button.button')`),
    "Owner is not offered self subscription",
  );
  await open(v.private);
  check(
    await c.evaluate(
      `!document.querySelector('.discussion') && !!document.querySelector('.watch-page__private-discussion')`,
    ),
    "Private discussion closed for owner",
  );
  await c.viewport(390, 844, true);
  await open();
  await c.wait(`!!document.querySelector('#new-comment')`);
  check(
    await c.evaluate(`document.documentElement.scrollWidth<=innerWidth`),
    "Phone action controls fit",
  );
  await submit("Комментарий с телефона");
  check(
    await c.evaluate(
      `document.querySelector('.comment__text').textContent==='Комментарий с телефона'`,
    ),
    "Phone comment publication",
  );
  await c.click('[aria-label="Упорядочить комментарии"]');
  check(
    await c.evaluate(
      `document.querySelector('.discussion__menu').getBoundingClientRect().right<=innerWidth`,
    ),
    "Phone sort menu fits",
  );
  await c.send("Input.dispatchKeyEvent", {
    type: "keyDown",
    key: "Escape",
    code: "Escape",
  });
  check(
    await c.evaluate(
      `!document.querySelector('.discussion__menu') && document.activeElement.getAttribute('aria-label')==='Упорядочить комментарии'`,
    ),
    "Sort menu Escape restores focus",
  );
  check(c.errors.length === 0, "No uncaught interaction errors");
  console.log(
    JSON.stringify(
      {
        status: "pass",
        assertions,
        engine: "Chromium",
        screens: ["1920x935", "390x844"],
        backend: "real isolated API and PostgreSQL",
      },
      null,
      2,
    ),
  );
} catch (error) {
  await c.screenshot("interactions-failure");
  console.error({ runtimeErrors: c.errors });
  throw error;
} finally {
  await c.close();
  server.closeAllConnections();
  server.close();
}
