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
  likePath = (id = v.public) => '/api/v1/videos/' + id + '/like';
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
      like = /^\/api\/v1\/videos\/\d+\/like$/.test(req.url),
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
            detail: '<img src=x onerror="window.likeInjection=true">',
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
  button = '.video-like button[aria-pressed]',
  queryClient =
    "Object.values(document.querySelector('#app').__vue_app__._context.provides).find(x=>typeof x?.getQueryCache==='function')";
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
  throw new Error('Like condition timed out');
}
async function api(path, method = 'GET', body) {
  return JSON.parse(
    await c.evaluate(
      `(async()=>{const cookie=document.cookie.split('; ').find(x=>x.startsWith('csrftoken='));const r=await fetch('/api/v1'+${JSON.stringify(path)},{method:${JSON.stringify(method)},headers:{'content-type':'application/json',...(cookie?{'x-csrftoken':decodeURIComponent(cookie.slice(10))}:{})},body:${body === undefined ? 'undefined' : JSON.stringify(JSON.stringify(body))}});return JSON.stringify({status:r.status,data:r.status===204?null:await r.json()});})()`,
    ),
  );
}
async function navigate(id = v.public) {
  await c.navigate(base + '/watch?v=' + id);
  await c.wait(`!!document.querySelector('${button}')`);
}
async function spa(path) {
  await c.evaluate(
    `document.querySelector('#app').__vue_app__.config.globalProperties.$router.push(${JSON.stringify(path)})`,
  );
}
async function state(liked, count) {
  await c.wait(
    `document.querySelector('${button}')?.getAttribute('aria-pressed')==='${liked}' && !document.querySelector('${button}').disabled && document.querySelector('${button}').getAttribute('aria-disabled')!=='true' && document.querySelector('${button}').textContent.trim()==='${count}'`,
  );
}
async function login(person = 'owner', returnTo = '/watch?v=' + v.public) {
  await c.navigate(base + '/login?returnTo=' + encodeURIComponent(returnTo));
  await submitLogin(person, returnTo);
}
async function submitLogin(person, returnTo) {
  await c.fill('#login-email', fixture.people[person].email);
  await c.fill('#login-password', 'abcdef');
  await c.click('.auth-card button[type=submit]');
  await c.wait(
    `location.pathname+location.search===${JSON.stringify(returnTo)} && !!document.querySelector('.avatar-button') && !!document.querySelector('${button}')`,
  );
  await c.wait(
    `${queryClient}.getQueryCache().findAll({queryKey:['video-like'],type:'active'}).some(q=>q.state.data?.can_like && q.state.fetchStatus==='idle')`,
  );
}
async function logout() {
  await c.click('.avatar-button');
  await c.click('#logout-button');
  await c.wait(
    `!document.querySelector('.avatar-button') && (!!document.querySelector('${button}') || document.body.textContent.includes('Видео недоступно'))`,
  );
}
async function refetchLikes() {
  await c.evaluate(
    `void ${queryClient}.refetchQueries({queryKey:['video-like'],type:'active'})`,
  );
}
async function actorRequest(person, path, method = 'GET', body) {
  const host = 'http://youtube-frontend-upload-gateway:8080/api/v1',
    cookies = new Map();
  const absorb = (r) =>
    r.headers.getSetCookie().forEach((value) => {
      const pair = value.split(';')[0],
        index = pair.indexOf('=');
      cookies.set(pair.slice(0, index), pair.slice(index + 1));
    });
  absorb(await fetch(host + '/auth/me'));
  const headers = () => ({
    cookie: [...cookies].map(([key, value]) => key + '=' + value).join('; '),
    'x-csrftoken': decodeURIComponent(cookies.get('csrftoken')),
    'content-type': 'application/json',
  });
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
  const response = await fetch(host + path, {
    method,
    headers: headers(),
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  assert.equal(response.status, 200);
  return response.json();
}
async function visibility(value) {
  await actorRequest('owner', '/videos/' + v.public + '/settings', 'PUT', {
    title: 'Проверка лайков',
    description: 'Описание',
    visibility: value,
  });
}
try {
  await visibility('public');
  for (const person of ['owner', 'invited', 'stranger']) {
    for (const id of [
      v.public,
      v.silent,
      ...(person === 'stranger' ? [] : [v.private]),
    ])
      await actorRequest(person, '/videos/' + id + '/like', 'PUT', {
        liked: false,
      });
  }
  await c.viewport(1280, 900);
  await navigate();
  await state(false, 0);
  check(writes.length === 0, 'guest sees real zero without a command');
  await c.click(button);
  await c.wait(`location.pathname==='/login'`);
  check(
    await c.evaluate(
      `new URLSearchParams(location.search).get('returnTo')==='/watch?v=${v.public}'`,
    ),
    'guest login preserves exact video URL',
  );
  await submitLogin('owner', '/watch?v=' + v.public);
  await state(false, 0);
  check(
    writes.length === 0,
    'login does not apply the guest action automatically',
  );

  holdRead = true;
  await refetchLikes();
  await until(() => held.size === 1);
  await c.click(button);
  await state(true, 1);
  release();
  await pause(500);
  check(
    await c.evaluate(
      `document.querySelector('${button}').getAttribute('aria-pressed')==='true'`,
    ),
    'late GET cannot overwrite confirmed PUT',
  );
  await c.reload();
  await state(true, 1);
  check(
    (await api('/videos/' + v.public + '/like')).data.liked,
    'own video like persists on reload',
  );
  await c.click(button);
  await state(false, 0);
  check(
    (await api('/videos/' + v.public + '/like')).data.like_count === 0,
    'removal persists at zero',
  );

  holdWriteBefore = true;
  const before = writes.length;
  await c.evaluate(
    `for(let i=0;i<4;i++)document.querySelector('${button}').click()`,
  );
  await until(() => held.size === 1);
  check(
    writes.length === before + 1 &&
      (await c.evaluate(
        `document.querySelector('${button}').getAttribute('aria-disabled')==='true' && document.querySelector('${button}').getAttribute('aria-pressed')==='false' && document.querySelector('${button}').textContent.trim()==='0'`,
      )),
    'rapid clicks send one command and keep confirmed state while pending',
  );
  check(
    writes.at(-1).path === likePath() &&
      JSON.stringify(writes.at(-1).body) === '{"liked":true}',
    'PUT uses exact int64 URL and only target bool',
  );
  holdWriteBefore = false;
  release();
  await state(true, 1);
  check(
    (await api('/videos/' + v.public + '/like')).data.like_count === 1,
    'double click creates one persisted like',
  );

  writeFailure = true;
  await c.click(button);
  await c.wait(
    `!!document.querySelector('.video-like [role=alert]') && !document.querySelector('${button}').disabled && document.querySelector('${button}').getAttribute('aria-disabled')!=='true'`,
  );
  await state(true, 1);
  check(
    !(await c.evaluate(
      `!!window.likeInjection || !!document.querySelector('.video-like img')`,
    )),
    'server error detail is neither rendered nor executed',
  );
  writeFailure = false;
  check(
    (await api('/videos/' + v.public + '/like')).data.liked,
    'failed command did not change backend',
  );
  await c.click(button);
  await state(false, 0);
  check(
    !(await c.evaluate(`!!document.querySelector('.video-like [role=alert]')`)),
    'retry clears error only after a real successful command',
  );

  disconnect = true;
  await c.click(button);
  await c.wait(
    `!!document.querySelector('.video-like [role=alert]') && !document.querySelector('${button}').disabled && document.querySelector('${button}').getAttribute('aria-disabled')!=='true'`,
  );
  await state(false, 0);
  disconnect = false;
  check(
    !(await api('/videos/' + v.public + '/like')).data.liked,
    'network failure leaves confirmed zero and backend unchanged',
  );

  dropCsrf = true;
  await c.click(button);
  await until(() => writes.at(-1).status === 403);
  await c.wait(
    `!!document.querySelector('.video-like [role=alert]') && !document.querySelector('${button}').disabled && document.querySelector('${button}').getAttribute('aria-disabled')!=='true'`,
  );
  await state(false, 0);
  check(
    await c.evaluate(
      `!!document.querySelector('.player video') && document.querySelector('.video-like [role=alert]').textContent.includes('Обновите страницу')`,
    ),
    'CSRF failure keeps permitted video and confirmed like',
  );
  dropCsrf = false;
  check(
    !(await api('/videos/' + v.public + '/like')).data.liked,
    'CSRF denial did not mutate backend',
  );

  readFailure = true;
  await refetchLikes();
  await c.wait(
    `document.querySelector('.video-like button')?.textContent.includes('Повторить')`,
  );
  check(
    !(await c.evaluate(`!!document.querySelector('${button}')`)),
    'failed state refresh offers retry without actionable stale count',
  );
  readFailure = false;
  await c.click('.video-like button');
  await state(false, 0);
  check(true, 'state retry loads confirmed count');
  readFailure = true;
  await c.reload();
  await c.wait(
    `document.querySelector('.video-like button')?.textContent.includes('Повторить')`,
  );
  readFailure = false;
  await c.click('.video-like button');
  await state(false, 0);
  check(true, 'initial GET failure can recover');

  holdWriteAfter = true;
  await c.click(button);
  await until(() => held.size === 1 && writes.at(-1).status === 200);
  await spa('/watch?v=' + v.silent);
  await state(false, 0);
  holdWriteAfter = false;
  release();
  await pause(400);
  check(
    await c.evaluate(
      `document.querySelector('${button}').getAttribute('aria-pressed')==='false' && !document.querySelector('.video-like [role=alert]')`,
    ),
    "late write cannot change another video's state or errors",
  );
  check(
    await c.evaluate(
      `${queryClient}.getQueryCache().findAll({queryKey:['video-like']}).every(q=>q.queryKey[2]!=='${v.public}')`,
    ),
    'leaving removes inactive like snapshot',
  );
  await navigate();
  await state(true, 1);
  check(
    (await api('/videos/' + v.public + '/like')).data.liked,
    'cancelled response does not pretend the committed server action was rolled back',
  );

  holdWriteAfter = true;
  await c.click(button);
  await until(() => held.size === 1 && writes.at(-1).status === 200);
  await logout();
  await state(false, 0);
  await login('invited');
  await state(false, 0);
  holdWriteAfter = false;
  release();
  await pause(400);
  check(
    await c.evaluate(
      `document.querySelector('${button}').getAttribute('aria-pressed')==='false' && !document.querySelector('.video-like [role=alert]')`,
    ),
    'late result after logout and account switch never selects the new viewer',
  );
  await c.click(button);
  await state(true, 1);
  await logout();
  await state(false, 1);
  check(true, 'guest sees aggregate but no viewer selection');
  await login();
  await state(false, 1);
  await c.click(button);
  await state(true, 2);
  check(true, 'different viewers contribute independent likes');

  await api('/auth/logout', 'POST');
  await c.click(button);
  await c.wait(
    `!document.querySelector('.avatar-button') && !!document.querySelector('${button}')`,
  );
  await state(false, 2);
  check(
    writes.at(-1).status === 401,
    'real expired session clears selection and cannot remove a persisted like',
  );

  await login('invited', '/watch?v=' + v.private);
  await state(false, 0);
  await c.click(button);
  await state(true, 1);
  check(
    (await api('/videos/' + v.private + '/like')).data.liked,
    'invited viewer can like a private video',
  );
  await logout();
  await c.navigate(base + '/watch?v=' + v.private);
  await c.wait(`document.body.textContent.includes('Видео недоступно')`);
  check(
    !(await c.evaluate(`!!document.querySelector('.video-like')`)),
    'a like gives guests no private access or counter',
  );

  await login('stranger');
  await state(false, 2);
  holdWriteBefore = true;
  await c.click(button);
  await until(() => held.size === 1);
  await visibility('private');
  holdWriteBefore = false;
  release();
  await until(() => writes.at(-1).status === 404);
  await c.wait(
    `document.body.textContent.includes('Видео недоступно') && !document.querySelector('.player video')`,
  );
  check(
    writes.at(-1).status === 404 &&
      !(await c.evaluate(`!!document.querySelector('.video-like')`)),
    'real access revocation during PUT removes player and like count',
  );
  await visibility('public');
  await navigate();
  await state(false, 2);
  check(
    !(await api('/videos/' + v.public + '/like')).data.liked,
    'denied write left new viewer unchanged',
  );

  holdRead = true;
  await refetchLikes();
  await until(() => held.size === 1);
  await spa('/watch?v=' + v.silent);
  await state(false, 0);
  release();
  await pause(300);
  check(
    await c.evaluate(
      `document.querySelector('${button}').textContent.trim()==='0'`,
    ),
    'late GET cannot transfer aggregate to another video',
  );
  await navigate();
  await visibility('private');
  await refetchLikes();
  await c.wait(
    `document.body.textContent.includes('Видео недоступно') && !document.querySelector('.video-like')`,
  );
  check(true, 'real access revocation on GET hides former state');
  await visibility('public');

  await navigate();
  await state(false, 2);
  holdWriteBefore = true;
  writeFailure = true;
  await c.click(button);
  await until(() => held.size === 1);
  await spa('/watch?v=' + v.silent);
  await state(false, 0);
  holdWriteBefore = false;
  release();
  await until(() => writes.at(-1).status === 503);
  writeFailure = false;
  await pause(300);
  check(
    !(await c.evaluate(`!!document.querySelector('.video-like [role=alert]')`)),
    'late failed command cannot add an error to the next video',
  );

  await c.viewport(390, 844, true);
  await navigate();
  await state(false, 2);
  await c.evaluate(`document.querySelector('${button}').focus()`);
  if (firefox) await c.key('\uE007');
  else {
    await c.send('Input.dispatchKeyEvent', {
      type: 'keyDown',
      key: 'Enter',
      code: 'Enter',
      windowsVirtualKeyCode: 13,
      text: '\r',
    });
    await c.send('Input.dispatchKeyEvent', {
      type: 'keyUp',
      key: 'Enter',
      code: 'Enter',
      windowsVirtualKeyCode: 13,
    });
  }
  await state(true, 3);
  check(
    await c.evaluate(
      `document.activeElement===document.querySelector('${button}') && document.querySelector('${button}').getAttribute('aria-label')==='Снять отметку Нравится'`,
    ),
    'phone width keyboard activation preserves focus and selected accessible label',
  );
  if (firefox) await c.key('\uE00D');
  else {
    await c.evaluate(
      `document.querySelector('${button}').scrollIntoView({block:'center'})`,
    );
    const point = JSON.parse(
      await c.evaluate(
        `JSON.stringify((()=>{const r=document.querySelector('${button}').getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2};})())`,
      ),
    );
    await c.send('Emulation.setTouchEmulationEnabled', { enabled: true });
    await c.send('Input.dispatchTouchEvent', {
      type: 'touchStart',
      touchPoints: [point],
    });
    await c.send('Input.dispatchTouchEvent', {
      type: 'touchEnd',
      touchPoints: [],
    });
  }
  await state(false, 2);
  check(
    true,
    firefox
      ? 'Space can remove a like without refocusing'
      : 'phone touch emulation can remove a like',
  );
  check(
    writes.every(
      (x) =>
        Object.keys(x.body).length === 1 && typeof x.body.liked === 'boolean',
    ),
    'all commands contain one target boolean without viewer IDs or counters',
  );
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
  console.log(
    JSON.stringify({
      profile: 'video-likes',
      browser: firefox ? 'Firefox' : 'Chromium',
      assertions,
      status: 'pass',
    }),
  );
} catch (error) {
  console.error(
    JSON.stringify({
      profile: 'video-likes',
      assertions,
      writes,
      like: await c.evaluate(
        "document.querySelector('.video-like')?.textContent",
      ),
      disabled: await c.evaluate(
        `document.querySelector('${button}')?.disabled`,
      ),
      calls: calls.filter((x) => x.path.endsWith('/like')),
      queries: JSON.parse(
        await c.evaluate(
          `JSON.stringify(${queryClient}.getQueryCache().findAll({queryKey:['video-like']}).map(q=>({key:q.queryKey,data:q.state.data,fetchStatus:q.state.fetchStatus})))`,
        ),
      ),
      path: await c.evaluate('location.pathname+location.search'),
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
