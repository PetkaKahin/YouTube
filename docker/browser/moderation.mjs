import assert from "node:assert/strict";
import { createServer, request as httpRequest } from "node:http";
import { readFile } from "node:fs/promises";
import { browser, pause } from "./cdp.mjs";
import { firefoxBrowser } from "./bidi.mjs";
let fixture;
for (let i = 0; i < 900; i++) {
  try {
    fixture = JSON.parse(await readFile("/work/watch-fixture.json", "utf8"));
    if (fixture.reports) break;
  } catch {}
  await pause(100);
}
if (!fixture?.reports)
  throw new Error("Moderation fixture did not become ready");
const firefox = process.argv.includes("--firefox");
let failure = 0,
  slow = false;
const calls = [],
  sockets = new Set();
const server = createServer(async (req, res) => {
  try {
    const api = req.url.startsWith("/api/"),
      report = req.url.endsWith("/decision") && req.method === "PUT";
    if (api && req.url.includes("/moderation/")) calls.push({ read: req.url });
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

await new Promise((resolve) => server.listen(4188, "127.0.0.1", resolve));
const c = firefox ? await firefoxBrowser() : await browser(),
  base = "http://127.0.0.1:4188",
  v = fixture.videos,
  r = fixture.reports;
const click = c.click;
c.click = async (selector) => {
  await c.wait(`!!document.querySelector(${JSON.stringify(selector)})`);
  await c.evaluate(
    `document.querySelector(${JSON.stringify(selector)}).focus()`,
  );
  await click(selector);
};
let assertions = 0;
const check = (x, message) => {
  assert.ok(x, message);
  assertions++;
};
const control = async (action) => {
  const auth = await fetch(
    "http://youtube-frontend-upload-api:8181/v1/auth/me",
  );
  const cookies = auth.headers.getSetCookie().map((x) => x.split(";")[0]);
  const csrf = decodeURIComponent(
    cookies.find((x) => x.startsWith("csrftoken=")).slice(10),
  );
  const response = await fetch(
    "http://youtube-frontend-upload-api:8181/__fixture/" + action,
    {
      method: "POST",
      headers: { cookie: cookies.join("; "), "x-csrftoken": csrf },
    },
  );
  assert.equal(response.status, 200);
};
async function navigate(path) {
  await c.navigate(base + path);
}
async function login(who = "stranger", to = "/moderation") {
  await navigate("/login?returnTo=" + encodeURIComponent(to));
  await c.fill("#login-email", fixture.people[who].email);
  await c.fill("#login-password", "abcdef");
  await c.click(".auth-card button[type=submit]");
  await c.wait(`!document.querySelector('.auth-card')`);
}
async function context(id = r.private) {
  await navigate("/moderation/" + id);
  await c.wait(`!!document.querySelector('.report-context')`);
}
async function privateMaterial() {
  await context();
  await c.wait(`!!document.querySelector('.moderation-material .player')`);
}
const writes = () => calls.filter((x) => x.body);
const queryClient = `Object.values(document.querySelector('#app').__vue_app__._context.provides).find(x=>typeof x?.getQueryCache==='function')`;
async function refetch() {
  await c.evaluate(
    `(async()=>{const client=${queryClient};await client.refetchQueries({queryKey:['moderation']});})()`,
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
      });
}
try {
  await control("moderator");
  await c.viewport(1920, 935);
  await navigate("/moderation");
  await c.wait(
    `document.querySelector('.moderation .page-state h1')?.textContent==='Войдите для модерации'`,
  );
  check(calls.length === 0, "Guest does not request staff data");
  await login("invited");
  await c.wait(
    `document.querySelector('.moderation .page-state h1')?.textContent==='Раздел недоступен'`,
  );
  check(!calls.some((x) => x.read), "Ordinary user denied before requests");
  await navigate("/");
  await c.wait(`!!document.querySelector('.avatar-button')`);
  await c.click(".avatar-button");
  check(
    !(await c.evaluate(
      `!!document.querySelector('.account-menu__moderation')`,
    )),
    "No staff menu for user",
  );
  await login();
  await c.wait(`!!document.querySelector('.moderation-table')`);
  for (let i = 0; i < 4; i++) {
    await c.evaluate(
      `window.scrollTo(0,document.body.scrollHeight);document.querySelector('.moderation-more button')?.click()`,
    );
    await pause(150);
  }
  check(
    await c.evaluate(
      `document.querySelectorAll('.moderation-table tbody tr').length>20`,
    ),
    "Queue paginates beyond 20",
  );
  check(
    await c.evaluate(
      `(()=>{const a=[...document.querySelectorAll('.moderation-table tbody tr td:first-child')].map(x=>x.textContent);return new Set(a).size===a.length})()`,
    ),
    "No duplicate report IDs",
  );
  check(
    calls.some((x) => x.read?.includes("cursor=")),
    "Server cursor used",
  );
  check(
    await c.evaluate(
      `document.querySelector('.moderation-table tbody tr a').getAttribute('href').includes('900719925474099')`,
    ),
    "Exact large report ID links",
  );
  const publicReport = await c.evaluate(
    `document.querySelector('.moderation-table tbody tr a').getAttribute('href').split('/').at(-1)`,
  );
  await c.screenshot("moderation-queue-" + (firefox ? "firefox" : "chromium"));
  await navigate("/");
  await c.wait(`!!document.querySelector('.avatar-button')`);
  await c.click(".avatar-button");
  check(
    await c.evaluate(`!!document.querySelector('.account-menu__moderation')`),
    "Moderator menu entry",
  );
  check(
    (await c.evaluate(
      `(async()=>{const r=await fetch('/api/v1/videos/${v.private}');return r.status})()`,
    )) === 404,
    "Staff role alone does not grant ordinary private access",
  );
  await control("repeat");
  await privateMaterial();
  check(
    calls.some(
      (x) => x.read === `/api/v1/moderation/reports/${r.private}/video`,
    ),
    "Private material bound to report",
  );
  check(
    await c.evaluate(
      `document.querySelector('.service-notice').textContent.includes('Служебный просмотр') && !window.frontendInject`,
    ),
    "Service context clearly marked and metadata safe",
  );
  // Exercise real master, segments and decoding with the reused HLS controls.
  await c.wait(`!document.querySelector('.player__start').disabled`, 30000);
  await c.evaluate(
    `document.querySelector('.moderation-material video').muted=true`,
  );
  if (firefox) {
    await c.evaluate(`document.querySelector('.player__start').focus()`);
    await key("Enter", "\uE007", 13);
  } else {
    await c.evaluate(
      `document.querySelector('.player__start').scrollIntoView({block:'center'})`,
    );
    const point = await c.evaluate(
      `(()=>{const r=document.querySelector('.player__start').getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()`,
    );
    for (const type of ["mousePressed", "mouseReleased"])
      await c.send("Input.dispatchMouseEvent", {
        type,
        button: "left",
        clickCount: 1,
        ...point,
      });
  }
  await c.wait(
    `document.querySelector('.moderation-material video').currentTime>0.2`,
    30000,
  );
  check(
    calls.some((x) => x.read?.includes("/hls/tracks/")),
    "Service HLS requests remain under report",
  );
  check(
    !calls.some((x) => x.read?.includes("/videos/")),
    "No ordinary media endpoint for service playback",
  );
  await c.screenshot(
    "moderation-private-" + (firefox ? "firefox" : "chromium"),
  );
  await c.click(".moderation-actions button:nth-child(2)");
  check(
    await c.evaluate(`document.querySelector('.moderation-confirm').disabled`),
    "Hide requires reason",
  );
  await key("Escape", "\uE00C", 27);
  check(
    await c.evaluate(
      `!document.querySelector('.studio-dialog').open && document.activeElement.matches('.moderation-actions button:nth-child(2)')`,
    ),
    "Cancel restores decision focus",
  );
  await c.click(".moderation-actions button:first-child");
  await control("repeat");
  await c.click(".moderation-confirm");
  await c.wait(
    `document.querySelector('.report-context > .form-error')?.textContent.includes('Жалоба изменилась')`,
  );
  check(
    await c.evaluate(
      `!!document.querySelector('.moderation-actions') && !document.querySelector('.moderation-notice')`,
    ),
    "Real stale revision 409 refreshes instead of claiming success",
  );
  await c.click(".moderation-actions button:first-child");
  failure = 503;
  await c.click(".moderation-confirm");
  await c.wait(
    `!!document.querySelector('#moderation-decision-form .form-error')`,
  );
  check(
    await c.evaluate(
      `document.querySelector('.studio-dialog').open && !!document.querySelector('.moderation-actions')`,
    ),
    "Failed decision leaves pending report intact",
  );
  failure = 0;
  slow = true;
  const before = writes().length;
  await c.evaluate(
    `(()=>{const form=document.querySelector('#moderation-decision-form');form.requestSubmit();form.requestSubmit()})()`,
  );
  await c.wait(`document.querySelector('.moderation-confirm').disabled`);
  await c.wait(`!!document.querySelector('.moderation-notice')`);
  slow = false;
  check(writes().length === before + 1, "One write on double confirm");
  check(
    await c.evaluate(
      `!!document.querySelector('.moderation-decision') && !document.querySelector('.moderation-material') && !document.querySelector('.moderation-actions')`,
    ),
    "Dismiss stops playback and records server decision",
  );
  await control("repeat");
  await context();
  await c.wait(`!!document.querySelector('.moderation-actions')`);
  check(true, "Repeated reviewed complaint opens new revision");
  await control("user");
  await refetch();
  await c.wait(
    `document.querySelector('.moderation .page-state h1')?.textContent==='Раздел недоступен'`,
  );
  check(
    !(await c.evaluate(
      `!!document.querySelector('.report-context') || !!document.querySelector('.moderation-material video')`,
    )),
    "Real role revocation removes private context and player",
  );
  check(
    await c.evaluate(
      `(()=>{const client=${queryClient};return !client.getQueryCache().getAll().some(q=>q.queryKey[0]==='moderation'&&q.state.data)})()`,
    ),
    "Role refusal purges staff cache",
  );
  await control("admin");
  await login();
  await c.wait(`!!document.querySelector('.moderation-table')`);
  check(true, "Admin can open queue");
  await context(publicReport);
  await c.wait(`!!document.querySelector('.moderation-actions')`);
  await c.click(".moderation-actions button:nth-child(2)");
  await c.evaluate(
    `(()=>{const s=document.querySelector('#hide-reason');s.value='spam';s.dispatchEvent(new Event('change',{bubbles:true}))})()`,
  );
  await c.click(".moderation-confirm");
  await c.wait(
    `document.querySelector('.moderation-notice')?.textContent.includes('Видео скрыто')`,
  );
  check(
    await c.evaluate(
      `document.querySelector('.moderation-decision').textContent.includes('Спам')`,
    ),
    "Hide confirmed with reason",
  );
  const status = await c.evaluate(
    `(async()=>{const r=await fetch('/api/v1/videos/${v.public}');return r.status})()`,
  );
  check(status === 404, "Hidden video denied in ordinary watch API");
  check(
    writes().every((x) => x.csrf),
    "Decision writes include CSRF",
  );
  await control("repeat");
  await privateMaterial();
  for (const [width, height] of [
    [390, 844],
    [320, 568],
  ]) {
    await c.viewport(width, height, true);
    check(
      await c.evaluate(`document.documentElement.scrollWidth<=innerWidth`),
      "Mobile content fits " + width,
    );
    await c.click(".moderation-actions button:nth-child(2)");
    check(
      await c.evaluate(
        `(()=>{const r=document.querySelector('.studio-dialog').getBoundingClientRect();return r.left>=0&&r.right<=innerWidth&&r.bottom<=innerHeight})()`,
      ),
      "Phone confirmation fits",
    );
    await c.screenshot(
      "moderation-phone-" + (firefox ? "firefox" : "chromium") + "-" + width,
    );
    await key("Escape", "\uE00C", 27);
  }
  await c.viewport(1920, 935);
  await navigate("/moderation/9223372036854775808");
  await c.wait(
    `document.querySelector('.moderation .page-state h1')?.textContent==='Жалоба недоступна'`,
  );
  check(
    !(await c.evaluate(`!!document.querySelector('.report-context')`)),
    "Invalid report ID has no old context",
  );
  await privateMaterial();
  slow = true;
  await c.click(".moderation-actions button:first-child");
  await c.click(".moderation-confirm");
  await c.wait(`document.querySelector('.moderation-confirm').disabled`);
  await c.evaluate(
    `document.querySelector('#app').__vue_app__.config.globalProperties.$router.push('/')`,
  );
  await c.wait(`location.pathname==='/'`);
  await pause(800);
  slow = false;
  check(
    !(await c.evaluate(
      `!!document.querySelector('.report-context')||!!document.querySelector('.moderation-notice')`,
    )),
    "Late decision cannot restore previous screen",
  );
  await control("repeat");
  await privateMaterial();
  await c.evaluate(
    `(async()=>{const csrf=decodeURIComponent(document.cookie.split('; ').find(x=>x.startsWith('csrftoken=')).slice(10));await fetch('/api/v1/auth/logout',{method:'POST',headers:{'x-csrftoken':csrf}});})()`,
  );
  await refetch();
  await c.wait(`!document.querySelector('.report-context')`);
  check(
    !(await c.evaluate(
      `!!document.querySelector('.moderation-material video')`,
    )),
    "Expired session tears down private playback",
  );
  check(c.errors.length === 0, "No runtime exceptions");
  console.log(
    JSON.stringify(
      {
        status: "pass",
        assertions,
        engine: firefox ? "Firefox" : "Chromium",
        backend:
          "real isolated API/PostgreSQL/Nginx/RustFS/roles/revisions/HLS",
      },
      null,
      2,
    ),
  );
} catch (error) {
  await c.screenshot(
    "moderation-failure-" + (firefox ? "firefox" : "chromium"),
  );
  console.error({
    runtime: c.errors,
    player: await c.evaluate(
      `(()=>{const v=document.querySelector('video');return {focus:document.activeElement?.outerHTML.slice(0,250),dialogOpen:document.querySelector('.studio-dialog')?.open,error:document.querySelector('.player__error')?.textContent,notice:document.querySelector('.player__notice')?.textContent,paused:v?.paused,ready:v?.readyState,code:v?.error?.code,duration:v?.duration}})()`,
    ),
  });
  throw error;
} finally {
  await c.close();
  for (const socket of sockets) socket.destroy();
  server.closeAllConnections();
  server.close();
}
