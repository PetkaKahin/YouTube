import assert from 'node:assert/strict';
import { createServer, request as httpRequest } from 'node:http';
import { readFile } from 'node:fs/promises';
import { browser, pause } from './cdp.mjs';
import { firefoxBrowser } from './bidi.mjs';
let fixture;
for (let i = 0; i < 1200; i++) {
  try {
    fixture = JSON.parse(await readFile('/work/watch-fixture.json', 'utf8'));
    if (fixture.videos?.silent) break;
  } catch {}
  await pause(100);
}
if (!fixture?.videos?.silent) throw Error('Logout fixture not ready');
const firefox = process.argv.includes('--firefox'),
  v = fixture.videos;
const calls = [],
  held = new Set(),
  heldMe = new Set(),
  heldOwn = new Set(),
  sockets = new Set();
let holdBefore = false,
  holdAfter = false,
  holdMe = false,
  holdOwn = false,
  refusal = 0,
  disconnectBefore = false,
  disconnectAfter = false,
  falseAck = false,
  dropCsrf = false,
  meFailure = false;
function release(set = held) {
  for (const resume of set) resume();
  set.clear();
}
const server = createServer(async (req, res) => {
  try {
    const logout = req.url === '/api/v1/auth/logout' && req.method === 'POST',
      me = req.url === '/api/v1/auth/me',
      own = req.url === '/api/v1/videos/mine',
      api = req.url.startsWith('/api/');
    const call = {
      path: req.url,
      method: req.method,
      status: undefined,
      csrf: !!req.headers['x-csrftoken'],
      bytes: 0,
    };
    if (api) calls.push(call);
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    call.bytes = chunks.reduce((n, x) => n + x.length, 0);
    if (logout && holdBefore) await new Promise((resolve) => held.add(resolve));
    if ((logout && refusal) || (me && meFailure)) {
      call.status = 503;
      res
        .writeHead(503, { 'content-type': 'application/problem+json' })
        .end(
          '{"code":"unavailable","detail":"<img src=x onerror=window.logoutInjection=1>"}',
        );
      return;
    }
    if (logout && disconnectBefore) {
      call.status = 0;
      res.destroy();
      return;
    }
    if (logout && falseAck) {
      call.status = 200;
      res.writeHead(200, { 'content-type': 'application/json' }).end('{}');
      return;
    }
    const headers = new Headers();
    for (const name of ['cookie', 'content-type', 'x-csrftoken', 'range'])
      if (req.headers[name]) headers.set(name, req.headers[name]);
    if (logout && dropCsrf) headers.delete('x-csrftoken');
    const response = await fetch(
      (api
        ? 'http://youtube-frontend-upload-gateway:8080'
        : 'http://node:5173') + req.url,
      {
        method: req.method,
        headers,
        body: chunks.length ? Buffer.concat(chunks) : undefined,
        redirect: 'manual',
      },
    );
    call.status = response.status;
    const body = Buffer.from(await response.arrayBuffer());
    if (me && holdMe) {
      holdMe = false;
      await new Promise((resolve) => heldMe.add(resolve));
    }
    if (own && holdOwn) {
      holdOwn = false;
      await new Promise((resolve) => heldOwn.add(resolve));
    }
    if (logout && holdAfter) await new Promise((resolve) => held.add(resolve));
    if (logout && disconnectAfter) {
      res.destroy();
      return;
    }
    res.statusCode = response.status;
    for (const name of ['content-type', 'location', 'cache-control'])
      if (response.headers.has(name))
        res.setHeader(name, response.headers.get(name));
    if (response.headers.getSetCookie().length)
      res.setHeader('set-cookie', response.headers.getSetCookie());
    res.end(body);
  } catch {
    res
      .writeHead(502, { 'content-type': 'application/problem+json' })
      .end('{"code":"unavailable"}');
  }
});
server.on('upgrade', (req, socket, head) => {
  const upstream = httpRequest({
    hostname: 'node',
    port: 5173,
    path: req.url,
    headers: req.headers,
  });
  upstream.on('upgrade', (response, peer, peerHead) => {
    sockets.add(socket);
    sockets.add(peer);
    socket.on('close', () => {
      sockets.delete(socket);
      peer.destroy();
    });
    peer.on('close', () => {
      sockets.delete(peer);
      socket.destroy();
    });
    socket.on('error', () => peer.destroy());
    peer.on('error', () => socket.destroy());
    socket.write(
      'HTTP/1.1 101 Switching Protocols\r\n' +
        Object.entries(response.headers)
          .map(([key, value]) => `${key}: ${value}`)
          .join('\r\n') +
        '\r\n\r\n',
    );
    if (head.length) peer.write(head);
    if (peerHead.length) socket.write(peerHead);
    socket.pipe(peer);
    peer.pipe(socket);
  });
  upstream.on('error', () => socket.destroy());
  upstream.end();
});

await new Promise((resolve) => server.listen(4189, '127.0.0.1', resolve));
const c = firefox ? await firefoxBrowser() : await browser(),
  base = 'http://127.0.0.1:4189',
  studio = '/studio/content/videos';
const store =
  "document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('session-ui')";
const client =
  "Object.values(document.querySelector('#app').__vue_app__._context.provides).find(x=>typeof x?.getQueryCache==='function')";
let assertions = 0;
const check = (value, message) => {
  assert.ok(value, message);
  assertions++;
};
const safeErrors = () =>
  c.errors.map((x) => x.replace(/https?:\/\/\S+/g, '[URL redacted]'));
const logoutCalls = () => calls.filter((x) => x.path === '/api/v1/auth/logout');
async function until(fn) {
  for (let i = 0; i < 300; i++) {
    if (await fn()) return;
    await pause(100);
  }
  throw Error('Logout condition timed out');
}
async function api(path, method = 'GET') {
  return JSON.parse(
    await c.evaluate(
      `(async()=>{const token=document.cookie.split('; ').find(x=>x.startsWith('csrftoken='));const r=await fetch('/api/v1'+${JSON.stringify(path)},{method:${JSON.stringify(method)},headers:token?{'x-csrftoken':decodeURIComponent(token.slice(10))}:{}});return JSON.stringify({status:r.status,data:r.status===204?null:await r.json()});})()`,
    ),
  );
}
async function spa(path) {
  await c.evaluate(
    `(async()=>{await document.querySelector('#app').__vue_app__.config.globalProperties.$router.push(${JSON.stringify(path)});return true;})()`,
  );
}
async function login(target = '/', person = 'owner') {
  await c.navigate(base + '/login?returnTo=' + encodeURIComponent(target));
  await c.fill('#login-email', fixture.people[person].email);
  await c.fill('#login-password', 'abcdef');
  await c.click('.auth-card button[type=submit]');
  await c.wait(
    `location.pathname===${JSON.stringify(target.split('?')[0])} && !document.querySelector('#login-password') && !!${store}.account`,
  );
}
async function openMenu() {
  const isStudio = await c.evaluate(
    "!!document.querySelector('.studio-shell')",
  );
  await c.click(
    isStudio ? 'button.studio-avatar' : '.masthead button.avatar-button',
  );
  const selector = isStudio ? '#studio-logout' : '#logout-button';
  await c.wait(`!!document.querySelector('${selector}')`);
  return selector;
}
async function logout() {
  const selector = await openMenu();
  await c.click(selector);
}
async function guest() {
  await c.wait(
    `${store}.account===null && !${store}.changing && !${store}.error`,
  );
}
async function failure(selector) {
  await c.wait(
    `!!document.querySelector('${selector}') && !!document.querySelector('${selector}').parentElement.querySelector('[role=alert]') && document.activeElement.id==='${selector.slice(1)}'`,
  );
}
async function oldCacheGone(scope) {
  return c.evaluate(
    `${client}.getQueryCache().getAll().filter(q=>q.queryKey.includes(${JSON.stringify(scope)})).every(q=>q.state.data===undefined)`,
  );
}
async function table() {
  await c.wait(
    "document.querySelectorAll('.own-video-table tbody tr').length>0",
  );
}
async function player() {
  await c.wait(
    "document.querySelector('.player__start')?.disabled===false && !!document.querySelector('.watch-page__metadata')",
  );
}
async function enter() {
  if (firefox) {
    await c.key('\uE007');
    return;
  }
  await c.send('Input.dispatchKeyEvent', {
    type: 'keyDown',
    key: 'Enter',
    windowsVirtualKeyCode: 13,
    text: '\r',
  });
  await c.send('Input.dispatchKeyEvent', {
    type: 'keyUp',
    key: 'Enter',
    windowsVirtualKeyCode: 13,
  });
}
try {
  await c.viewport(1280, 900);
  if (!firefox)
    await c.send('Emulation.setFocusEmulationEnabled', { enabled: true });
  await login();
  await c.wait("!!document.querySelector('.masthead button.avatar-button')");
  holdBefore = true;
  const before = logoutCalls().length,
    scope = await c.evaluate(`${store}.scope`);
  await logout();
  await until(() => held.size > 0);
  check(
    await c.evaluate(
      `${store}.changing && !document.querySelector('.masthead button.avatar-button') && !!document.querySelector('.account-skeleton')`,
    ),
    'pending logout hides personal UI without confirmed guest state',
  );
  check(
    (await api('/auth/me')).status === 200,
    'before logout reaches API the real server session is still active',
  );
  await c.evaluate(`for(let i=0;i<4;i++)void ${store}.signOut()`);
  check(
    logoutCalls().length === before + 1,
    'duplicate signOut while pending sends one POST',
  );
  holdBefore = false;
  release();
  await guest();
  check(
    logoutCalls().at(-1).status === 204 &&
      (await api('/auth/me')).status === 401,
    'real 204 logout ends server session',
  );
  check(
    await oldCacheGone(scope),
    'successful logout removes data from old account scope',
  );
  await c.wait('document.activeElement.matches(\'a[href^="/login"]\')');
  check(true, 'ordinary shell returns focus to guest login action');

  await login(studio);
  await table();
  refusal = 503;
  await logout();
  await failure('#studio-logout');
  check(
    (await api('/auth/me')).status === 200 &&
      (await c.evaluate(
        `!!${store}.account && !!document.querySelector('#studio-navigation') && !window.logoutInjection`,
      )),
    '503 before logout retains verified account and shows Studio error',
  );
  check(
    await c.evaluate(
      "document.querySelector('#studio-account [role=alert]').textContent.includes('Не удалось')",
    ),
    'Studio failure is visible in reopened account menu',
  );
  refusal = 0;
  await c.click('#studio-logout');
  await guest();
  await c.wait(
    "document.querySelector('.page-state h1')?.textContent==='Войдите в YouTube Studio'",
  );
  check(
    !(await c.evaluate(
      "!!document.querySelector('.own-video-table') || !!document.querySelector('#studio-navigation')",
    )),
    'successful Studio retry removes private catalogue and navigation',
  );
  await c.wait('document.activeElement.matches(\'a[href^="/login"]\')');
  check(true, 'Studio successful retry focuses its login action');

  await login();
  disconnectBefore = true;
  await logout();
  await failure('#logout-button');
  check(
    (await api('/auth/me')).status === 200,
    'network loss before commit rechecks and preserves real active session',
  );
  disconnectBefore = false;
  dropCsrf = true;
  await c.click('#logout-button');
  await failure('#logout-button');
  check(
    logoutCalls().at(-1).status === 403 &&
      (await api('/auth/me')).status === 200,
    'real CSRF refusal does not become successful logout',
  );
  dropCsrf = false;
  falseAck = true;
  await c.click('#logout-button');
  await failure('#logout-button');
  check(
    logoutCalls().at(-1).status === 200 &&
      (await c.evaluate(`!!${store}.account`)),
    'unexpected HTTP 200 cannot falsely confirm logout while me still authenticates',
  );
  falseAck = false;
  await c.click('#logout-button');
  await guest();
  check(
    (await api('/auth/me')).status === 401,
    'explicit retry completes same logout intent',
  );

  await login(studio);
  await table();
  disconnectAfter = true;
  await logout();
  await guest();
  disconnectAfter = false;
  check(
    logoutCalls().at(-1).status === 204 &&
      (await api('/auth/me')).status === 401,
    'lost response after real commit is resolved by confirmed unauthenticated me',
  );
  check(
    !(await c.evaluate("!!document.querySelector('.own-video-table')")),
    'confirmed committed logout never restores old private catalogue',
  );

  await login();
  refusal = 503;
  meFailure = true;
  await logout();
  await c.wait(
    `!${store}.changing && !!${store}.error && !!document.querySelector('.session-error button')`,
  );
  check(
    await c.evaluate(
      `!${store}.account && !document.querySelector('.avatar-button') && !document.querySelector('.masthead .sign-in')`,
    ),
    'unknown logout outcome with failed identity check is neither old account nor confirmed guest',
  );
  refusal = 0;
  meFailure = false;
  await c.click('.session-error button');
  await c.wait("!!document.querySelector('.masthead button.avatar-button')");
  check(
    (await api('/auth/me')).status === 200,
    'manual identity retry recovers actual still-active session',
  );
  await logout();
  await guest();

  await login('/watch?v=' + v.private, 'invited');
  await player();
  await c.evaluate(
    "document.querySelector('.player video').muted=true;document.querySelector('.player__start').focus()",
  );
  await enter();
  await c.wait(
    "!document.querySelector('.player video').paused && document.querySelector('.player video').currentTime>0",
  );
  await c.evaluate(
    "window.logoutPlayer=document.querySelector('.player video')",
  );
  const privateScope = await c.evaluate(`${store}.scope`);
  holdBefore = true;
  await logout();
  await until(() => held.size > 0);
  check(
    await c.evaluate(
      "window.logoutPlayer.paused && !window.logoutPlayer.getAttribute('src') && !window.logoutPlayer.isConnected && !document.querySelector('.watch-page__metadata')",
    ),
    'starting logout destroys and clears actively playing private media before response',
  );
  holdBefore = false;
  release();
  await guest();
  await c.wait(
    "document.querySelector('.watch-page .page-state h1')?.textContent==='Видео недоступно'",
  );
  check(
    (await api('/videos/' + v.private)).status === 404 &&
      (await oldCacheGone(privateScope)),
    'after logout private metadata/API and previous cache remain unavailable',
  );

  await login('/watch?v=' + v.public, 'invited');
  await player();
  await logout();
  await guest();
  await player();
  check(
    (await api('/videos/' + v.public)).status === 200,
    'public video remains viewable as guest after logout',
  );

  await login();
  await spa(studio);
  await table();
  await logout();
  await guest();
  await c.evaluate('history.back()');
  await c.wait("location.pathname==='/'");
  await c.evaluate('history.forward()');
  await c.wait(`location.pathname==='${studio}'`);
  await c.wait(
    "document.querySelector('.page-state h1')?.textContent==='Войдите в YouTube Studio'",
  );
  check(
    !(await c.evaluate("!!document.querySelector('.own-video-table')")),
    'Back/Forward cannot restore signed-out private catalogue',
  );
  await c.reload();
  await c.wait(
    "document.querySelector('.page-state h1')?.textContent==='Войдите в YouTube Studio'",
  );
  check(
    (await api('/auth/me')).status === 401,
    'reload requires new login after confirmed logout',
  );

  await login(studio);
  await table();
  const oldScope = await c.evaluate(`${store}.scope`);
  holdOwn = true;
  await c.evaluate(
    `void ${client}.refetchQueries({queryKey:['studio-videos'],type:'active'})`,
  );
  await until(() => heldOwn.size > 0);
  holdMe = true;
  await c.evaluate(`void ${store}.refresh()`);
  await until(() => heldMe.size > 0);
  await logout();
  await guest();
  release(heldMe);
  release(heldOwn);
  check(
    (await oldCacheGone(oldScope)) &&
      (await api('/auth/me')).status === 401 &&
      !(await c.evaluate("!!document.querySelector('.own-video-table')")),
    'late identity and private responses cannot revive signed-out account or cookie',
  );

  await login();
  holdBefore = true;
  await logout();
  await until(() => held.size > 0);
  await spa('/results?search_query=logout-late');
  await c.evaluate("document.querySelector('.search-form input').focus()");
  holdBefore = false;
  release();
  await guest();
  check(
    await c.evaluate(
      "location.pathname==='/results' && document.activeElement.matches('.search-form input')",
    ),
    'late success cannot steal focus or redirect another screen',
  );
  await login();
  holdBefore = true;
  refusal = 503;
  await logout();
  await until(() => held.size > 0);
  await spa(studio);
  holdBefore = false;
  release();
  await c.wait(`!${store}.changing && !!${store}.account`);
  await table();
  check(
    !(await c.evaluate(
      "!!document.querySelector('#studio-account [role=alert]')",
    )),
    'late failure from disposed ordinary shell cannot open Studio error or move its focus',
  );
  refusal = 0;
  await logout();
  await guest();

  await c.viewport(390, 844, true);
  await login(studio);
  await table();
  refusal = 503;
  await openMenu();
  await c.evaluate("document.querySelector('#studio-logout').focus()");
  await enter();
  await failure('#studio-logout');
  check(
    await c.evaluate("document.activeElement.id==='studio-logout'"),
    'phone keyboard failure restores focused retry action',
  );
  refusal = 0;
  await enter();
  await guest();
  await c.wait('document.activeElement.matches(\'a[href^="/login"]\')');
  check(
    (await api('/auth/me')).status === 401,
    'phone keyboard retry confirms server logout',
  );
  check(
    logoutCalls().every((x) => x.csrf && x.bytes === 0),
    'logout commands contain CSRF and no user ID, role or credential payload',
  );
  const errors = safeErrors().filter(
    (x) =>
      !(
        firefox &&
        x.includes('downloadable font: download failed') &&
        x.includes('status=2152398850')
      ),
  );
  check(
    errors.length === 0,
    'no unexpected runtime errors: ' + errors.join(' | '),
  );
  console.log(
    JSON.stringify({
      profile: 'logout',
      browser: firefox ? 'Firefox' : 'Chromium',
      assertions,
      status: 'pass',
    }),
  );
} catch (error) {
  console.error(
    JSON.stringify({
      profile: 'logout',
      assertions,
      path: await c.evaluate('location.pathname'),
      errors: safeErrors(),
    }),
  );
  throw error;
} finally {
  release();
  release(heldMe);
  release(heldOwn);
  await c.close();
  for (const socket of sockets) socket.destroy();
  server.closeAllConnections();
  await new Promise((resolve) => server.close(resolve));
}
