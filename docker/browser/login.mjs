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
if (!fixture?.videos?.silent) throw Error('Login fixture not ready');
const firefox = process.argv.includes('--firefox'),
  v = fixture.videos,
  host = 'http://youtube-frontend-upload-gateway:8080/api/v1';
const calls = [],
  attempts = [],
  held = new Set(),
  sockets = new Set();
let refusal = 0,
  dropCsrf = false,
  disconnect = false,
  holdBefore = false,
  holdAfter = false;
const hold = () => new Promise((resolve) => held.add(resolve));
const release = () => {
  for (const resolve of held) resolve();
  held.clear();
};
const server = createServer(async (req, res) => {
  try {
    const login = req.url === '/api/v1/auth/login' && req.method === 'POST',
      api = req.url.startsWith('/api/');
    const call = { path: req.url, method: req.method, status: undefined };
    if (api) calls.push(call);
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    if (login) {
      const data = JSON.parse(Buffer.concat(chunks).toString());
      attempts.push({
        keys: Object.keys(data),
        emailLength: data.email.length,
        passwordLength: data.password.length,
      });
    }
    if (login && holdBefore) await hold();
    if (login && disconnect) {
      call.status = 0;
      res.destroy();
      return;
    }
    if (login && refusal) {
      call.status = refusal;
      res
        .writeHead(refusal, {
          'content-type': 'application/problem+json',
          'cache-control': 'no-store',
        })
        .end(
          JSON.stringify({
            code: 'unavailable',
            detail:
              '<img src=x onerror=window.loginInjection=1> /run/secrets/internal',
          }),
        );
      return;
    }
    const headers = new Headers();
    for (const key of ['cookie', 'content-type', 'x-csrftoken', 'range'])
      if (req.headers[key]) headers.set(key, req.headers[key]);
    if (login && dropCsrf) headers.delete('x-csrftoken');
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
    const data = Buffer.from(await response.arrayBuffer());
    if (login && holdAfter) await hold();
    res.statusCode = response.status;
    for (const key of ['content-type', 'cache-control', 'location'])
      if (response.headers.has(key))
        res.setHeader(key, response.headers.get(key));
    if (response.headers.getSetCookie().length)
      res.setHeader('set-cookie', response.headers.getSetCookie());
    res.end(data);
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
const button = '.auth-card button[type=submit]',
  queryClient =
    "Object.values(document.querySelector('#app').__vue_app__._context.provides).find(x=>typeof x?.getQueryCache==='function')";
const padded = {
  email: 'login-' + crypto.randomUUID().slice(0, 12) + '@example.com',
  password: ' Пароль 123 с пробелами ',
};
let assertions = 0;
const check = (value, message) => {
  assert.ok(value, message);
  assertions++;
};
const safeErrors = () =>
  c.errors.map((x) => x.replace(/https?:\/\/\S+/g, '[URL redacted]'));
async function until(fn) {
  for (let i = 0; i < 200; i++) {
    if (await fn()) return;
    await pause(100);
  }
  throw Error('Login condition timed out');
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
function loginPath(target) {
  return (
    '/login' +
    (target === undefined ? '' : '?returnTo=' + encodeURIComponent(target))
  );
}
async function open(target) {
  await c.navigate(base + loginPath(target));
  await c.wait(
    `!!document.querySelector('#login-email') && !document.querySelector('${button}').disabled`,
  );
}
async function fill(email = fixture.people.invited.email, password = 'abcdef') {
  await c.fill('#login-email', email);
  await c.fill('#login-password', password);
}
async function submit() {
  await c.click(button);
}
async function error() {
  await c.wait(
    `!!document.querySelector('#login-error') && !document.querySelector('${button}').disabled && document.activeElement.id==='login-error'`,
  );
}
async function logged(path = '/') {
  await c.wait(
    `location.pathname===${JSON.stringify(path)} && !document.querySelector('#login-password')`,
  );
}
async function reset(target) {
  await api('/auth/logout', 'POST');
  await open(target);
}
async function keyEnter() {
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
  // This account proves that login preserves significant password whitespace and Unicode.
  const seed = await fetch(host + '/auth/me'),
    jar = new Map();
  for (const cookie of seed.headers.getSetCookie()) {
    const pair = cookie.split(';')[0],
      i = pair.indexOf('=');
    jar.set(pair.slice(0, i), pair.slice(i + 1));
  }
  const registration = await fetch(host + '/auth/register', {
    method: 'POST',
    headers: {
      cookie: [...jar].map(([k, v]) => k + '=' + v).join('; '),
      'x-csrftoken': decodeURIComponent(jar.get('csrftoken')),
      'content-type': 'application/json',
    },
    body: JSON.stringify({
      name: 'Проверка Входа',
      email: padded.email,
      tag: 'login_' + crypto.randomUUID().replaceAll('-', '').slice(0, 12),
      password: padded.password,
      password_confirmation: padded.password,
    }),
  });
  assert.equal(registration.status, 201);
  await c.viewport(1280, 900);
  if (!firefox)
    await c.send('Emulation.setFocusEmulationEnabled', { enabled: true });
  await open();
  await submit();
  check(
    attempts.length === 0 &&
      (await c.evaluate(
        "!document.querySelector('.auth-card form').checkValidity()",
      )),
    'empty required fields prevent network login',
  );
  await fill('invalid-address', 'abcdef');
  await submit();
  check(
    attempts.length === 0,
    'native email validation blocks malformed email',
  );
  await fill(fixture.people.invited.email, 'wrong-password');
  await submit();
  await error();
  check(
    (await api('/auth/me')).status === 401 &&
      (await c.evaluate(
        "document.querySelector('#login-error').textContent.includes('Неверная почта или пароль')",
      )),
    'wrong password leaves guest with generic actual API rejection',
  );
  check(
    await c.evaluate(
      "!document.querySelector('#login-email').disabled && document.querySelector('#login-password').type==='password' && document.querySelector('#login-password').getAttribute('aria-describedby')==='login-error'",
    ),
    'failure unlocks inputs and associates focused error without revealing password',
  );
  const wrongText = await c.evaluate(
    "document.querySelector('#login-error').textContent",
  );
  await fill('absent-' + crypto.randomUUID() + '@example.com', 'abcdef');
  await submit();
  await error();
  check(
    (await c.evaluate("document.querySelector('#login-error').textContent")) ===
      wrongText,
    'unknown account and wrong password have indistinguishable feedback',
  );
  await fill(padded.email, padded.password.trim());
  await submit();
  await error();
  check(
    (await api('/auth/me')).status === 401,
    'trimmed password cannot authenticate account with significant spaces',
  );
  await fill(padded.email.toUpperCase(), padded.password);
  await submit();
  await logged();
  check(
    (await api('/auth/me')).data.email === padded.email,
    'real login normalizes email and preserves Unicode/spaces in password',
  );
  check(
    await c.evaluate("!!document.querySelector('.avatar-button')"),
    'successful login updates application header',
  );

  await reset('/results?search_query=F06%20return#return');
  refusal = 503;
  await fill();
  await submit();
  await error();
  check(
    (await api('/auth/me')).status === 401 &&
      (await c.evaluate(
        "!window.loginInjection && !document.body.textContent.includes('/run/secrets/internal')",
      )),
    '503 has honest guest state and never renders server detail HTML/paths',
  );
  check(
    await c.evaluate(
      `document.querySelector('#login-email').value===${JSON.stringify(fixture.people.invited.email)} && document.querySelector('#login-password').value==='abcdef'`,
    ),
    'retryable error preserves entered credentials in local form',
  );
  refusal = 0;
  disconnect = true;
  await submit();
  await error();
  check(
    (await api('/auth/me')).status === 401,
    'connection loss before login cannot become successful authentication',
  );
  disconnect = false;
  dropCsrf = true;
  await submit();
  await error();
  check(
    calls.filter((x) => x.path === '/api/v1/auth/login').at(-1).status ===
      403 && (await api('/auth/me')).status === 401,
    'real CSRF refusal has no authenticated session',
  );
  dropCsrf = false;
  holdBefore = true;
  const before = attempts.length;
  await submit();
  await until(() => held.size > 0);
  await c.evaluate(
    "(()=>{const f=document.querySelector('.auth-card form');for(let i=0;i<4;i++)f.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));})()",
  );
  check(
    attempts.length === before + 1 &&
      (await c.evaluate(
        "document.querySelector('.auth-card form').getAttribute('aria-busy')==='true' && document.querySelector('#login-email').disabled && document.querySelector('#login-password').disabled",
      )),
    'pending login is announced and duplicate submit is blocked',
  );
  holdBefore = false;
  release();
  await logged('/results');
  check(
    await c.evaluate(
      "new URLSearchParams(location.search).get('search_query')==='F06 return' && location.hash==='#return'",
    ),
    'successful retry preserves internal query and hash',
  );

  for (const target of [
    '//invalid.example/path',
    '/%2f%2finvalid.example/',
    '/LOGIN/',
    '/%6cogin',
    '/register?returnTo=/watch',
  ]) {
    await reset(target);
    await fill();
    await submit();
    await logged();
    check(
      await c.evaluate(
        `location.origin===${JSON.stringify(base)} && location.pathname==='/'`,
      ),
      'unsafe or looping destination falls back to home: ' + target,
    );
  }
  await reset();
  await c.navigate(base + '/login?returnTo=/watch&returnTo=/studio/upload');
  await fill();
  await submit();
  await logged();
  check(
    await c.evaluate("location.pathname==='/'"),
    'duplicate returnTo cannot select an arbitrary destination',
  );

  await reset('/watch?v=' + v.public);
  await c.click('.auth-card__actions a');
  await c.wait("location.pathname==='/register'");
  check(
    await c.evaluate(
      `new URLSearchParams(location.search).get('returnTo')==='/watch?v=${v.public}'`,
    ),
    'registration link preserves safe original scenario',
  );
  await c.click('.auth-card__actions a');
  await c.wait("location.pathname==='/login'");
  check(
    await c.evaluate(
      `new URLSearchParams(location.search).get('returnTo')==='/watch?v=${v.public}' && document.querySelector('#login-password').value===''`,
    ),
    'return from registration keeps destination and a fresh password field',
  );

  await c.navigate(base + '/watch?v=' + v.public);
  await c.wait("!!document.querySelector('.video-like button[aria-pressed]')");
  const likeBefore = calls.filter(
    (x) => x.method === 'PUT' && x.path.endsWith('/like'),
  ).length;
  await c.click('.video-like button[aria-pressed]');
  await c.wait("location.pathname==='/login'");
  await fill();
  await submit();
  await logged('/watch');
  await c.wait("!!document.querySelector('.video-like button[aria-pressed]')");
  check(
    (await c.evaluate(
      `new URLSearchParams(location.search).get('v')==='${v.public}'`,
    )) &&
      calls.filter((x) => x.method === 'PUT' && x.path.endsWith('/like'))
        .length === likeBefore,
    'login returns from guest like without applying mutation',
  );

  await api('/auth/logout', 'POST');
  await c.navigate(base + '/@' + fixture.people.owner.tag);
  await c.wait("!!document.querySelector('.subscribe button[aria-pressed]')");
  const subscriptions = calls.filter(
    (x) => x.method === 'PUT' && x.path.endsWith('/subscription'),
  ).length;
  await c.click('.subscribe button[aria-pressed]');
  await c.wait("location.pathname==='/login'");
  await fill();
  await submit();
  await logged('/@' + fixture.people.owner.tag);
  check(
    calls.filter((x) => x.method === 'PUT' && x.path.endsWith('/subscription'))
      .length === subscriptions,
    'login returns from guest subscription without subscribing',
  );

  await api('/auth/logout', 'POST');
  await c.navigate(base + '/studio/upload');
  await c.wait("!!document.querySelector('.upload-page .page-state a')");
  const drafts = calls.filter(
    (x) => x.method === 'POST' && x.path === '/api/v1/videos',
  ).length;
  await c.click('.upload-page .page-state a');
  await c.wait("location.pathname==='/login'");
  await fill();
  await submit();
  await logged('/studio/upload');
  await c.wait("!!document.querySelector('.upload-drop')");
  check(
    calls.filter((x) => x.method === 'POST' && x.path === '/api/v1/videos')
      .length === drafts,
    'login restores upload chooser without creating or publishing content',
  );

  await reset('/studio/upload');
  holdAfter = true;
  await fill();
  await submit();
  await until(() => held.size > 0);
  await spa('/results?search_query=keep-route');
  holdAfter = false;
  release();
  await until(
    () =>
      calls.filter((x) => x.path === '/api/v1/auth/login').at(-1).status ===
      200,
  );
  await c.wait("!!document.querySelector('.avatar-button')");
  check(
    await c.evaluate(
      "location.pathname==='/results' && location.search==='?search_query=keep-route'",
    ),
    'late successful login cannot redirect after leaving form',
  );
  await spa('/login');
  await c.wait("!!document.querySelector('#login-password')");
  check(
    await c.evaluate("document.querySelector('#login-password').value===''"),
    'reopened login never restores completed password',
  );

  await reset('/watch?v=' + v.public);
  holdBefore = true;
  refusal = 503;
  await fill();
  await submit();
  await until(() => held.size > 0);
  const oldAttempts = attempts.length;
  await spa('/login?returnTo=' + encodeURIComponent('/studio/upload'));
  await c.wait("document.querySelector('#login-password').value===''");
  await c.evaluate(
    "document.querySelector('.auth-card form').dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}))",
  );
  check(
    attempts.length === oldAttempts,
    'changed login context cannot submit while session change is still in flight',
  );
  holdBefore = false;
  release();
  await c.wait(`!document.querySelector('${button}').disabled`);
  check(
    await c.evaluate(
      "location.pathname==='/login' && !document.querySelector('#login-error') && document.querySelector('#login-password').value===''",
    ),
    'late error cannot populate changed returnTo form',
  );
  refusal = 0;
  await fill();
  await submit();
  await logged('/studio/upload');
  check(
    true,
    'new form can explicitly authenticate and use its own destination',
  );

  await reset('/watch?v=' + v.public);
  holdAfter = true;
  await fill();
  await submit();
  await until(() => held.size > 0);
  await spa('/login?returnTo=' + encodeURIComponent('/studio/upload'));
  holdAfter = false;
  release();
  await c.wait(`!document.querySelector('${button}').disabled`);
  check(
    (await api('/auth/me')).status === 200 &&
      (await c.evaluate(
        "location.pathname==='/login' && new URLSearchParams(location.search).get('returnTo')==='/studio/upload' && !document.querySelector('#login-error') && document.querySelector('#login-password').value===''",
      )),
    'late success updates real session without redirecting changed login context',
  );

  await api('/auth/logout', 'POST');
  await c.navigate(base);
  await spa(loginPath('/results?search_query=history'));
  await fill();
  await submit();
  await logged('/results');
  const historyAttempts = attempts.length;
  await c.evaluate('history.back()');
  await c.wait("location.pathname==='/'");
  check(
    attempts.length === historyAttempts,
    'login replaces history entry and Back never resubmits credentials',
  );
  check(
    await c.evaluate(
      `(()=>{const texts=[JSON.stringify({...localStorage}),JSON.stringify({...sessionStorage}),JSON.stringify(${queryClient}.getQueryCache().getAll().map(q=>q.state.data)),JSON.stringify(${queryClient}.getMutationCache().getAll().map(m=>m.state.variables)),location.href];return texts.every(text=>!text.includes(${JSON.stringify(padded.password)})&&!text.includes('abcdef'));})()`,
    ),
    'credentials stay out of URL, browser storage and Query/Mutation cache',
  );
  check(
    attempts.every(
      (x) => JSON.stringify(x.keys.sort()) === '["email","password"]',
    ),
    'every login command sends only email and password',
  );

  await c.viewport(390, 844, true);
  await reset('/watch?v=' + v.public);
  await fill(fixture.people.invited.email, 'bad-password');
  await c.evaluate("document.querySelector('#login-password').focus()");
  await keyEnter();
  await error();
  check(
    await c.evaluate("document.activeElement.id==='login-error'"),
    'phone keyboard failure focuses announced error after unlocking',
  );
  await fill();
  await c.evaluate("document.querySelector('#login-password').focus()");
  await keyEnter();
  await logged('/watch');
  check(
    await c.evaluate(
      `new URLSearchParams(location.search).get('v')==='${v.public}'`,
    ),
    'phone keyboard login restores original video',
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
      profile: 'login',
      browser: firefox ? 'Firefox' : 'Chromium',
      assertions,
      status: 'pass',
    }),
  );
} catch (error) {
  console.error(
    JSON.stringify({
      profile: 'login',
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
}
