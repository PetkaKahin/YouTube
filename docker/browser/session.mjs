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
if (!fixture?.videos?.silent) throw Error('Session fixture not ready');
const firefox = process.argv.includes('--firefox'),
  v = fixture.videos,
  host = 'http://youtube-frontend-upload-gateway:8080/api';
const calls = [],
  heldMe = new Set(),
  heldJobs = new Set(),
  sockets = new Set();
let holdMe = false,
  holdJobs = false,
  meFailure = 0,
  badId = false;
function release(set = heldMe) {
  for (const resume of set) resume();
  set.clear();
}
const server = createServer(async (req, res) => {
  try {
    const me = req.url === '/api/v1/auth/me',
      jobs = req.url === '/api/v1/admin/job-errors',
      api = req.url.startsWith('/api/');
    const call = {
      path: req.url,
      method: req.method,
      status: undefined,
      hasSession:
        req.headers.cookie
          ?.split(';')
          .some((x) => x.trim().startsWith('session=')) ?? false,
      csrf: !!req.headers['x-csrftoken'],
    };
    if (api) calls.push(call);
    if (me && meFailure) {
      call.status = meFailure;
      res
        .writeHead(meFailure, {
          'content-type': 'application/problem+json',
          'cache-control': 'no-store',
        })
        .end('{"code":"unavailable"}');
      return;
    }
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    const headers = new Headers();
    for (const name of ['cookie', 'content-type', 'x-csrftoken', 'range'])
      if (req.headers[name]) headers.set(name, req.headers[name]);
    const upstream = await fetch(
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
    call.status = upstream.status;
    let body = Buffer.from(await upstream.arrayBuffer());
    if (me && badId && upstream.ok)
      body = Buffer.from(body.toString().replace(/"id":\d+/, '"id":0'));
    if (me && holdMe) await new Promise((resolve) => heldMe.add(resolve));
    if (jobs && holdJobs) await new Promise((resolve) => heldJobs.add(resolve));
    res.statusCode = upstream.status;
    for (const name of ['content-type', 'location', 'cache-control'])
      if (upstream.headers.has(name))
        res.setHeader(name, upstream.headers.get(name));
    if (upstream.headers.getSetCookie().length)
      res.setHeader('set-cookie', upstream.headers.getSetCookie());
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
  base = 'http://127.0.0.1:4189';
const client =
  "Object.values(document.querySelector('#app').__vue_app__._context.provides).find(x=>typeof x?.getQueryCache==='function')";
const session =
  "document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('session-ui')";
const studio = '/studio/content/videos';
let assertions = 0;
const check = (value, message) => {
  assert.ok(value, message);
  assertions++;
};
const safeErrors = () =>
  c.errors.map((x) => x.replace(/https?:\/\/\S+/g, '[URL redacted]'));
async function until(fn) {
  for (let i = 0; i < 300; i++) {
    if (await fn()) return;
    await pause(100);
  }
  throw Error('Session condition timed out');
}
async function control(prefix, action) {
  const initial = await fetch(host + '/v1/auth/me'),
    jar = new Map();
  for (const cookie of initial.headers.getSetCookie()) {
    const pair = cookie.split(';')[0],
      i = pair.indexOf('=');
    jar.set(pair.slice(0, i), pair.slice(i + 1));
  }
  const response = await fetch(host + '/' + prefix + '/' + action, {
    method: 'POST',
    headers: {
      cookie: [...jar].map(([k, v]) => k + '=' + v).join('; '),
      'x-csrftoken': decodeURIComponent(jar.get('csrftoken')),
    },
  });
  assert.equal(response.status, 200);
  return response.json();
}
async function api(path, method = 'GET', csrf = true) {
  return JSON.parse(
    await c.evaluate(
      `(async()=>{const token=document.cookie.split('; ').find(x=>x.startsWith('csrftoken='));const r=await fetch('/api/v1'+${JSON.stringify(path)},{method:${JSON.stringify(method)},headers:${csrf}&&token?{'x-csrftoken':decodeURIComponent(token.slice(10))}:{}});return JSON.stringify({status:r.status,data:r.status===204?null:await r.json()});})()`,
    ),
  );
}
async function spa(path) {
  await c.evaluate(
    `(async()=>{await document.querySelector('#app').__vue_app__.config.globalProperties.$router.push(${JSON.stringify(path)});return true;})()`,
  );
}
async function refresh() {
  await c.evaluate(`void ${session}.refresh()`);
}
async function login(person = 'owner', target = studio) {
  await c.navigate(base + '/login?returnTo=' + encodeURIComponent(target));
  await c.fill('#login-email', fixture.people[person].email);
  await c.fill('#login-password', 'abcdef');
  await c.click('.auth-card button[type=submit]');
  await c.wait(
    `location.pathname===${JSON.stringify(target)} && !document.querySelector('#login-password')`,
  );
}
async function reset(person = 'owner', target = studio) {
  await api('/auth/logout', 'POST');
  await login(person, target);
}
const privateCalls = () =>
  calls.filter(
    (x) =>
      x.path.startsWith('/api/v1/videos/mine') ||
      x.path.startsWith('/api/v1/playlists/mine') ||
      x.path.startsWith('/api/v1/moderation') ||
      x.path.startsWith('/api/v1/admin'),
  ).length;
async function state(title) {
  await c.wait(
    `Array.from(document.querySelectorAll('.page-state h1')).some(x=>x.textContent===${JSON.stringify(title)})`,
  );
}
async function table() {
  await c.wait(
    "document.querySelectorAll('.own-video-table tbody tr').length>0",
  );
}
async function noPrivate() {
  return c.evaluate(
    `!document.querySelector('.own-video-table') && !document.querySelector('#studio-navigation') && !document.querySelector('.upload-dialog') && !document.querySelector('.job-history') && ${client}.getQueryCache().getAll().filter(q=>q.queryKey[0]!=='session').every(q=>q.state.data===undefined)`,
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
  await control('__fixture', 'moderator');
  await c.viewport(1280, 900);
  if (!firefox)
    await c.send('Emulation.setFocusEmulationEnabled', { enabled: true });
  holdMe = true;
  const beforeInitial = privateCalls();
  await c.navigate(base + studio);
  await until(() => heldMe.size > 0);
  await state('Проверяем аккаунт');
  check(
    privateCalls() === beforeInitial && (await noPrivate()),
    'cold identity check sends no private request and shows no personal shell',
  );
  holdMe = false;
  release();
  await state('Войдите в YouTube Studio');
  check(
    !(await c.evaluate("!!document.querySelector('.studio-avatar')")),
    'confirmed guest has no account avatar',
  );
  await login();
  await table();
  check(
    await c.evaluate(
      `document.querySelector('.studio-navigation__identity').textContent.includes(${JSON.stringify('Автор owner')})`,
    ),
    'real login displays confirmed owner identity',
  );
  check(
    await c.evaluate(
      '!!document.querySelector(\'#studio-navigation a[href="/admin/jobs"]\') && !!document.querySelector(\'#studio-navigation a[href="/moderation"]\')',
    ),
    'actual admin receives both staff entries',
  );
  check(
    (await api('/auth/me')).status === 200 &&
      (await c.evaluate(
        "!document.cookie.split(';').some(x=>x.trim().startsWith('session='))",
      )),
    'authenticated opaque session is HttpOnly and not readable by UI',
  );
  holdMe = true;
  const beforeReload = privateCalls();
  await c.reload();
  await until(() => heldMe.size > 0);
  await state('Проверяем аккаунт');
  check(
    privateCalls() === beforeReload && (await noPrivate()),
    'reload with retained cookie waits for identity without stale personal content',
  );
  holdMe = false;
  release();
  await table();
  check(true, 'valid server session restores owned catalogue after reload');

  for (const path of ['/studio/upload', '/moderation', '/admin/jobs']) {
    holdMe = true;
    const before = privateCalls();
    await c.navigate(base + path);
    await until(() => heldMe.size > 0);
    await state('Проверяем аккаунт');
    check(
      privateCalls() === before && (await noPrivate()),
      'cold ' + path + ' cannot read/render personal data before identity',
    );
    holdMe = false;
    release();
    await c.wait(
      `!Array.from(document.querySelectorAll('.page-state h1')).some(x=>x.textContent==='Проверяем аккаунт')`,
    );
  }
  await c.navigate(base + studio);
  await table();
  meFailure = 503;
  await refresh();
  await state('Не удалось проверить аккаунт');
  check(
    await noPrivate(),
    'background identity failure removes personal data and actions',
  );
  check(
    !(await c.evaluate(
      "document.body.textContent.includes('Войдите в YouTube Studio')",
    )),
    'identity transport failure is distinct from confirmed guest',
  );
  meFailure = 0;
  holdMe = true;
  const beforeRetry = calls.filter((x) => x.path === '/api/v1/auth/me').length;
  await c.evaluate("document.querySelector('.page-state button').focus()");
  await enter();
  await until(() => heldMe.size > 0);
  await c.evaluate(
    "for(let i=0;i<4;i++)document.querySelector('.page-state button').click()",
  );
  check(
    calls.filter((x) => x.path === '/api/v1/auth/me').length ===
      beforeRetry + 1 && (await noPrivate()),
    'repeated identity retry shares one request and cannot revive stale account while pending',
  );
  holdMe = false;
  release();
  await table();
  check(
    true,
    'same server session recovers after keyboard retry with fresh catalogue',
  );
  badId = true;
  await refresh();
  await state('Не удалось проверить аккаунт');
  check(
    await noPrivate(),
    'malformed account ID cannot become confirmed identity',
  );
  badId = false;
  await c.click('.page-state button');
  await table();

  const expired = await control('__session_fixture', 'expired');
  check(
    expired.affected > 0,
    'fixture expires only its server-side Valkey sessions',
  );
  await refresh();
  await state('Войдите в YouTube Studio');
  check(
    calls.filter((x) => x.path === '/api/v1/auth/me').at(-1).status === 401 &&
      calls.filter((x) => x.path === '/api/v1/auth/me').at(-1).hasSession &&
      (await noPrivate()),
    'server expiry with retained browser cookie removes identity and private cache',
  );
  await login();
  await table();
  const lost = await control('__session_fixture', 'lost');
  check(
    lost.affected > 0,
    'fixture simulates real loss of its Valkey session records',
  );
  await c.reload();
  await state('Войдите в YouTube Studio');
  check(
    await noPrivate(),
    'lost Valkey session cannot be restored from saved client identity',
  );
  await login();
  await table();
  check(true, 'new login restores persistent video data after session loss');
  check(
    (await api('/auth/logout', 'POST', false)).status === 403 &&
      (await api('/auth/me')).status === 200,
    'real missing-CSRF mutation refusal preserves valid server session',
  );

  await reset('invited');
  await c.wait("!!document.querySelector('#studio-navigation')");
  check(
    await c.evaluate(
      '!document.querySelector(\'#studio-navigation a[href="/admin/jobs"]\') && !document.querySelector(\'#studio-navigation a[href="/moderation"]\')',
    ),
    'ordinary user has no staff entries',
  );
  for (const path of ['/moderation', '/admin/jobs']) {
    const before = privateCalls();
    await spa(path);
    await state('Раздел недоступен');
    check(
      privateCalls() === before,
      'direct ordinary-user route ' + path + ' issues no staff request',
    );
  }
  check(
    (await api('/moderation/reports')).status === 403 &&
      (await api('/admin/job-errors')).status === 403,
    'server denies staff APIs independently of route controls',
  );

  await control('__fixture', 'user');
  await reset('stranger', '/');
  await c.wait("!!document.querySelector('.avatar-button')");
  await control('__fixture', 'moderator');
  await refresh();
  await c.wait(`${session}.account?.role==='moderator'`);
  await spa('/moderation');
  await c.wait(
    "document.querySelectorAll('.moderation table tbody tr').length>0",
  );
  check(
    await c.evaluate(
      '!!document.querySelector(\'#studio-navigation a[href="/moderation"]\') && !document.querySelector(\'#studio-navigation a[href="/admin/jobs"]\')',
    ),
    'existing session sees real moderator grant without relogin',
  );
  check(
    (await api('/admin/job-errors')).status === 403,
    'moderator cannot obtain administrator data',
  );
  await control('__fixture', 'admin');
  await refresh();
  await c.wait(`${session}.account?.role==='admin'`);
  await spa('/admin/jobs');
  await state('История ошибок пуста');
  check(
    await c.evaluate(
      '!!document.querySelector(\'#studio-navigation a[href="/admin/jobs"]\')',
    ),
    'existing session sees admin grant and authorized history API',
  );
  check(
    (await api('/videos/' + v.private)).status === 404,
    'staff role does not bypass ordinary private video access',
  );
  holdJobs = true;
  await c.evaluate(
    `void ${client}.refetchQueries({queryKey:['job-admin'],type:'active'})`,
  );
  await until(() => heldJobs.size > 0);
  await control('__fixture', 'user');
  await refresh();
  await state('Раздел недоступен');
  holdJobs = false;
  release(heldJobs);
  check(
    await c.evaluate(
      `!document.querySelector('#studio-navigation a[href="/admin/jobs"]') && !document.querySelector('.job-history') && ${client}.getQueryCache().getAll().filter(q=>q.queryKey[0]==='job-admin').every(q=>q.state.data===undefined)`,
    ),
    'demotion removes staff cache and ignores delayed formerly authorized result',
  );
  await control('__fixture', 'admin');
  await refresh();
  await state('История ошибок пуста');
  await control('__fixture', 'user');
  await c.evaluate(
    `void ${client}.refetchQueries({queryKey:['job-admin'],type:'active'})`,
  );
  await state('Раздел недоступен');
  await c.wait(`${session}.account?.role==='user'`);
  check(
    !(await c.evaluate(
      '!!document.querySelector(\'#studio-navigation a[href="/admin/jobs"]\')',
    )),
    'real staff API refusal refreshes stale role and removes its actions',
  );

  await reset();
  await table();
  holdMe = true;
  await refresh();
  await until(() => heldMe.size > 0);
  await control('__session_fixture', 'lost');
  await c.evaluate(
    `void ${client}.refetchQueries({queryKey:['studio-videos'],type:'active'})`,
  );
  await state('Войдите в YouTube Studio');
  holdMe = false;
  release();
  await c.wait(`${session}.account===null`);
  check(
    await noPrivate(),
    'late successful identity snapshot cannot revive expired session after protected API 401',
  );

  await login();
  await table();
  holdMe = true;
  await refresh();
  await until(() => heldMe.size > 0);
  await spa('/login?returnTo=' + encodeURIComponent(studio));
  await c.fill('#login-email', fixture.people.invited.email);
  await c.fill('#login-password', 'abcdef');
  await c.click('.auth-card button[type=submit]');
  await c.wait(
    `location.pathname==='${studio}' && !!document.querySelector('#studio-navigation')`,
  );
  holdMe = false;
  release();
  check(
    await c.evaluate(
      `${session}.account.id===${JSON.stringify(fixture.people.invited.id)} && !document.querySelector('.own-video-table')`,
    ),
    'late me from old account cannot replace explicit new login',
  );
  check(
    await c.evaluate(
      `(()=>{const account=${session}.account;return !('password' in account)&&!('session' in account)&&!JSON.stringify({...localStorage,...sessionStorage}).includes('abcdef');})()`,
    ),
    'account data and local stores contain neither password nor session secret',
  );

  await c.viewport(390, 844, true);
  await c.navigate(base + studio);
  await c.wait("!!document.querySelector('#studio-navigation')");
  meFailure = 503;
  await refresh();
  await state('Не удалось проверить аккаунт');
  check(
    await noPrivate(),
    'phone removes identity and personal actions on failed check',
  );
  meFailure = 0;
  await c.evaluate("document.querySelector('.page-state button').focus()");
  await enter();
  await c.wait("!!document.querySelector('#studio-navigation')");
  check(
    true,
    'phone keyboard can restore actual session after temporary identity failure',
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
      profile: 'session',
      browser: firefox ? 'Firefox' : 'Chromium',
      assertions,
      status: 'pass',
    }),
  );
} catch (error) {
  console.error(
    JSON.stringify({
      profile: 'session',
      assertions,
      path: await c.evaluate('location.pathname'),
      errors: safeErrors(),
    }),
  );
  throw error;
} finally {
  release();
  release(heldJobs);
  await c.close();
  for (const socket of sockets) socket.destroy();
  server.closeAllConnections();
  await new Promise((resolve) => server.close(resolve));
}
