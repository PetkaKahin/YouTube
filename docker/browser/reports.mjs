import assert from "node:assert/strict";
import { createServer, request as httpRequest } from "node:http";
import { readFile } from "node:fs/promises";
import { browser, pause } from "./cdp.mjs";
import { firefoxBrowser } from "./bidi.mjs";
const fixture = JSON.parse(await readFile("/work/watch-fixture.json", "utf8"));
const firefox = process.argv.includes("--firefox");
let failure = 0,
  slow = false;
const calls = [],
  sockets = new Set();
const server = createServer(async (req, res) => {
  try {
    const api = req.url.startsWith("/api/"),
      report = req.url.endsWith("/reports") && req.method === "POST";
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    if (report)
      calls.push({
        path: req.url,
        csrf: !!req.headers["x-csrftoken"],
        body: Buffer.concat(chunks).toString(),
      });
    if (report && failure) {
      res
        .writeHead(failure, { "content-type": "application/problem+json" })
        .end('{"code":"unavailable"}');
      return;
    }
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
    if (report && slow) await pause(650);
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

await new Promise((resolve) => server.listen(4187, "127.0.0.1", resolve));
const c = firefox ? await firefoxBrowser() : await browser(),
  base = "http://127.0.0.1:4187",
  v = fixture.videos;
let assertions = 0;
const check = (x, message) => {
  assert.ok(x, message);
  assertions++;
};
async function key(name, value, code) {
  if (firefox) await c.key(value);
  else
    for (const type of ["keyDown", "keyUp"])
      await c.send("Input.dispatchKeyEvent", {
        type,
        key: name,
        code: name,
        windowsVirtualKeyCode: code,
      });
}
async function watchVideo(id = v.public) {
  await c.navigate(base + "/watch?v=" + id);
  await c.wait(`!!document.querySelector('.report-trigger')`);
}
async function open() {
  await c.click(".report-trigger");
  await c.click(".report-menu button");
  await c.wait(`document.querySelector('.report-dialog')?.open`);
}
async function login() {
  await c.fill("#login-email", fixture.people.stranger.email);
  await c.fill("#login-password", "abcdef");
  await c.click(".auth-card button[type=submit]");
  await c.wait(
    `location.pathname==='/watch' && !!document.querySelector('.report-trigger')`,
  );
}
const submit = () => c.click(".report-dialog button[type=submit]");
const select = (value) => c.click(`.report-dialog input[value="${value}"]`);
try {
  await c.viewport(1920, 935);
  await watchVideo();
  await c.click(".report-trigger");
  await c.click(".report-menu button");
  await c.wait(`location.pathname==='/login'`);
  check(calls.length === 0, "Guest enters login without report POST");
  check(
    await c.evaluate(
      `new URLSearchParams(location.search).get('returnTo')===${JSON.stringify("/watch?v=" + v.public)}`,
    ),
    "Login keeps video return URL",
  );
  await login();
  await open();
  check(
    await c.evaluate(
      `document.querySelector('.report-dialog button[type=submit]').disabled`,
    ),
    "No default reason",
  );
  await c.evaluate(
    `document.querySelector('.report-dialog form').requestSubmit()`,
  );
  check(calls.length === 0, "No-choice submit has no request");
  check(
    await c.evaluate(
      `document.querySelectorAll('.report-dialog input[type=radio]').length===6`,
    ),
    "Six agreed reasons",
  );
  await c.screenshot("report-desktop-" + (firefox ? "firefox" : "chromium"));
  await select("spam");
  await key("Escape", "\uE00C", 27);
  check(
    await c.evaluate(
      `!document.querySelector('.report-dialog').open && document.activeElement.classList.contains('report-trigger')`,
    ),
    "Escape restores trigger focus",
  );
  await open();
  check(
    await c.evaluate(`!document.querySelector('.report-dialog input:checked')`),
    "Close clears selection",
  );
  await key("Tab", "\uE004", 9);
  await key("Tab", "\uE004", 9);
  check(
    await c.evaluate(`document.activeElement.closest('dialog')?.open===true`),
    "Tab stays inside dialog",
  );
  await select("spam");
  failure = 503;
  await submit();
  await c.wait(`!!document.querySelector('.report-dialog .form-error')`);
  check(
    await c.evaluate(
      `document.querySelector('.report-dialog input:checked').value==='spam' && !document.querySelector('.report-notice')`,
    ),
    "Failure preserves choice without false success",
  );
  failure = 0;
  slow = true;
  const before = calls.length;
  await c.evaluate(
    `(()=>{const form=document.querySelector('.report-dialog form');form.requestSubmit();form.requestSubmit()})()`,
  );
  await c.wait(
    `document.querySelector('.report-dialog').getAttribute('aria-busy')==='true'`,
  );
  check(
    await c.evaluate(
      `[...document.querySelectorAll('.report-dialog button')].every(b=>b.disabled) && document.querySelector('.report-dialog fieldset').disabled`,
    ),
    "Pending locks controls",
  );
  await key("Escape", "\uE00C", 27);
  check(
    await c.evaluate(`document.querySelector('.report-dialog').open`),
    "Pending Escape cannot duplicate delivery",
  );
  await c.wait(`!!document.querySelector('.report-notice')`);
  slow = false;
  check(calls.length === before + 1, "Double submit sends once");
  check(
    await c.evaluate(
      `document.querySelector('.report-notice').textContent==='Жалоба отправлена на рассмотрение.' && !!document.querySelector('.watch-page h1')`,
    ),
    "Accepted means review and video stays visible",
  );
  for (const value of [
    "sexual_content",
    "violence",
    "harassment",
    "spam",
    "copyright",
    "other",
  ]) {
    await open();
    await select(value);
    await submit();
    await c.wait(`!!document.querySelector('.report-notice')`);
    check(
      !(await c.evaluate(`document.querySelector('.report-dialog').open`)),
      "Reason accepted: " + value,
    );
  }
  check(
    calls.every((x) => x.csrf),
    "All reports include CSRF",
  );
  check(
    calls.every((x) => x.path === `/api/v1/videos/${v.public}/reports`),
    "Exact int64 video path",
  );
  check(
    calls.every((x) => Object.keys(JSON.parse(x.body)).join(",") === "reason"),
    "No reporter or account data sent",
  );
  for (const width of [390, 320]) {
    await c.viewport(width, 844, true);
    await open();
    check(
      await c.evaluate(
        `(()=>{const r=document.querySelector('.report-dialog').getBoundingClientRect();return r.left>=0&&r.right<=innerWidth&&r.bottom<=innerHeight&&document.documentElement.scrollWidth<=innerWidth})()`,
      ),
      "Phone modal fits " + width,
    );
    await c.screenshot(
      "report-phone-" + (firefox ? "firefox" : "chromium") + "-" + width,
    );
    await c.click(".report-dialog button[type=button]");
  }
  await c.viewport(1920, 935);
  await c.navigate(base + "/watch?v=" + v.private);
  await c.wait(
    `document.querySelector('.page-state h1')?.textContent==='Видео недоступно'`,
  );
  check(
    !(await c.evaluate(`!!document.querySelector('.report-trigger')`)),
    "Unavailable private video has no reporting form",
  );
  await watchVideo();
  await open();
  await select("other");
  slow = true;
  await submit();
  await c.wait(
    `document.querySelector('.report-dialog').getAttribute('aria-busy')==='true'`,
  );
  await c.evaluate(
    `document.querySelector('#app').__vue_app__.config.globalProperties.$router.push('/')`,
  );
  await c.wait(`location.pathname==='/'`);
  await pause(800);
  slow = false;
  check(
    !(await c.evaluate(
      `!!document.querySelector('.report-dialog') || !!document.querySelector('.report-notice')`,
    )),
    "Late response does not return old UI",
  );
  await watchVideo();
  await open();
  await select("other");
  failure = 404;
  await submit();
  await c.wait(
    `document.querySelector('.page-state h1')?.textContent==='Видео недоступно'`,
  );
  failure = 0;
  check(
    !(await c.evaluate(`!!document.querySelector('.report-dialog')`)),
    "Access refusal tears down form and player",
  );
  await watchVideo();
  await open();
  await select("other");
  failure = 401;
  await submit();
  await c.wait(`!document.querySelector('.avatar-button')`);
  failure = 0;
  check(
    !(await c.evaluate(
      `document.querySelector('.report-dialog')?.open || !!document.querySelector('.report-notice')`,
    )),
    "Expired session clears selection and success",
  );
  check(c.errors.length === 0, "No runtime exceptions");
  console.log(
    JSON.stringify(
      {
        status: "pass",
        assertions,
        engine: firefox ? "Firefox" : "Chromium",
        backend: "real isolated API/PostgreSQL/Nginx/RustFS",
      },
      null,
      2,
    ),
  );
} catch (error) {
  await c.screenshot("report-failure-" + (firefox ? "firefox" : "chromium"));
  console.error({ runtime: c.errors });
  throw error;
} finally {
  await c.close();
  for (const socket of sockets) socket.destroy();
  server.closeAllConnections();
  server.close();
}
