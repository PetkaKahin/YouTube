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
if (!fixture?.videos?.silent) throw Error('Access fixture not ready');
const firefox = process.argv.includes('--firefox'),
  v = fixture.videos,
  host = 'http://youtube-frontend-upload-gateway:8080/api/v1';
const jars = new Map(),
  calls = [],
  held = new Set(),
  sockets = new Set();
let holdPath = '',
  heldMeCookie = false;
function release() {
  for (const resume of held) resume();
  held.clear();
}
const server = createServer(async (req, res) => {
  try {
    const api = req.url.startsWith('/api/'),
      call = { path: req.url, method: req.method, status: undefined };
    if (api) calls.push(call);
    const headers = new Headers();
    for (const key of ['cookie', 'content-type', 'x-csrftoken', 'range'])
      if (req.headers[key]) headers.set(key, req.headers[key]);
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    if (holdPath && req.url === holdPath) {
      heldMeCookie = !!req.headers.cookie;
      holdPath = '';
      await new Promise((resolve) => held.add(resolve));
    }
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
    const result = Buffer.from(await response.arrayBuffer());
    res.statusCode = response.status;
    for (const key of ['content-type', 'location', 'cache-control'])
      if (response.headers.has(key))
        res.setHeader(key, response.headers.get(key));
    if (response.headers.getSetCookie().length)
      res.setHeader('set-cookie', response.headers.getSetCookie());
    res.end(result);
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
const sdk = "await import('/src/shared/api/index.ts')";
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
  throw Error('Access condition timed out');
}
async function actorRequest(person, path, method = 'GET', body) {
  let cookies = jars.get(person);
  const absorb = (response) =>
    response.headers.getSetCookie().forEach((value) => {
      const pair = value.split(';')[0],
        index = pair.indexOf('=');
      cookies.set(pair.slice(0, index), pair.slice(index + 1));
    });
  const headers = () => ({
    cookie: [...cookies].map(([key, value]) => key + '=' + value).join('; '),
    'x-csrftoken': decodeURIComponent(cookies.get('csrftoken')),
    'content-type': 'application/json',
  });
  if (!cookies) {
    cookies = new Map();
    absorb(await fetch(host + '/auth/me'));
    const auth = await fetch(host + '/auth/login', {
      method: 'POST',
      headers: headers(),
      body: JSON.stringify({
        email: fixture.people[person].email,
        password: 'abcdef',
      }),
    });
    assert.equal(auth.status, 200);
    absorb(auth);
    jars.set(person, cookies);
  }
  const response = await fetch(host + path, {
    method,
    headers: headers(),
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  absorb(response);
  const raw = await response.text();
  return {
    status: response.status,
    data:
      raw && response.headers.get('content-type')?.includes('json')
        ? JSON.parse(raw, (key, value, context) =>
            (key === 'id' || key.endsWith('_id')) && typeof value === 'number'
              ? context.source
              : value,
          )
        : raw || null,
  };
}

async function api(path, method = 'GET') {
  return JSON.parse(
    await c.evaluate(
      `(async()=>{const cookie=document.cookie.split('; ').find(x=>x.startsWith('csrftoken='));const r=await fetch('/api/v1'+${JSON.stringify(path)},{method:${JSON.stringify(method)},headers:cookie?{'x-csrftoken':decodeURIComponent(cookie.slice(10))}:{}});return JSON.stringify({status:r.status,data:r.status===204?null:await r.json()});})()`,
    ),
  );
}
async function control(action, prefix = '__fixture') {
  const controlHost = 'http://youtube-frontend-upload-gateway:8080/api';
  const initial = await fetch(controlHost + '/v1/auth/me');
  const cookies = new Map();
  for (const cookie of initial.headers.getSetCookie()) {
    const pair = cookie.split(';')[0],
      index = pair.indexOf('=');
    cookies.set(pair.slice(0, index), pair.slice(index + 1));
  }
  const r = await fetch(controlHost + '/' + prefix + '/' + action, {
    method: 'POST',
    headers: {
      cookie: [...cookies].map(([key, value]) => key + '=' + value).join('; '),
      'x-csrftoken': decodeURIComponent(cookies.get('csrftoken')),
    },
  });
  assert.equal(r.status, 200);
  return r.json();
}
async function spa(path) {
  await c.evaluate(
    `(async()=>{await document.querySelector('#app').__vue_app__.config.globalProperties.$router.push(${JSON.stringify(path)});return true;})()`,
  );
}
async function fillLogin(person, target = studio) {
  await spa('/login?returnTo=' + encodeURIComponent(target));
  await c.wait("!!document.querySelector('#login-email')");
  await c.fill('#login-email', fixture.people[person].email);
  await c.fill('#login-password', 'abcdef');
  await c.click('.auth-card button[type=submit]');
  await c.wait(
    `!document.querySelector('#login-password') && ${store}.account?.id===${JSON.stringify(fixture.people[person].id)} && !${store}.changing`,
  );
}
async function login(person = 'owner', target = studio) {
  await c.navigate(base + '/login?returnTo=' + encodeURIComponent(target));
  await c.fill('#login-email', fixture.people[person].email);
  await c.fill('#login-password', 'abcdef');
  await c.click('.auth-card button[type=submit]');
  await c.wait(
    `!document.querySelector('#login-password') && ${store}.account?.id===${JSON.stringify(fixture.people[person].id)} && !${store}.changing`,
  );
}
async function player() {
  await c.wait(
    "document.querySelector('.player__start')?.disabled===false && !!document.querySelector('.watch-page__metadata h1')",
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
async function play() {
  await player();
  await c.evaluate(
    "document.querySelector('.player video').muted=true;document.querySelector('.player__start').focus()",
  );
  await enter();
  await c.wait(
    "!document.querySelector('.player video').paused && document.querySelector('.player video').currentTime>0",
  );
}
async function denied() {
  await c.wait(
    "document.querySelector('.watch-page .page-state h1')?.textContent==='Видео недоступно'",
  );
}
async function permission(allowed) {
  assert.equal(
    (
      await actorRequest(
        'owner',
        '/videos/' + v.private + '/permissions',
        'PUT',
        { email: fixture.people.invited.email, allowed },
      )
    ).status,
    200,
  );
}
async function visibility(value) {
  const s = (await actorRequest('owner', '/videos/' + v.public + '/settings'))
    .data;
  assert.equal(
    (
      await actorRequest('owner', '/videos/' + v.public + '/settings', 'PUT', {
        title: s.title,
        description: s.description,
        visibility: value,
      })
    ).status,
    200,
  );
}
const refreshVideo = () =>
  c.evaluate(
    `void ${client}.refetchQueries({queryKey:['video'],type:'active'})`,
  );
async function cleanScope(scope) {
  return c.evaluate(
    `${client}.getQueryCache().getAll().filter(q=>q.queryKey.includes(${JSON.stringify(scope)})).every(q=>q.state.data===undefined)`,
  );
}
try {
  await control('restore_silent');
  await permission(true);
  await visibility('public');
  await c.viewport(1280, 900);
  if (!firefox)
    await c.send('Emulation.setFocusEmulationEnabled', { enabled: true });
  await login();
  await c.wait(
    "document.querySelectorAll('.own-video-table tbody tr').length>0",
  );
  const ownerScope = await c.evaluate(`${store}.scope`);
  holdPath = '/api/v1/videos/mine';
  await c.evaluate(
    `void (async()=>{const m=${sdk};window.oldStatus=undefined;const r=await m.mineV1VideosMineGet();window.oldStatus=r.response?.status??0;})()`,
  );
  await until(() => held.size > 0);
  await fillLogin('invited');
  release();
  await c.wait('window.oldStatus===401');
  check(
    (await api('/auth/me')).status === 200 &&
      (await c.evaluate(
        `${store}.account?.id===${JSON.stringify(fixture.people.invited.id)}`,
      )),
    'real delayed 401 from previous cookie cannot expire newly authenticated account',
  );
  check(
    (await cleanScope(ownerScope)) &&
      !(await c.evaluate("!!document.querySelector('.own-video-table')")),
    'new account never displays prior owned catalogue/cache',
  );
  check(
    heldMeCookie &&
      calls.some((x) => x.path === '/api/v1/videos/mine' && x.status === 401),
    'late request really used the former cookie and server rejected it',
  );
  await control('lost', '__session_fixture');
  jars.clear();
  await c.evaluate(
    `void (async()=>{const m=${sdk};const r=await m.mineV1VideosMineGet();window.currentStatus=r.response?.status??0;})()`,
  );
  await c.wait(
    `window.currentStatus===401 && !${store}.account && !${store}.changing`,
  );
  check(
    await c.evaluate(
      "!document.querySelector('#studio-navigation') && !document.querySelector('.own-video-table')",
    ),
    'current-context 401 still expires identity and clears personal UI',
  );

  await login();
  await actorRequest('invited', '/videos/' + v.public + '/like', 'PUT', {
    liked: false,
  });
  const writesBefore = calls.filter(
    (x) => x.method === 'PUT' && x.path.endsWith('/like'),
  ).length;
  holdPath = '/api/v1/auth/me';
  await c.evaluate(
    `document.cookie='csrftoken=;path=/;max-age=0';void (async()=>{const m=${sdk};const r=await m.setStateV1VideosVideoIdLikePut({path:{video_id:${JSON.stringify(v.public)}},body:{liked:true}});window.oldWriteDone=true;window.oldWriteStatus=r.response?.status??0;})()`,
  );
  await until(() => held.size > 0);
  await fillLogin('invited');
  release();
  await c.wait('window.oldWriteDone===true');
  check(
    calls.filter((x) => x.method === 'PUT' && x.path.endsWith('/like'))
      .length === writesBefore,
    'old mutation waiting for CSRF bootstrap is never sent under new session',
  );
  check(
    (await api('/videos/' + v.public + '/like')).data.liked === false &&
      (await api('/auth/me')).status === 200,
    'cancelled former action neither likes for new viewer nor expires new session',
  );

  await login();
  const beforeFetchGate = calls.filter(
    (x) => x.method === 'PUT' && x.path.endsWith('/like'),
  ).length;
  await c.evaluate(
    `void (async()=>{const m=${sdk};const moduleUrl=performance.getEntriesByType('resource').find(x=>new URL(x.name).pathname==='/src/shared/api/generated/client.gen.ts').name;const {client}=await import(moduleUrl);let once=true;client.interceptors.request.use(async request=>{if(once&&request.method==='PUT'&&request.url.endsWith('/like')){once=false;await new Promise(resolve=>{window.fetchGate=resolve;});}return request;});const r=await m.setStateV1VideosVideoIdLikePut({path:{video_id:${JSON.stringify(v.public)}},body:{liked:true}});window.fetchGateDone=true;})()`,
  );
  await c.wait("typeof window.fetchGate==='function'");
  await fillLogin('invited');
  await c.evaluate('window.fetchGate()');
  await c.wait('window.fetchGateDone===true');
  check(
    calls.filter((x) => x.method === 'PUT' && x.path.endsWith('/like'))
      .length === beforeFetchGate,
    'final fetch boundary rejects old request even after a later asynchronous interceptor',
  );
  check(
    (await api('/videos/' + v.public + '/like')).data.liked === false &&
      (await api('/auth/me')).status === 200,
    'last-moment cancellation preserves new viewer reaction and session',
  );

  await permission(true);
  await login('invited', '/watch?v=' + v.private);
  await play();
  await c.evaluate(
    "window.revokedPlayer=document.querySelector('.player video')",
  );
  await permission(false);
  await refreshVideo();
  await denied();
  check(
    await c.evaluate(
      "window.revokedPlayer.paused && !window.revokedPlayer.getAttribute('src') && !window.revokedPlayer.isConnected && !document.querySelector('.watch-page__metadata') && document.title==='YouTube'",
    ),
    'actual revoked email access removes private title, metadata and playing media',
  );
  check(
    (await api('/auth/me')).status === 200 &&
      (await api('/videos/' + v.private)).status === 404,
    'resource refusal does not log out valid viewer or reveal private data',
  );
  check(
    await c.evaluate(
      `${client}.getQueryCache().getAll().filter(q=>['video','video-like','comments'].includes(q.queryKey[0])&&q.queryKey[2]===${JSON.stringify(v.private)}).every(q=>q.state.data===undefined)`,
    ),
    'revoked video clears metadata and dependent private queries',
  );
  await permission(true);
  await c.navigate(base + '/watch?v=' + v.private);
  await player();
  check(true, 'fresh read after regrant restores legitimate access');
  await spa('/results?search_query=access-lifecycle');
  await permission(false);
  holdPath = '/api/v1/videos/' + v.private;
  await c.evaluate('history.back()');
  await until(() => held.size > 0);
  check(
    await c.evaluate(
      "!document.querySelector('.watch-page__metadata') && !document.querySelector('.player')",
    ),
    'Back waits for current access without painting cached private metadata',
  );
  release();
  await denied();
  check(
    (await api('/videos/' + v.private)).status === 404,
    'Back after revoke receives ordinary server refusal',
  );
  await permission(true);

  await login('owner', '/playlist?list=' + fixture.playlists.private);
  await c.wait("!!document.querySelector('.playlist-contents__header')");
  const playlistScope = await c.evaluate(`${store}.scope`);
  await fillLogin('invited', '/playlist?list=' + fixture.playlists.private);
  await c.wait(
    "Array.from(document.querySelectorAll('.page-state h1')).some(x=>x.textContent==='Плейлист недоступен')",
  );
  check(
    !(await c.evaluate(
      "!!document.querySelector('.playlist-contents__header')",
    )) && (await cleanScope(playlistScope)),
    'owner private playlist cannot survive account switch through direct URL',
  );
  check(
    (await api('/playlists/' + fixture.playlists.private)).status === 404,
    'playlist API independently denies non-owner',
  );
  await c.navigate(base + '/studio/video/' + v.private + '/edit');
  await c.wait(
    "Array.from(document.querySelectorAll('.page-state h1')).some(x=>x.textContent==='Видео недоступно')",
  );
  check(
    !(await c.evaluate(
      "!!document.querySelector('#settings-title') || !!document.querySelector('.video-settings-form')",
    )),
    'non-owner cannot recover creator form by direct URL',
  );

  await api('/auth/logout', 'POST');
  await c.navigate(base);
  await c.wait(
    `!!document.querySelector('.video-card__preview[href="/watch?v=${v.public}"] img')`,
  );
  await visibility('private');
  await c.evaluate(
    `(()=>{const image=document.querySelector('.video-card__preview[href="/watch?v=${v.public}"] img');image.src=image.getAttribute('src')+'?access-lifecycle=1';})()`,
  );
  await c.wait(
    `!document.querySelector('.video-card__preview[href="/watch?v=${v.public}"]')`,
  );
  check(
    true,
    'real preview denial removes newly private video from stale public feed',
  );
  await c.navigate(base + '/watch?v=' + v.public);
  await denied();
  check(
    (await api('/videos/' + v.public)).status === 404,
    'direct guest URL cannot bypass changed visibility',
  );
  await visibility('public');
  await c.navigate(base + '/watch?v=' + v.public);
  await player();
  check(true, 'public access returns only after fresh authorized read');

  await login('invited', '/watch?v=' + v.silent);
  await play();
  await c.evaluate(
    "window.hiddenPlayer=document.querySelector('.player video')",
  );
  assert.ok(
    (
      await actorRequest(
        'invited',
        '/videos/' + v.silent + '/reports',
        'POST',
        { reason: 'spam' },
      )
    ).status < 300,
  );
  let report, cursor;
  do {
    const r = await actorRequest(
      'owner',
      '/moderation/reports' +
        (cursor ? '?cursor=' + encodeURIComponent(cursor) : ''),
    );
    assert.equal(r.status, 200);
    report = r.data.items.find((x) => x.video_id === v.silent);
    cursor = r.data.next_cursor;
  } while (!report && cursor);
  assert.ok(report);
  const decision = await actorRequest(
    'owner',
    '/moderation/reports/' + report.id + '/decision',
    'PUT',
    { action: 'hide', revision: report.revision, reason: 'spam' },
  );
  assert.equal(decision.status, 200);
  await refreshVideo();
  await denied();
  check(
    await c.evaluate(
      "window.hiddenPlayer.paused && !window.hiddenPlayer.getAttribute('src') && !document.querySelector('.watch-page__metadata')",
    ),
    'real moderation hide stops ongoing ordinary playback',
  );
  check(
    (await actorRequest('owner', '/videos/' + v.silent)).status === 404 &&
      (await api('/videos/' + v.silent)).status === 404,
    'moderator/admin role never overrides hidden ordinary media access',
  );

  await c.viewport(390, 844, true);
  await permission(false);
  await c.navigate(base + '/watch?v=' + v.private);
  await denied();
  check(
    !(await c.evaluate(
      "!!document.querySelector('.player') || !!document.querySelector('.watch-page__metadata')",
    )),
    'phone direct revoked URL has same private-data boundary',
  );
  await permission(true);
  await c.navigate(base + '/watch?v=' + v.private);
  await player();
  const phoneScope = await c.evaluate(`${store}.scope`);
  await fillLogin('stranger', '/watch?v=' + v.private);
  await denied();
  check(
    (await cleanScope(phoneScope)) &&
      !(await c.evaluate("!!document.querySelector('.player')")),
    'phone account switch does not reuse other viewer permission or player',
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
      profile: 'access-lifecycle',
      browser: firefox ? 'Firefox' : 'Chromium',
      assertions,
      status: 'pass',
    }),
  );
} catch (error) {
  console.error(
    JSON.stringify({
      profile: 'access-lifecycle',
      assertions,
      path: await c.evaluate('location.pathname'),
      errors: safeErrors(),
    }),
  );
  throw error;
} finally {
  release();
  await c.close();
  for (const socket of sockets) socket.destroy();
  server.closeAllConnections();
  await new Promise((resolve) => server.close(resolve));
  await control('restore_silent');
  jars.clear();
  await permission(true);
  await visibility('public');
}
