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
const autoplayAllow = false;
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
    if (req.method === 'PUT')
      call.body = JSON.parse(Buffer.concat(chunks).toString());
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
const like = '.video-like__like',
  dislike = '.video-like__dislike';
async function navigate(id = v.public) {
  await c.navigate(base + '/watch?v=' + id);
  await c.wait("!!document.querySelector('.video-like__buttons')");
}
async function state(liked, disliked, count) {
  await c.wait(
    `document.querySelector('${like}')?.getAttribute('aria-pressed')==='${liked}' && document.querySelector('${dislike}')?.getAttribute('aria-pressed')==='${disliked}' && document.querySelector('${like} span').textContent.trim()===${JSON.stringify(String(count))}`,
  );
  await settled();
}
async function settled() {
  await c.wait(
    `!${client}.isFetching({queryKey:['video-like']}) && document.querySelector('${like}')?.getAttribute('aria-busy')!=='true'`,
  );
}
async function serverState(person = 'owner', id = v.public) {
  const r = await actorRequest(person, '/videos/' + id + '/like');
  assert.equal(r.status, 200);
  return r.data;
}
async function reset(person, id = v.public) {
  for (const [path, body] of [
    ['like', { liked: false }],
    ['dislike', { disliked: false }],
  ])
    assert.equal(
      (await actorRequest(person, '/videos/' + id + '/' + path, 'PUT', body))
        .status,
      200,
    );
}
async function openQuality() {
  await c.click('[aria-label="Настройки"]');
  await c.evaluate(
    "[...document.querySelectorAll('.player__menu button')].find(x=>x.textContent.includes('Качество')).click()",
  );
  await c.wait("!!document.querySelector('.player__menu-heading')");
}
async function checkMenuLayer(phone = false) {
  await c.viewport(phone ? 390 : 1280, phone ? 844 : 900, phone);
  await c.wait("document.querySelector('.player__start')?.disabled===false");
  await openQuality();
  const overlap = await c.evaluate(
    `JSON.stringify((()=>{const menu=document.querySelector('.player__menu'),seek=document.querySelector('.player__seek');const m=menu.getBoundingClientRect(),s=seek.getBoundingClientRect();const left=Math.max(m.left,s.left),right=Math.min(m.right,s.right),top=Math.max(m.top,s.top),bottom=Math.min(m.bottom,s.bottom);const x=(left+right)/2,y=(top+bottom)/2;return {overlap:right>left&&bottom>top,hit:!!document.elementFromPoint(x,y)?.closest('.player__menu'),above:Number(getComputedStyle(menu).zIndex)>Number(getComputedStyle(document.querySelector('.player__controls')).zIndex)};})())`,
  );
  const result = JSON.parse(overlap);
  check(
    result.overlap && result.hit && result.above,
    'quality menu receives pointer above overlapping seek bar: ' +
      (phone ? 'phone' : 'desktop'),
  );
  const before = await c.evaluate(
    "document.querySelector('.player video').currentTime",
  );
  await c.evaluate(
    "[...document.querySelectorAll('.player__menu button')].find(x=>x.textContent.trim().startsWith('Авто')).click()",
  );
  await c.wait("!document.querySelector('.player__menu')");
  check(
    Math.abs(
      (await c.evaluate(
        "document.querySelector('.player video').currentTime",
      )) - before,
    ) < 0.1,
    'quality selection does not seek underneath: ' +
      (phone ? 'phone' : 'desktop'),
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
  for (const person of ['owner', 'invited', 'stranger']) await reset(person);
  await c.viewport(1280, 900);
  if (!firefox)
    await c.send('Emulation.setFocusEmulationEnabled', { enabled: true });
  await navigate();
  await state(false, false, 0);
  check(
    !calls.some((x) => x.method === 'PUT'),
    'guest reads both buttons without reaction command',
  );
  await c.click(dislike);
  await c.wait("location.pathname==='/login'");
  check(
    await c.evaluate(
      `new URLSearchParams(location.search).get('returnTo')==='/watch?v=${v.public}'`,
    ),
    'guest dislike returns to exact video',
  );
  await c.fill('#login-email', fixture.people.owner.email);
  await c.fill('#login-password', 'abcdef');
  await c.click('.auth-card button[type=submit]');
  await c.wait(
    `!!${store}.account && !${store}.changing && document.querySelector('${dislike}')?.disabled===false`,
  );
  await state(false, false, 0);
  check(
    !calls.some((x) => x.method === 'PUT'),
    'login does not apply guest dislike',
  );
  await c.click(dislike);
  await state(false, true, 0);
  check((await serverState()).disliked, 'own dislike persists on real server');
  check(
    !Object.hasOwn(await serverState(), 'dislike_count') &&
      (await c.evaluate(`!document.querySelector('${dislike} span')`)),
    'no public dislike count in response or UI',
  );
  await c.click(like);
  await state(true, false, 1);
  check(
    !(await serverState()).disliked,
    'like removes prior dislike atomically',
  );
  await c.click(dislike);
  await state(false, true, 0);
  check(
    !(await serverState()).liked,
    'dislike removes prior like and decrements public count',
  );
  await c.reload();
  await state(false, true, 0);
  check(true, 'reload restores dislike');
  await c.click(dislike);
  await state(false, false, 0);
  check(
    !(await serverState()).disliked,
    'second dislike click removes reaction',
  );
  await c.click(like);
  await state(true, false, 1);
  await c.click(like);
  await state(false, false, 0);
  check(true, 'existing repeated like toggle remains functional');
  // One pending command gates both halves of the capsule.
  holdBeforePath = '/api/v1/videos/' + v.public + '/dislike';
  const before = calls.filter((x) => x.method === 'PUT').length;
  await c.click(dislike);
  await until(() => held.size > 0);
  await c.evaluate(
    `for(let i=0;i<4;i++){document.querySelector('${dislike}').click();document.querySelector('${like}').click();}`,
  );
  check(
    calls.filter((x) => x.method === 'PUT').length === before + 1 &&
      (await c.evaluate(
        `document.querySelector('${dislike}').getAttribute('aria-pressed')==='false' && document.querySelector('${like}').getAttribute('aria-busy')==='true'`,
      )),
    'rapid mixed clicks send one command and retain confirmed state',
  );
  const write = calls.filter((x) => x.method === 'PUT').at(-1);
  check(
    write.path.endsWith(v.public + '/dislike') &&
      JSON.stringify(write.body) === '{"disliked":true}',
    'exact int64 path and only target bool are sent',
  );
  release();
  await state(false, true, 0);
  // A stale state read cannot undo the complete command response.
  holdAfterPath = '/api/v1/videos/' + v.public + '/like';
  await c.evaluate(
    `void ${client}.refetchQueries({queryKey:['video-like'],type:'active'})`,
  );
  await until(() => held.size > 0);
  await c.click(like);
  await state(true, false, 1);
  release();
  await state(true, false, 1);
  check(true, 'late GET cannot restore prior dislike over committed like');
  failurePath = '/api/v1/videos/' + v.public + '/dislike';
  await c.click(dislike);
  await c.wait("!!document.querySelector('.video-like .form-error')");
  await settled();
  check(
    (await serverState()).liked && !(await serverState()).disliked,
    '503 does not invent reaction or alter server',
  );
  failurePath = '';
  await c.click(dislike);
  await state(false, true, 0);
  check(true, 'explicit retry applies dislike');
  stripCsrf = true;
  await c.click(like);
  await c.wait(
    "document.querySelector('.video-like .form-error')?.textContent.includes('Обновите')",
  );
  stripCsrf = false;
  await settled();
  check(
    (await serverState()).disliked &&
      (await c.evaluate("!!document.querySelector('.player video')")),
    'real CSRF failure preserves dislike and accessible video',
  );
  // Confirmed dislike removes video from the real liked collection.
  await c.click(like);
  await state(true, false, 1);
  await spa('/feed/liked');
  await c.wait(
    `!!document.querySelector('.library-page .video-card__preview[href="/watch?v=${v.public}"]')`,
  );
  await spa('/watch?v=' + v.public);
  await state(true, false, 1);
  await c.click(dislike);
  await state(false, true, 0);
  await spa('/feed/liked');
  await c.wait(
    "document.querySelector('.library-page .page-state h1')?.textContent==='Нет понравившихся видео'",
  );
  check(true, 'dislike excludes prior like from personal liked list');
  await navigate();
  await state(false, true, 0);
  // Switching route or account never inherits the previous reaction.
  holdAfterPath = '/api/v1/videos/' + v.public + '/like';
  await c.evaluate(
    `void ${client}.refetchQueries({queryKey:['video-like'],type:'active'})`,
  );
  await until(() => held.size > 0);
  await spa('/watch?v=' + v.silent);
  await state(false, false, 0);
  release();
  await state(false, false, 0);
  check(true, 'late reaction GET does not select another video');
  await navigate();
  await state(false, true, 0);
  holdAfterPath = '/api/v1/videos/' + v.public + '/like';
  await c.evaluate(
    `void ${client}.refetchQueries({queryKey:['video-like'],type:'active'})`,
  );
  await until(() => held.size > 0);
  await fillLogin('invited', '/watch?v=' + v.public);
  await state(false, false, 0);
  release();
  await state(false, false, 0);
  check(true, 'late account response cannot disclose former dislike');
  await c.evaluate(`document.querySelector('${dislike}').focus()`);
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
  await state(false, true, 0);
  check(
    await c.evaluate(
      `document.activeElement===document.querySelector('${dislike}')`,
    ),
    'keyboard dislike preserves focus and accessible selection',
  );
  // Real private permission and revocation.
  await reset('invited', v.private);
  await navigate(v.private);
  await state(false, false, 0);
  await c.click(dislike);
  await state(false, true, 0);
  check(
    (await serverState('invited', v.private)).disliked,
    'invited viewer can dislike accessible private video',
  );
  await actorRequest('owner', '/videos/' + v.private + '/permissions', 'PUT', {
    email: fixture.people.invited.email,
    allowed: false,
  });
  await c.evaluate(
    `void ${client}.refetchQueries({queryKey:['video-like'],type:'active'})`,
  );
  await c.wait(
    "document.querySelector('.watch-page .page-state h1')?.textContent==='Видео недоступно'",
  );
  check(
    await c.evaluate(
      "!document.querySelector('.video-like') && !document.querySelector('.player video')",
    ),
    'private revocation removes both reactions and player',
  );
  await actorRequest('owner', '/videos/' + v.private + '/permissions', 'PUT', {
    email: fixture.people.invited.email,
    allowed: true,
  });
  await navigate();
  await state(false, true, 0);
  await checkMenuLayer();
  await checkMenuLayer(true);
  await c.click(dislike);
  await state(false, false, 0);
  check(
    await c.evaluate(
      `document.querySelector('${dislike}').getBoundingClientRect().width>=40`,
    ),
    'phone reaction remains reachable',
  );
  await c.viewport(320, 700, true);
  check(
    await c.evaluate('document.documentElement.scrollWidth<=innerWidth'),
    'reaction capsule fits narrow phone',
  );
  await control('lost', '__session_fixture');
  jars.clear();
  await c.click(dislike);
  await c.wait(`!${store}.account && !${store}.changing`);
  await c.wait(
    `document.querySelector('${dislike}')?.getAttribute('aria-pressed')==='false'`,
  );
  check(true, 'actual session loss never leaves former personal reaction');
  const errors = safeErrors().filter(
    (x) =>
      !(
        firefox &&
        x.includes('downloadable font: download failed') &&
        x.includes('status=2152398850')
      ),
  );
  check(errors.length === 0, 'no runtime errors: ' + errors.join('|'));
  console.log(
    JSON.stringify({
      profile: 'video-reactions',
      browser: firefox ? 'Firefox' : 'Chromium',
      assertions,
      status: 'pass',
    }),
  );
} catch (error) {
  console.error(
    JSON.stringify({
      profile: 'video-reactions',
      assertions,
      path: await c.evaluate('location.pathname'),
      errors: safeErrors(),
      state: await c.evaluate(
        `JSON.stringify({account:!!${store}.account,changing:${store}.changing,disabled:document.querySelector('${dislike}')?.disabled,pending:document.querySelector('${like}')?.getAttribute('aria-busy'),error:document.querySelector('.video-like .form-error')?.textContent})`,
      ),
      writes: calls
        .filter((x) => x.method === 'PUT')
        .map((x) => ({ path: x.path, status: x.status })),
    }),
  );
  throw error;
} finally {
  failurePath = '';
  stripCsrf = false;
  release();
  await c.close();
  for (const socket of sockets) socket.destroy();
  server.closeAllConnections();
  await new Promise((resolve) => server.close(resolve));
}
