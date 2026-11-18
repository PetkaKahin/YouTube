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
const autoplayAllow = process.argv.includes('--autoplay-allow');
const firefox = process.argv.includes('--firefox'),
  v = fixture.videos,
  host = 'http://youtube-frontend-upload-gateway:8080/api/v1';
const jars = new Map(),
  calls = [],
  held = new Set(),
  sockets = new Set();
let holdBeforePath = '';
let stripCsrf = false;
let failurePath = '',
  holdAfterPath = '';
function release() {
  for (const resume of held) resume();
  held.clear();
}
const server = createServer(async (req, res) => {
  try {
    const api = req.url.startsWith('/api/'),
      call = { path: req.url, method: req.method, status: undefined };
    if (api) calls.push(call);
    if (failurePath && req.url.split('?')[0] === failurePath) {
      res
        .writeHead(503, { 'content-type': 'application/problem+json' })
        .end('{"code":"unavailable"}');
      return;
    }
    const headers = new Headers();
    for (const key of ['cookie', 'content-type', 'x-csrftoken', 'range'])
      if (req.headers[key]) headers.set(key, req.headers[key]);
    if (stripCsrf && req.method === 'PUT') headers.delete('x-csrftoken');
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    if (holdBeforePath && req.url.split('?')[0] === holdBeforePath) {
      holdBeforePath = '';
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
    if (holdAfterPath && req.url.split('?')[0] === holdAfterPath) {
      holdAfterPath = '';
      await new Promise((resolve) => held.add(resolve));
    }
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
const c = firefox
    ? await firefoxBrowser({ autoplayAllow })
    : await browser({ autoplayAllow }),
  base = 'http://127.0.0.1:4189',
  defaultTarget = '/feed/history';
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
async function fillLogin(person, target = defaultTarget) {
  await spa('/login?returnTo=' + encodeURIComponent(target));
  await c.wait("!!document.querySelector('#login-email')");
  await c.fill('#login-email', fixture.people[person].email);
  await c.fill('#login-password', 'abcdef');
  await c.click('.auth-card button[type=submit]');
  await c.wait(
    `!document.querySelector('#login-password') && ${store}.account?.id===${JSON.stringify(fixture.people[person].id)} && !${store}.changing`,
  );
}
async function login(person = 'owner', target = defaultTarget) {
  await c.navigate(base + '/login?returnTo=' + encodeURIComponent(target));
  await c.fill('#login-email', fixture.people[person].email);
  await c.fill('#login-password', 'abcdef');
  await c.click('.auth-card button[type=submit]');
  await c.wait(
    `!document.querySelector('#login-password') && ${store}.account?.id===${JSON.stringify(fixture.people[person].id)} && !${store}.changing`,
  );
}
const root = '/watch?v=';
const allow = process.argv.includes('--autoplay-allow');
const localKey = 'youtube.guest-progress.v1:' + v.public;
const writes = () =>
  calls.filter((x) => x.method === 'PUT' && x.path.includes('/progress'));
async function ready(id = v.public, position = 0) {
  await c.wait(
    `!!document.querySelector('.watch-page__metadata h1') && document.querySelector('.player video')?.readyState>=2 && !document.querySelector('.player--restoring')`,
    30000,
  );
  await c.wait(
    `Math.abs(document.querySelector('.player video').currentTime-${position})<${allow ? 2 : 0.15}`,
    30000,
  );
  assert.equal(
    await c.evaluate("new URLSearchParams(location.search).get('v')"),
    id,
  );
}
async function navigate(id = v.public, position = 0) {
  await c.navigate(base + '/watch?v=' + id);
  await ready(id, position);
}
async function accountPosition(person, id, position) {
  const r = await actorRequest(person, '/videos/' + id + '/progress', 'PUT', {
    position_seconds: position,
  });
  assert.equal(r.status, 200);
}
async function current() {
  return await c.evaluate(
    "document.querySelector('.player video').currentTime",
  );
}
async function pausePlayer() {
  await c.evaluate("document.querySelector('.player video').pause()");
  await c.wait("document.querySelector('.player video').paused");
}
async function seek(position) {
  await c.fill('.player__seek', String(position));
  await c.wait(
    `Math.abs(document.querySelector('.player video').currentTime-${position})<0.15`,
  );
}
async function serverPosition(person = 'owner', id = v.public) {
  return (
    (await actorRequest(person, '/videos/' + id)).data.watch_progress
      ?.position_seconds ?? null
  );
}
async function enter() {
  if (firefox) await c.key('\uE007');
  else {
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
}
async function play() {
  await c.evaluate(
    "document.querySelector('.player video').muted=true;document.querySelector('.player__control').focus()",
  );
  await enter();
  await c.wait("!document.querySelector('.player video').paused");
}
async function api(path, method = 'GET', body) {
  return JSON.parse(
    await c.evaluate(
      `(async()=>{const cookie=document.cookie.split('; ').find(x=>x.startsWith('csrftoken='));const r=await fetch('/api/v1'+${JSON.stringify(path)},{method:${JSON.stringify(method)},headers:{'content-type':'application/json',...(cookie?{'x-csrftoken':decodeURIComponent(cookie.slice(10))}:{})},body:${body === undefined ? 'undefined' : JSON.stringify(JSON.stringify(body))}});return JSON.stringify({status:r.status,data:r.status===204?null:await r.json()});})()`,
    ),
  );
}
try {
  await until(async () => {
    try {
      return (await fetch(host + '/health/ready')).ok;
    } catch {
      return false;
    }
  });
  await c.viewport(1280, 900);
  if (!firefox)
    await c.send('Emulation.setFocusEmulationEnabled', { enabled: true });
  await c.navigate(base + '/');
  await c.evaluate(`localStorage.setItem(${JSON.stringify(localKey)},'6')`);
  const initialWrites = writes().length;
  await navigate(v.public, 6);
  check((await current()) >= 5.9, 'guest starts at stored midpoint');
  if (allow) {
    await c.wait(
      "!document.querySelector('.player video').paused && document.querySelector('.player video').currentTime>6.2",
    );
    check(
      (await current()) < 9,
      'allowed autoplay advances from restored midpoint',
    );
    await pausePlayer();
    check(
      Number(
        await c.evaluate(`localStorage.getItem(${JSON.stringify(localKey)})`),
      ) >= 6,
      'guest autoplay never replaces midpoint with initial zero',
    );
  } else {
    check(
      await c.evaluate(
        "document.querySelector('.player video').paused && !!document.querySelector('.player__start')",
      ),
      'blocked autoplay leaves paused player at restored position',
    );
    await c.reload();
    await ready(v.public, 6);
    check(true, 'reload preserves paused saved position');
    check(
      Number(
        await c.evaluate(`localStorage.getItem(${JSON.stringify(localKey)})`),
      ) === 6,
      'metadata/initial seek does not overwrite guest position',
    );
    await c.evaluate(`localStorage.setItem(${JSON.stringify(localKey)},'9')`);
    await pause(700);
    check(
      Math.abs((await current()) - 6) < 0.15,
      'live player does not seek when guest state changes',
    );
    await seek(4);
    await c.wait(
      `Number(localStorage.getItem(${JSON.stringify(localKey)}))===4`,
    );
    await c.reload();
    await ready(v.public, 4);
    check(true, 'explicit backwards seek saves and restores new position');
    await seek(0);
    await c.wait(
      `Number(localStorage.getItem(${JSON.stringify(localKey)}))===0`,
    );
    await c.reload();
    await ready();
    check(
      true,
      'explicit rewind to zero is preserved rather than suppressed as initialization',
    );
    await c.evaluate(
      `localStorage.setItem(${JSON.stringify(localKey)},'1e30')`,
    );
    await c.reload();
    await c.wait(
      "document.querySelector('.player video')?.duration>0 && !document.querySelector('.player--restoring')",
      30000,
    );
    check(
      await c.evaluate(
        "Math.abs(document.querySelector('.player video').currentTime-document.querySelector('.player video').duration)<0.15 && document.querySelector('.player video').paused",
      ),
      'oversized stored value clamps to real end without replaying from zero',
    );
    await play();
    await c.wait(
      "document.querySelector('.player video').currentTime>0.2 && document.querySelector('.player video').currentTime<3",
    );
    await pausePlayer();
    check(
      true,
      'explicit replay from restored end starts cleanly from beginning',
    );
    await spa('/');
    for (const raw of ['-1', 'null', '"oops"', '1e400']) {
      await c.evaluate(
        `localStorage.setItem(${JSON.stringify(localKey)},${JSON.stringify(raw)})`,
      );
      await navigate();
      check(
        (await current()) < 0.15,
        'invalid guest position falls back safely: ' + raw,
      );
    }
  }
  check(
    writes().length === initialWrites,
    'guest restore never sends account progress',
  );
  // Account state is authoritative and never inherits guest storage.
  await accountPosition('owner', v.public, 5);
  await accountPosition('owner', v.silent, 2);
  await c.evaluate(`localStorage.setItem(${JSON.stringify(localKey)},'9')`);
  await fillLogin('owner', '/watch?v=' + v.public);
  await ready(v.public, 5);
  check(
    (await current()) >= 4.9,
    'account restores server position instead of guest position',
  );
  if (allow) {
    await c.wait(
      "!document.querySelector('.player video').paused && document.querySelector('.player video').currentTime>5.2",
    );
    await pausePlayer();
    await until(async () => (await serverPosition()) >= 5);
    check(
      (await serverPosition()) >= 5,
      'account autoplay persists positive progress after restore',
    );
  } else {
    await pause(700);
    check(
      (await serverPosition()) === 5,
      'paused restoration does not rewrite server progress',
    );
    const before = writes().length;
    await c.evaluate(
      `void ${client}.refetchQueries({queryKey:['video'],type:'active'})`,
    );
    await c.wait(`!${client}.isFetching({queryKey:['video']})`);
    check(
      Math.abs((await current()) - 5) < 0.15 && writes().length === before,
      'metadata refetch neither seeks nor writes',
    );
    await accountPosition('owner', v.public, 8);
    await c.evaluate(
      `void ${client}.refetchQueries({queryKey:['video'],type:'active'})`,
    );
    await c.wait(`!${client}.isFetching({queryKey:['video']})`);
    check(
      Math.abs((await current()) - 5) < 0.15,
      'new server position does not move currently open playback',
    );
    await c.reload();
    await ready(v.public, 8);
    check(true, 'next opening uses newly confirmed server position');
    await seek(3);
    await until(async () => (await serverPosition()) === 3);
    await c.reload();
    await ready(v.public, 3);
    check(true, 'manual rewind replaces account position and survives reload');
  }
  const firstPos = allow ? await serverPosition() : 3;
  await spa('/watch?v=' + v.silent);
  await ready(v.silent, 2);
  await pausePlayer();
  check(
    Math.abs((await current()) - 2) < (allow ? 2 : 0.15),
    'next video uses its own stored position',
  );
  await spa('/watch?v=' + v.public);
  await ready(v.public, firstPos);
  await pausePlayer();
  check(
    (await current()) >= firstPos - 0.15,
    'return to first video never inherits second video position',
  );
  await accountPosition('invited', v.public, 1);
  await fillLogin('invited', '/watch?v=' + v.public);
  await ready(v.public, 1);
  await pausePlayer();
  check(
    (await current()) >= 0.9,
    'switching accounts uses new viewer position',
  );
  if (!allow) {
    await play();
    await c.wait("document.querySelector('.player video').currentTime>1.1");
    await pausePlayer();
    const pausedAt = await current();
    await until(
      async () => Math.abs((await serverPosition('invited')) - pausedAt) < 0.2,
    );
    check(
      (await serverPosition('invited')) > 1,
      'actual playback continues saving after initialization gate',
    );
    await accountPosition('invited', v.private, 7);
    await spa('/watch?v=' + v.private);
    await ready(v.private, 7);
    check(
      Math.abs((await current()) - 7) < 0.15,
      'permitted private restores personal position',
    );
    await actorRequest(
      'owner',
      '/videos/' + v.private + '/permissions',
      'PUT',
      { email: fixture.people.invited.email, allowed: false },
    );
    await c.evaluate(
      `void ${client}.refetchQueries({queryKey:['video'],type:'active'})`,
    );
    await c.wait(
      "document.querySelector('.watch-page .page-state h1')?.textContent==='Видео недоступно'",
    );
    check(
      !(await c.evaluate("!!document.querySelector('.player video')")),
      'saved position never bypasses revoked private access',
    );
    await actorRequest(
      'owner',
      '/videos/' + v.private + '/permissions',
      'PUT',
      { email: fixture.people.invited.email, allowed: true },
    );
    await c.viewport(390, 844, true);
    await navigate(v.public, pausedAt);
    check(
      await c.evaluate('document.documentElement.scrollWidth<=innerWidth'),
      'restored controls remain reachable on phone',
    );
  }
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
    'no unexpected runtime errors: ' + errors.join('|'),
  );
  console.log(
    JSON.stringify({
      profile: allow ? 'resume-autoplay' : 'resume',
      browser: firefox ? 'Firefox' : 'Chromium',
      assertions,
      status: 'pass',
    }),
  );
} catch (error) {
  console.error(
    JSON.stringify({
      profile: 'resume',
      assertions,
      path: await c.evaluate('location.pathname'),
      errors: safeErrors(),
      state: await c.evaluate(
        "JSON.stringify({time:document.querySelector('.player video')?.currentTime,duration:document.querySelector('.player video')?.duration,ready:document.querySelector('.player video')?.readyState,restoring:!!document.querySelector('.player--restoring')})",
      ),
    }),
  );
  throw error;
} finally {
  release();
  await c.close();
  for (const socket of sockets) socket.destroy();
  server.closeAllConnections();
  await new Promise((resolve) => server.close(resolve));
}
