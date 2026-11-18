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
if (!fixture?.videos?.silent) throw new Error('Like fixture not ready');
const firefox = process.argv.includes('--firefox');
const v = fixture.videos,
  tag = fixture.people.owner.tag;
const host = 'http://youtube-frontend-upload-gateway:8080/api/v1';
let readFailure = false,
  writeFailure = false,
  disconnect = false,
  dropCsrf = false,
  holdRead = false,
  holdWriteBefore = false,
  holdWriteAfter = false;
const held = new Set(),
  calls = [],
  writes = [],
  sockets = new Set();
const hold = () => new Promise((resolve) => held.add(resolve));
function release() {
  for (const resolve of held) resolve();
  held.clear();
}
const server = createServer(async (req, res) => {
  try {
    const api = req.url.startsWith('/api/'),
      like = /^\/api\/v1\/channels\/[^/]+\/subscription$/.test(req.url),
      write = like && req.method === 'PUT';
    if (api) calls.push({ path: req.url, method: req.method });
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    let entry;
    if (write) {
      const body = JSON.parse(Buffer.concat(chunks).toString());
      entry = { path: req.url, body, status: undefined };
      writes.push(entry);
      if (holdWriteBefore) await hold();
    }
    if (
      (like && req.method === 'GET' && readFailure) ||
      (write && writeFailure)
    ) {
      if (entry) entry.status = 503;
      res
        .writeHead(503, {
          'content-type': 'application/problem+json',
          'cache-control': 'no-store',
        })
        .end(
          JSON.stringify({
            code: 'unavailable',
            detail: '<img src=x onerror="window.subscriptionInjection=true">',
          }),
        );
      return;
    }
    if (write && disconnect) {
      entry.status = 0;
      res.destroy();
      return;
    }
    const headers = new Headers();
    for (const name of ['cookie', 'content-type', 'x-csrftoken', 'range'])
      if (req.headers[name]) headers.set(name, req.headers[name]);
    if (write && dropCsrf) headers.delete('x-csrftoken');
    const response = await fetch(
      api
        ? 'http://youtube-frontend-upload-gateway:8080' + req.url
        : 'http://node:5173' + req.url,
      {
        method: req.method,
        headers,
        body: chunks.length ? Buffer.concat(chunks) : undefined,
        redirect: 'manual',
      },
    );
    const body = Buffer.from(await response.arrayBuffer());
    if (entry) entry.status = response.status;
    if (like && req.method === 'GET' && holdRead) {
      holdRead = false;
      await hold();
    }
    if (write && holdWriteAfter) await hold();
    res.statusCode = response.status;
    for (const name of ['content-type', 'location', 'cache-control'])
      if (response.headers.has(name))
        res.setHeader(name, response.headers.get(name));
    if (response.headers.getSetCookie().length)
      res.setHeader('set-cookie', response.headers.getSetCookie());
    res.end(body);
  } catch {
    res.writeHead(502, { 'cache-control': 'no-store' }).end();
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
  root = '/@' + tag,
  watch = '/watch?v=' + v.public,
  button = '.subscribe button[aria-pressed]',
  queryClient =
    "Object.values(document.querySelector('#app').__vue_app__._context.provides).find(x=>typeof x?.getQueryCache==='function')",
  jars = new Map();
let assertions = 0;
const check = (value, message) => {
  assert.ok(value, message);
  assertions++;
};
async function until(predicate) {
  for (let i = 0; i < 100; i++) {
    if (await predicate()) return;
    await pause(100);
  }
  throw new Error('Subscription condition timed out');
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
            key === 'id' && typeof value === 'number' ? context.source : value,
          )
        : raw || null,
  };
}
async function api(path, method = 'GET', body) {
  return JSON.parse(
    await c.evaluate(
      `(async()=>{const cookie=document.cookie.split('; ').find(x=>x.startsWith('csrftoken='));const r=await fetch('/api/v1'+${JSON.stringify(path)},{method:${JSON.stringify(method)},headers:{'content-type':'application/json',...(cookie?{'x-csrftoken':decodeURIComponent(cookie.slice(10))}:{})},body:${body === undefined ? 'undefined' : JSON.stringify(JSON.stringify(body))}});return JSON.stringify({status:r.status,data:r.status===204?null:await r.json()});})()`,
    ),
  );
}
const path = (target = tag) => '/channels/' + target + '/subscription';
async function serverState(person = 'stranger', target = tag) {
  const r = await actorRequest(person, path(target));
  assert.equal(r.status, 200);
  return r.data;
}
async function setState(person, value, target = tag) {
  const r = await actorRequest(person, path(target), 'PUT', {
    subscribed: value,
  });
  assert.equal(r.status, 200);
  return r.data;
}
async function state(value) {
  await c.wait(
    `document.querySelector('${button}')?.getAttribute('aria-pressed')==='${value}' && document.querySelector('${button}').getAttribute('aria-disabled')!=='true' && document.querySelector('${button}').textContent.trim()==='${value ? 'Вы подписаны' : 'Подписаться'}'`,
  );
}
async function settled() {
  await c.wait(
    `${queryClient}.getQueryCache().findAll({queryKey:['subscription'],type:'active'}).some(q=>q.state.fetchStatus==='idle'&&q.state.status==='success')`,
  );
}
async function open(to = root) {
  await c.navigate(base + to);
  await c.wait(`!!document.querySelector('.subscribe')`);
}
async function spa(to) {
  await c.evaluate(
    `document.querySelector('#app').__vue_app__.config.globalProperties.$router.push(${JSON.stringify(to)})`,
  );
  await c.wait(
    `location.pathname+location.search===${JSON.stringify(to)} && !!document.querySelector('.subscribe')`,
  );
}
async function login(person = 'stranger', to = root) {
  await c.navigate(base + '/login?returnTo=' + encodeURIComponent(to));
  await submitLogin(person, to);
}
async function submitLogin(person, to) {
  await c.fill('#login-email', fixture.people[person].email);
  await c.fill('#login-password', 'abcdef');
  await c.click('.auth-card button[type=submit]');
  await c.wait(
    `location.pathname+location.search===${JSON.stringify(to)} && !!document.querySelector('.subscribe') && !!document.querySelector('.avatar-button')`,
  );
}
async function logout() {
  await c.click('.avatar-button');
  await c.click('#logout-button');
  await c.wait(
    `!document.querySelector('.avatar-button') && !!document.querySelector('.subscribe')`,
  );
}
async function refetch() {
  await c.evaluate(
    `void ${queryClient}.refetchQueries({queryKey:['subscription'],type:'active'})`,
  );
}
async function retry() {
  await c.click('.subscribe .text-link');
}
async function key(value) {
  if (firefox) {
    await c.key(value === 'Enter' ? '\uE007' : '\uE00D');
    return;
  }
  const code = value === 'Enter' ? 13 : 32;
  await c.send('Input.dispatchKeyEvent', {
    type: 'keyDown',
    key: value === 'Enter' ? 'Enter' : ' ',
    windowsVirtualKeyCode: code,
    text: value === 'Enter' ? '\r' : ' ',
  });
  await c.send('Input.dispatchKeyEvent', {
    type: 'keyUp',
    key: value === 'Enter' ? 'Enter' : ' ',
    windowsVirtualKeyCode: code,
  });
}
try {
  const suffix = crypto.randomUUID().replaceAll('-', '').slice(0, 12);
  fixture.people.twin = {
    name: 'Автор owner',
    tag: 'twin_' + suffix,
    email: 'twin-' + suffix + '@example.com',
  };
  const registered = await actorRequest('owner', '/auth/register', 'POST', {
    ...fixture.people.twin,
    password: 'abcdef',
    password_confirmation: 'abcdef',
  });
  assert.equal(registered.status, 201);
  const twin = fixture.people.twin.tag,
    twinRoot = '/@' + twin;
  for (const person of ['stranger', 'invited']) {
    await setState(person, false);
    await setState(person, false, twin);
  }
  await c.viewport(1280, 900);
  if (!firefox)
    await c.send('Emulation.setFocusEmulationEnabled', { enabled: true });
  await open();
  await state(false);
  check(
    !calls.some((x) => x.path.endsWith('/subscription')) && writes.length === 0,
    'guest sees subscribe without an unauthorized subscription read',
  );
  await c.click(button);
  await c.wait(`location.pathname==='/login'`);
  check(
    await c.evaluate(
      `new URLSearchParams(location.search).get('returnTo')===${JSON.stringify(root)}`,
    ),
    'guest channel action preserves return URL',
  );
  await submitLogin('stranger', root);
  await state(false);
  await settled();
  check(writes.length === 0, 'sign-in never automatically subscribes');
  await c.click(button);
  await state(true);
  await settled();
  check(
    (await serverState()).subscribed,
    'confirmed channel subscription is persisted',
  );
  await spa(watch);
  await state(true);
  await settled();
  check(true, 'watch page reads the same confirmed channel pair');
  await c.click(button);
  await state(false);
  await settled();
  await spa(root);
  await state(false);
  await settled();
  check(
    !(await serverState()).subscribed,
    'cancellation from watch is reflected on channel',
  );
  await c.reload();
  await state(false);
  await settled();
  check(true, 'cancelled state survives reload');

  holdWriteBefore = true;
  const before = writes.length;
  await c.evaluate(
    `for(let i=0;i<4;i++)document.querySelector('${button}').click()`,
  );
  await until(() => held.size === 1);
  check(
    writes.length === before + 1 &&
      (await c.evaluate(
        `document.querySelector('${button}').getAttribute('aria-disabled')==='true' && document.querySelector('${button}').getAttribute('aria-pressed')==='false'`,
      )),
    'rapid clicks send one command while retaining confirmed state',
  );
  check(
    writes.at(-1).path === '/api/v1' + path() &&
      JSON.stringify(writes.at(-1).body) === '{"subscribed":true}',
    'PUT uses the target tag and only subscribed bool',
  );
  holdWriteBefore = false;
  release();
  await state(true);
  await settled();
  check(
    (await serverState()).subscribed,
    'repeated click does not undo accepted target',
  );

  writeFailure = true;
  await c.click(button);
  await c.wait(`!!document.querySelector('.subscribe [role=alert]')`);
  await settled();
  await state(true);
  check(
    !(await c.evaluate(
      `!!window.subscriptionInjection || !!document.querySelector('.subscribe img')`,
    )),
    '503 preserves selected state without executable error detail',
  );
  writeFailure = false;
  check(
    (await serverState()).subscribed,
    'failed cancellation leaves backend unchanged',
  );
  await c.click(button);
  await state(false);
  await settled();
  check(
    !(await c.evaluate(`!!document.querySelector('.subscribe [role=alert]')`)),
    'retry clears error after actual successful cancellation',
  );
  disconnect = true;
  await c.click(button);
  await c.wait(`!!document.querySelector('.subscribe [role=alert]')`);
  await settled();
  await state(false);
  disconnect = false;
  check(
    !(await serverState()).subscribed,
    'network failure cannot create a local or persisted subscription',
  );
  dropCsrf = true;
  await c.click(button);
  await until(() => writes.at(-1).status === 403);
  await c.wait(
    `document.querySelector('.subscribe [role=alert]')?.textContent.includes('Обновите страницу')`,
  );
  await settled();
  await state(false);
  check(
    await c.evaluate(`!!document.querySelector('.channel-page__header')`),
    'CSRF denial keeps public channel and confirmed button',
  );
  dropCsrf = false;
  check(!(await serverState()).subscribed, 'CSRF denial did not mutate pair');

  holdRead = true;
  await refetch();
  await until(() => held.size === 1);
  await c.click(button);
  await state(true);
  release();
  await settled();
  check(
    await c.evaluate(
      `document.querySelector('${button}').getAttribute('aria-pressed')==='true'`,
    ),
    'late GET cannot overwrite confirmed PUT',
  );
  readFailure = true;
  await refetch();
  await c.wait(
    `!!document.querySelector('.subscribe .text-link') && !document.querySelector('${button}')`,
  );
  check(
    await c.evaluate(`!!document.querySelector('.channel-page__header')`),
    'failed refresh removes actionable stale subscription while preserving channel',
  );
  readFailure = false;
  await retry();
  await state(true);
  await settled();
  check(true, 'state retry restores actual selected value');
  readFailure = true;
  await open(watch);
  await c.wait(`!!document.querySelector('.subscribe .text-link')`);
  check(
    !(await c.evaluate(`!!document.querySelector('${button}')`)),
    'initial subscription error shows retry without false subscribe state',
  );
  readFailure = false;
  await retry();
  await state(true);
  await settled();

  await setState('stranger', false);
  holdRead = true;
  await spa(root);
  await until(() => held.size === 1);
  check(
    await c.evaluate(
      `!!document.querySelector('.subscribe [role=status]') && !document.querySelector('${button}')`,
    ),
    'same-pair route transition waits for fresh read instead of flashing old selected state',
  );
  release();
  await state(false);
  await settled();
  holdWriteAfter = true;
  await c.click(button);
  await until(() => held.size === 1 && writes.at(-1).status === 200);
  await spa(watch);
  await state(true);
  holdWriteAfter = false;
  release();
  await settled();
  check(
    (await serverState()).subscribed,
    'navigation reads committed subscription even if old PUT response was cancelled',
  );

  holdRead = true;
  await refetch();
  await until(() => held.size === 1);
  await spa(twinRoot);
  await state(false);
  release();
  await settled();
  check(
    await c.evaluate(
      `document.querySelector('.channel-page h1').textContent==='Автор owner' && document.querySelector('.channel-page__identity p').textContent==='@${twin}' && document.querySelector('${button}').getAttribute('aria-pressed')==='false'`,
    ),
    'late read cannot confuse same-named channels with different tags',
  );
  check(
    await c.evaluate(
      `${queryClient}.getQueryCache().findAll({queryKey:['subscription']}).every(q=>q.queryKey[2]!=='${tag}')`,
    ),
    'leaving removes the inactive subscription snapshot',
  );
  await open(root);
  await state(true);
  await settled();
  await c.click(button);
  await state(false);
  await settled();
  holdWriteAfter = true;
  await c.click(button);
  await until(() => held.size === 1 && writes.at(-1).status === 200);
  await spa(twinRoot);
  await state(false);
  holdWriteAfter = false;
  release();
  await pause(200);
  check(
    !(await serverState('stranger', twin)).subscribed &&
      !(await c.evaluate(
        `!!document.querySelector('.subscribe [role=alert]')`,
      )),
    'late successful write cannot select or error another channel',
  );
  await open(root);
  await state(true);
  await settled();
  await c.click(button);
  await state(false);
  await settled();
  holdWriteBefore = true;
  writeFailure = true;
  await c.click(button);
  await until(() => held.size === 1);
  await spa(twinRoot);
  await state(false);
  holdWriteBefore = false;
  release();
  await until(() => writes.at(-1).status === 503);
  writeFailure = false;
  await pause(200);
  check(
    !(await c.evaluate(`!!document.querySelector('.subscribe [role=alert]')`)),
    'late failed write cannot add another channel error',
  );

  await open(root);
  await state(false);
  await settled();
  holdWriteAfter = true;
  await c.click(button);
  await until(() => held.size === 1 && writes.at(-1).status === 200);
  await logout();
  await state(false);
  await login('invited');
  await state(false);
  holdWriteAfter = false;
  release();
  await settled();
  check(
    (await serverState()).subscribed &&
      !(await serverState('invited')).subscribed,
    'late result after logout and account switch never selects the next viewer',
  );
  await login('stranger');
  await state(true);
  await settled();
  await api('/auth/logout', 'POST');
  await refetch();
  await c.wait(`!document.querySelector('.avatar-button')`);
  await state(false);
  check(
    (await serverState()).subscribed,
    'GET 401 clears UI selection without cancelling persisted subscription',
  );
  await login('stranger');
  await state(true);
  await settled();
  await api('/auth/logout', 'POST');
  await c.click(button);
  await c.wait(`!document.querySelector('.avatar-button')`);
  await state(false);
  check(
    writes.at(-1).status === 401 && (await serverState()).subscribed,
    'PUT 401 cannot remove another confirmed pair',
  );

  const selfBefore = writes.length;
  await c.click(button);
  await c.wait(`location.pathname==='/login'`);
  await submitLogin('owner', root);
  await settled();
  check(
    !(await c.evaluate(`!!document.querySelector('.subscribe button')`)) &&
      writes.length === selfBefore,
    'own channel offers no self-subscription after guest return',
  );
  await spa(watch);
  await settled();
  check(
    !(await c.evaluate(`!!document.querySelector('.subscribe button')`)),
    'own video also hides self-subscription',
  );

  await login('stranger', watch);
  await state(true);
  await settled();
  check(
    (await actorRequest('stranger', '/videos/' + v.private)).status === 404 &&
      (
        await actorRequest(
          'stranger',
          '/videos/' + v.private + '/hls/master.m3u8',
        )
      ).status === 404,
    'active subscription grants neither private metadata nor manifest',
  );
  await spa(root);
  await state(true);
  await c.wait(`!!document.querySelector('.video-card')`);
  check(
    !(await c.evaluate(
      `!!document.querySelector('.video-card__preview[href="/watch?v=${v.private}"]')`,
    )),
    'subscription does not expand public channel catalogue',
  );
  await login('invited', '/watch?v=' + v.private);
  await state(false);
  await settled();
  await c.click(button);
  await state(true);
  await settled();
  await c.click(button);
  await state(false);
  await settled();
  check(
    (await actorRequest('invited', '/videos/' + v.private)).status === 200 &&
      (await c.evaluate(
        `!!document.querySelector('.player video') && !!document.querySelector('.watch-page h1')`,
      )),
    'cancelling subscription does not remove an independent private invitation',
  );

  await c.viewport(390, 844, true);
  await c.evaluate(`document.querySelector('${button}').focus()`);
  await key('Enter');
  await state(true);
  await settled();
  check(
    await c.evaluate(
      `document.activeElement===document.querySelector('${button}')`,
    ),
    'phone keyboard subscribe keeps focus after pending state',
  );
  await key('Space');
  await state(false);
  await settled();
  check(true, 'Space cancels subscription without refocusing');
  const errors = c.errors.filter(
    (x) =>
      !(
        firefox &&
        x.includes('downloadable font: download failed') &&
        x.includes('status=2152398850')
      ),
  );
  check(
    errors.length === 0,
    'no unexpected browser runtime errors: ' + errors.join(' | '),
  );
  check(
    writes.every(
      (x) =>
        Object.keys(x.body).length === 1 &&
        typeof x.body.subscribed === 'boolean',
    ),
    'every command contains one boolean without subscriber or target IDs in body',
  );
  console.log(
    JSON.stringify({
      profile: 'subscriptions',
      browser: firefox ? 'Firefox' : 'Chromium',
      assertions,
      status: 'pass',
    }),
  );
} catch (error) {
  console.error(
    JSON.stringify({
      profile: 'subscriptions',
      assertions,
      writes: writes.slice(-4),
      path: await c.evaluate('location.pathname+location.search'),
      state: await c.evaluate(
        "document.querySelector('.subscribe')?.textContent",
      ),
      errors: c.errors,
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
