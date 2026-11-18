import assert from "node:assert/strict";
import { createServer, request as httpRequest } from "node:http";
import { readFile } from "node:fs/promises";
import { browser, pause } from "./cdp.mjs";
import { firefoxBrowser } from "./bidi.mjs";
let fixture;
for (let i = 0; i < 900; i++) {
  try {
    fixture = JSON.parse(await readFile("/work/watch-fixture.json", "utf8"));
    if (fixture.jobs) break;
  } catch {}
  await pause(100);
}
if (!fixture?.jobs) throw new Error("Jobs fixture did not become ready");
const firefox = process.argv.includes("--firefox");
let failure = 0,
  slow = false,
  readFailure = false;
const calls = [],
  sockets = new Set();
const server = createServer(async (req, res) => {
  try {
    const api = req.url.startsWith("/api/"),
      report = req.url.startsWith("/api/v1/admin/") && req.method === "POST";
    if (api && req.url.includes("/admin/")) calls.push({ read: req.url });
    if (readFailure && req.url.startsWith("/api/v1/admin/job-errors")) {
      res
        .writeHead(503, { "content-type": "application/problem+json" })
        .end('{"code":"unavailable"}');
      return;
    }
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

await new Promise((resolve) => server.listen(4189, "127.0.0.1", resolve));
const c = firefox ? await firefoxBrowser() : await browser(),
  base = "http://127.0.0.1:4189";
c.click = async (selector) => {
  await c.wait(`!!document.querySelector(${JSON.stringify(selector)})`);
  await c.evaluate(
    `document.querySelector(${JSON.stringify(selector)}).scrollIntoView({block:'center'})`,
  );
  if (firefox) {
    await c.evaluate(
      `document.querySelector(${JSON.stringify(selector)}).focus()`,
    );
    const checkbox = await c.evaluate(
      `document.querySelector(${JSON.stringify(selector)}).matches('input[type=checkbox]')`,
    );
    await c.key(checkbox ? " " : String.fromCharCode(0xe007));
  } else {
    const point = await c.evaluate(
      `(()=>{const r=document.querySelector(${JSON.stringify(selector)}).getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()`,
    );
    for (const type of ["mousePressed", "mouseReleased"])
      await c.send("Input.dispatchMouseEvent", {
        type,
        button: "left",
        clickCount: 1,
        ...point,
      });
  }
};
let assertions = 0;
const check = (x, message) => {
  assert.ok(x, message);
  assertions++;
};
const writes = () => calls.filter((x) => Object.hasOwn(x, "body"));
const first = fixture.jobs[0],
  second = fixture.jobs[1];
const row = (id) => `.job-history tr[data-job-id="${id}"]`;
const clientExpr = `Object.values(document.querySelector('#app').__vue_app__._context.provides).find(x=>typeof x?.getQueryCache==='function')`;
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
async function control(action) {
  const host = "http://youtube-frontend-upload-api:8181";
  const auth = await fetch(host + "/v1/auth/me");
  const cookies = auth.headers.getSetCookie().map((x) => x.split(";")[0]);
  const csrf = decodeURIComponent(
    cookies.find((x) => x.startsWith("csrftoken=")).slice(10),
  );
  const response = await fetch(host + "/__jobs_fixture/" + action, {
    method: "POST",
    headers: { cookie: cookies.join("; "), "x-csrftoken": csrf },
  });
  assert.equal(response.status, 200);
}
async function navigate(path = "/admin/jobs") {
  await c.navigate(base + path);
}
async function login(who = "owner", path = "/admin/jobs") {
  await navigate("/login?returnTo=" + encodeURIComponent(path));
  await c.fill("#login-email", fixture.people[who].email);
  await c.fill("#login-password", "abcdef");
  await c.click(".auth-card button[type=submit]");
  await c.wait(`!document.querySelector('.auth-card')`);
}
async function loadAll() {
  await c.wait(`!!document.querySelector('.job-history')`);
  for (let i = 0; i < 4; i++) {
    await c.evaluate(
      `window.scrollTo(0,document.body.scrollHeight);document.querySelector('.job-more button')?.click()`,
    );
    await pause(150);
  }
  await c.wait(
    `document.querySelectorAll('.job-history tbody tr').length===42`,
  );
}
async function confirm() {
  await c.wait(`document.querySelector('.studio-dialog').open`);
  const previous = await c.evaluate(`location.search`);
  await c.click(".job-confirm");
  await c.wait(
    `location.search!==${JSON.stringify(previous)} && !!document.querySelector('.job-request-state') && document.querySelector('.job-receipt h2')?.textContent.includes(new URLSearchParams(location.search).get('retry'))`,
  );
}
async function pump() {
  for (let i = 0; i < 15; i++) await control("pump");
  await c.click(".job-status-refresh");
  await c.wait(`!document.querySelector('.job-status-refresh').disabled`);
}
try {
  await control("admin");
  await c.viewport(1920, 935);
  await navigate();
  await c.wait(
    `document.querySelector('.job-admin .page-state h1')?.textContent==='Войдите для управления заданиями'`,
  );
  check(!calls.length, "Guest requests no admin data");
  for (const person of ["invited", "stranger"]) {
    await login(person);
    await c.wait(
      `document.querySelector('.job-admin .page-state h1')?.textContent==='Раздел недоступен'`,
    );
    check(
      !calls.some((x) => x.read),
      "User and moderator request no admin data",
    );
  }
  await navigate("/");
  await c.wait(`!!document.querySelector('.avatar-button')`);
  await c.click(".avatar-button");
  check(
    !(await c.evaluate(`!!document.querySelector('.account-menu__jobs')`)),
    "Moderator has no jobs menu",
  );
  readFailure = true;
  await login();
  await c.wait(
    `document.querySelector('.job-admin .page-state h1')?.textContent==='Не удалось загрузить историю'`,
  );
  check(
    !(await c.evaluate(`!!document.querySelector('.job-history')`)),
    "History failure has no fake rows",
  );
  readFailure = false;
  await c.click(".job-admin .page-state button");
  await loadAll();
  check(
    calls.some((x) => x.read?.includes("cursor=")),
    "History uses cursor 20/20/2",
  );
  check(
    await c.evaluate(
      `new Set([...document.querySelectorAll('.job-history tbody tr')].map(x=>x.dataset.errorId)).size===42 && !window.jobsXss`,
    ),
    "History IDs unique and diagnostics safe text",
  );
  check(
    await c.evaluate(`document.querySelector('.job-selected').disabled`),
    "Selected disabled without jobs",
  );
  await c.click(row(first) + " input");
  check(
    await c.evaluate(
      `[...document.querySelectorAll(${JSON.stringify(row(first) + " input")})].every(x=>x.checked) && document.querySelector('.job-selected').textContent.includes('(1)')`,
    ),
    "Duplicate history entries select one job",
  );
  await c.click(".job-selected");
  await key("Escape", "\uE00C", 27);
  check(
    await c.evaluate(
      `!document.querySelector('.studio-dialog').open && document.activeElement.matches('.job-selected')`,
    ),
    "Cancel returns focus without enqueueing",
  );
  check(!writes().length, "Cancel has no POST");
  await c.screenshot("jobs-history-" + (firefox ? "firefox" : "chromium"));
  await c.click(row(first) + " .job-one");
  await c.wait(`document.querySelector('.studio-dialog').open`);
  check(
    await c.evaluate(
      `document.querySelector('.studio-dialog').textContent.includes(${JSON.stringify(first)})`,
    ),
    "Single confirmation keeps exact bigint",
  );
  failure = 503;
  await c.click(".job-confirm");
  await c.wait(`!!document.querySelector('.studio-dialog .form-error')`);
  check(
    await c.evaluate(
      `document.querySelector('.studio-dialog').open && document.querySelector('.job-selected').textContent.includes('(1)') && !document.querySelector('.job-request-state')`,
    ),
    "POST failure retains selection without receipt",
  );
  failure = 0;
  slow = true;
  const before = writes().length;
  await c.evaluate(
    `(()=>{const b=document.querySelector('.job-confirm');b.click();b.click()})()`,
  );
  await c.wait(`document.querySelector('.job-confirm').disabled`);
  await key("Escape", "\uE00C", 27);
  check(
    await c.evaluate(`document.querySelector('.studio-dialog').open`),
    "Pending prevents closing and repeat",
  );
  await c.wait(`!!document.querySelector('.job-request-state')`);
  slow = false;
  check(writes().length === before + 1, "Double confirm sends one request");
  check(
    writes().at(-1).path === `/api/v1/admin/jobs/${first}/retry`,
    "Single uses real ID endpoint",
  );
  check(
    await c.evaluate(
      `document.querySelector('.job-request-state').textContent.includes('Обрабатывается') && !document.querySelector('.job-selected').textContent.includes('(1)')`,
    ),
    "202 is pending request and clears accepted selection",
  );
  const singlePath = await c.evaluate(`location.pathname+location.search`);
  await c.reload();
  await c.wait(`!!document.querySelector('.job-request-state')`);
  check(
    await c.evaluate(
      `location.pathname+location.search===${JSON.stringify(singlePath)}`,
    ),
    "Receipt survives reload through URL",
  );
  await pump();
  check(
    await c.evaluate(
      `document.querySelector('.job-request-state').textContent.includes('завершена')`,
    ),
    "Real dispatcher completes request",
  );
  await loadAll();
  await c.click(row(first) + " input");
  await c.click(row(second) + " input");
  await c.click(".job-selected");
  await confirm();
  const selectedPost = writes().at(-1);
  check(
    selectedPost.path === "/api/v1/admin/job-retries" &&
      selectedPost.body.includes('"mode":"selected"') &&
      selectedPost.body.includes(first) &&
      selectedPost.body.includes(second),
    "Selected body preserves exact numeric IDs",
  );
  await pump();
  check(
    await c.evaluate(
      `document.querySelector('.job-results').textContent.includes('Пропущено')`,
    ),
    "Overlap shows already requested job as skipped",
  );
  await c.click(".job-all");
  await c.wait(`document.querySelector('.studio-dialog').open`);
  check(
    await c.evaluate(
      `document.querySelector('.studio-dialog').textContent.includes('за пределами загруженной истории')`,
    ),
    "All means global failed jobs, not displayed rows",
  );
  await confirm();
  check(
    writes().at(-1).body === '{"mode":"all"}',
    "All sends no accidental job list",
  );
  await pump();
  for (let i = 0; i < 3; i++) {
    if (await c.evaluate(`!!document.querySelector('.job-results-more')`)) {
      await c.click(".job-results-more");
      await pause(150);
    }
  }
  await c.wait(
    `document.querySelectorAll('.job-results tbody tr').length===40`,
  );
  check(true, "Result cursor 20/20, succeeded jobs excluded from all");
  check(
    await c.evaluate(
      `document.querySelector('.job-results').textContent.includes('Недоступно') && document.querySelectorAll('.job-history tbody tr').length===42`,
    ),
    "Unsupported jobs reported unavailable; history retained",
  );
  const receiptPath = await c.evaluate(`location.pathname+location.search`);
  await c.screenshot("jobs-results-" + (firefox ? "firefox" : "chromium"));
  for (const [width, height] of [
    [390, 844],
    [320, 568],
  ]) {
    await c.viewport(width, height, true);
    check(
      await c.evaluate(`document.documentElement.scrollWidth<=innerWidth`),
      "Phone tables stay inside containers " + width,
    );
    await c.click(".job-all");
    await c.wait(`document.querySelector('.studio-dialog').open`);
    check(
      await c.evaluate(
        `(()=>{const r=document.querySelector('.studio-dialog').getBoundingClientRect();return r.left>=0&&r.right<=innerWidth&&r.bottom<=innerHeight})()`,
      ),
      "Phone confirm fits",
    );
    await c.screenshot(
      "jobs-phone-" + (firefox ? "firefox" : "chromium") + "-" + width,
    );
    await key("Escape", "\uE00C", 27);
  }
  await c.viewport(1920, 935);
  await c.click(".job-all");
  await confirm();
  const cancelledPath = await c.evaluate(`location.pathname+location.search`);
  await control("moderator");
  for (let i = 0; i < 15; i++) await control("pump");
  await c.evaluate(
    `(async()=>{const client=${clientExpr};await client.refetchQueries({queryKey:['job-admin']});})()`,
  );
  await c.wait(
    `document.querySelector('.job-admin .page-state h1')?.textContent==='Раздел недоступен'`,
  );
  check(
    !(await c.evaluate(
      `!!document.querySelector('.job-history') || !!document.querySelector('.job-receipt')`,
    )),
    "Live admin revocation removes history and receipts",
  );
  check(
    await c.evaluate(
      `(()=>{const client=${clientExpr};return !client.getQueryCache().getAll().some(q=>q.queryKey[0]==='job-admin'&&q.state.data)})()`,
    ),
    "Admin cache purged after refusal",
  );
  await control("admin");
  await login("owner", cancelledPath);
  await c.wait(
    `document.querySelector('.job-request-state')?.textContent.includes('отменён')`,
  );
  check(true, "Server cancels remaining work after role revocation");
  await navigate("/");
  await c.wait(`!!document.querySelector('.avatar-button')`);
  await c.click(".avatar-button");
  check(
    await c.evaluate(`!!document.querySelector('.account-menu__jobs')`),
    "Admin jobs menu entry",
  );
  await navigate("/admin/jobs?retry=9223372036854775808");
  await c.wait(
    `document.querySelector('.job-receipt .page-state h1')?.textContent==='Неверный номер запроса'`,
  );
  check(true, "Invalid receipt ID rejected");
  await navigate(receiptPath);
  await loadAll();
  await c.click(row(first) + " .job-one");
  slow = true;
  await c.click(".job-confirm");
  await c.wait(`document.querySelector('.job-confirm').disabled`);
  await c.evaluate(
    `document.querySelector('#app').__vue_app__.config.globalProperties.$router.push('/')`,
  );
  await c.wait(`location.pathname==='/'`);
  await pause(800);
  slow = false;
  check(
    !(await c.evaluate(
      `!!document.querySelector('.job-receipt') || !!document.querySelector('.studio-dialog')`,
    )),
    "Late receipt cannot return old UI",
  );
  await navigate(receiptPath);
  await c.wait(`!!document.querySelector('.job-request-state')`);
  await c.evaluate(
    `(async()=>{const csrf=decodeURIComponent(document.cookie.split('; ').find(x=>x.startsWith('csrftoken=')).slice(10));await fetch('/api/v1/auth/logout',{method:'POST',headers:{'x-csrftoken':csrf}});const client=${clientExpr};await client.refetchQueries({queryKey:['job-admin']});})()`,
  );
  await c.wait(`!document.querySelector('.job-history')`);
  check(
    !(await c.evaluate(`!!document.querySelector('.job-receipt')`)),
    "Expired session clears diagnostics",
  );
  check(
    writes().every((x) => x.csrf),
    "All retry writes include CSRF",
  ); // Firefox reports NS_BINDING_ABORTED when navigation cancels an in-flight font.
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
        backend: "isolated real API/PostgreSQL/queue/dispatcher",
      },
      null,
      2,
    ),
  );
} catch (error) {
  await c.screenshot("jobs-failure-" + (firefox ? "firefox" : "chromium"));
  console.error({ runtime: c.errors });
  throw error;
} finally {
  await c.close();
  for (const socket of sockets) socket.destroy();
  server.closeAllConnections();
  server.close();
}
