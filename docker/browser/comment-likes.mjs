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
const v = {
  ...fixture.videos,
  public: firefox ? fixture.videos.silent : fixture.videos.public,
  other: firefox ? fixture.videos.public : fixture.videos.silent,
};
const host = 'http://youtube-frontend-upload-gateway:8080/api/v1';
let readFailure = false,
  writeFailure = false,
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
      list =
        /^\/api\/v1\/videos\/\d+\/comments(?:\?|$)/.test(req.url) &&
        req.method === 'GET',
      like = /^\/api\/v1\/videos\/\d+\/comments\/\d+\/like$/.test(req.url),
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
    if ((list && readFailure) || (write && writeFailure)) {
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
    if (list && holdRead) {
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
  queryClient =
    "Object.values(document.querySelector('#app').__vue_app__._context.provides).find(x=>typeof x?.getQueryCache==='function')",
  marker = 'F17-' + crypto.randomUUID().slice(0, 8),
  seeded = [];
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
  throw new Error('Comment like condition timed out');
}
const jars = new Map();
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
    data: raw
      ? JSON.parse(raw, (key, value, context) =>
          key === 'id' && typeof value === 'number' ? context.source : value,
        )
      : null,
  };
}
async function api(path, method = 'GET', body) {
  return JSON.parse(
    await c.evaluate(
      `(async()=>{const cookie=document.cookie.split('; ').find(x=>x.startsWith('csrftoken='));const r=await fetch('/api/v1'+${JSON.stringify(path)},{method:${JSON.stringify(method)},headers:{'content-type':'application/json',...(cookie?{'x-csrftoken':decodeURIComponent(cookie.slice(10))}:{})},body:${body === undefined ? 'undefined' : JSON.stringify(JSON.stringify(body))}});return JSON.stringify({status:r.status,data:r.status===204?null:await r.json()});})()`,
    ),
  );
}
function row(id) {
  return `.comment[data-comment-id="${id}"]`;
}
function button(id) {
  return row(id) + ' button[aria-pressed]';
}
function likePath(id, video = v.public) {
  return '/videos/' + video + '/comments/' + id + '/like';
}
async function state(id, liked, count) {
  await c.wait(
    `document.querySelector(${JSON.stringify(button(id))})?.getAttribute('aria-pressed')==='${liked}' && document.querySelector(${JSON.stringify(button(id))}).getAttribute('aria-disabled')!=='true' && document.querySelector(${JSON.stringify(row(id) + ' .comment__actions span')}).textContent.trim()==='${count}'`,
  );
}
async function settled() {
  await c.wait(
    `${queryClient}.getQueryCache().findAll({queryKey:['comments'],type:'active'}).some(q=>q.state.fetchStatus==='idle'&&q.state.status==='success')`,
  );
}
async function navigate(id = v.public) {
  await c.navigate(base + '/watch?v=' + id);
  await c.wait(
    `!!document.querySelector('.discussion__guest') || !!document.querySelector('#new-comment')`,
  );
  await settled();
}
async function spa(id) {
  await c.evaluate(
    `document.querySelector('#app').__vue_app__.config.globalProperties.$router.push('/watch?v=${id}')`,
  );
  await c.wait(
    `location.search==='?v=${id}' && (!!document.querySelector('#new-comment') || !!document.querySelector('.discussion__guest'))`,
  );
  await settled();
}
async function login(person = 'owner') {
  await c.navigate(
    base + '/login?returnTo=' + encodeURIComponent('/watch?v=' + v.public),
  );
  await submitLogin(person);
}
async function submitLogin(person = 'owner') {
  await c.fill('#login-email', fixture.people[person].email);
  await c.fill('#login-password', 'abcdef');
  await c.click('.auth-card button[type=submit]');
  await c.wait(
    `location.search==='?v=${v.public}' && !!document.querySelector('#new-comment') && !!document.querySelector('.avatar-button')`,
  );
  await settled();
}
async function logout() {
  await c.click('.avatar-button');
  await c.click('#logout-button');
  await c.wait(
    `!document.querySelector('.avatar-button') && !!document.querySelector('.discussion__guest')`,
  );
  await settled();
}
async function visibility(value) {
  const r = await actorRequest(
    'owner',
    '/videos/' + v.public + '/settings',
    'PUT',
    { title: 'Лайки комментариев', description: 'Описание', visibility: value },
  );
  assert.equal(r.status, 200);
}
async function refetch() {
  await c.evaluate(
    `void ${queryClient}.refetchQueries({queryKey:['comments'],type:'active'})`,
  );
}
async function choose(sort) {
  await c.click('.discussion__sort > button');
  await c.evaluate(
    `[...document.querySelectorAll('.discussion__menu button')].find(x=>x.textContent.includes('${sort === 'popular' ? 'популярные' : 'новые'}')).click()`,
  );
}
async function allComments(sort = 'newest', id = v.public) {
  const result = [];
  let cursor;
  do {
    const r = await actorRequest(
      'owner',
      '/videos/' +
        id +
        '/comments?sort=' +
        sort +
        (cursor ? '&cursor=' + encodeURIComponent(cursor) : ''),
    );
    assert.equal(r.status, 200);
    result.push(...r.data.items);
    cursor = r.data.next_cursor;
  } while (cursor);
  return result;
}
async function uiIds() {
  return JSON.parse(
    await c.evaluate(
      `JSON.stringify([...document.querySelectorAll('.comment')].map(x=>x.dataset.commentId))`,
    ),
  );
}
async function loadAll(expected) {
  while ((await uiIds()).length < expected) {
    await c.click('.discussion__more');
    await settled();
  }
}
async function key(value) {
  if (firefox) {
    await c.key({ Enter: '\uE007', Space: '\uE00D' }[value]);
    return;
  }
  const code = value === 'Enter' ? 13 : 32;
  await c.send('Input.dispatchKeyEvent', {
    type: 'keyDown',
    key: value === 'Space' ? ' ' : value,
    windowsVirtualKeyCode: code,
    text: value === 'Enter' ? '\r' : ' ',
  });
  await c.send('Input.dispatchKeyEvent', {
    type: 'keyUp',
    key: value === 'Space' ? ' ' : value,
    windowsVirtualKeyCode: code,
  });
}
function releaseOne() {
  const resolve = held.values().next().value;
  held.delete(resolve);
  resolve();
}
const listCalls = () =>
  calls.filter(
    (x) =>
      /^\/api\/v1\/videos\/\d+\/comments(?:\?|$)/.test(x.path) &&
      x.method === 'GET',
  ).length;
try {
  await visibility('public');
  for (let i = 0; i < 41; i++) {
    const person = i === 39 ? 'invited' : 'owner',
      r = await actorRequest(
        person,
        '/videos/' + v.public + '/comments',
        'POST',
        { text: marker + ' comment ' + i },
      );
    assert.equal(r.status, 201);
    seeded.push(r.data);
  }
  const a = seeded[40].id,
    b = seeded[39].id,
    old = seeded[0].id,
    other = seeded[3].id;
  await c.viewport(1280, 900);
  if (!firefox)
    await c.send('Emulation.setFocusEmulationEnabled', { enabled: true });
  await navigate();
  await state(a, false, 0);
  check(
    writes.length === 0,
    'guest sees unselected real counter without mutation',
  );
  await c.click(button(a));
  await c.wait(`location.pathname==='/login'`);
  check(
    await c.evaluate(
      `new URLSearchParams(location.search).get('returnTo')==='/watch?v=${v.public}'`,
    ),
    'guest like preserves current return URL',
  );
  await submitLogin();
  await state(a, false, 0);
  check(
    writes.length === 0,
    'sign-in never applies the guest like automatically',
  );
  await c.click(button(a));
  await state(a, true, 1);
  await settled();
  check(
    (await actorRequest('owner', likePath(a))).data.liked,
    'own comment can be liked and persists on the server',
  );
  await c.reload();
  await state(a, true, 1);
  await c.click(button(a));
  await state(a, false, 0);
  await settled();
  check(
    (await actorRequest('owner', likePath(a))).data.like_count === 0,
    'removal persists without negative counter',
  );
  await c.click(button(b));
  await state(b, true, 1);
  await settled();
  check(
    (await actorRequest('owner', likePath(b))).data.liked,
    'another author comment can be liked',
  );
  await c.click(button(b));
  await state(b, false, 0);
  await settled();

  holdWriteBefore = true;
  const before = writes.length;
  const readsBeforeClick = listCalls();
  await c.evaluate(
    `for(let i=0;i<4;i++)document.querySelector(${JSON.stringify(button(a))}).click();document.querySelector('.discussion__more').click()`,
  );
  await until(() => held.size === 1);
  await pause(150);
  check(
    listCalls() === readsBeforeClick,
    'same-frame continuation cannot start a list read during a like command',
  );
  check(
    writes.length === before + 1 &&
      (await c.evaluate(
        `document.querySelector(${JSON.stringify(button(a))}).getAttribute('aria-disabled')==='true' && document.querySelector(${JSON.stringify(button(a))}).getAttribute('aria-pressed')==='false'`,
      )),
    'rapid clicks produce one command and no optimistic selection',
  );
  check(
    writes.at(-1).path === '/api/v1' + likePath(a) &&
      JSON.stringify(writes.at(-1).body) === '{"liked":true}',
    'PUT fixes exact video/comment IDs and only a target bool',
  );
  holdWriteBefore = false;
  release();
  await state(a, true, 1);
  await settled();
  check(
    (await actorRequest('owner', likePath(a))).data.like_count === 1,
    'repeated click creates one persisted like',
  );

  writeFailure = true;
  await c.click(button(a));
  await c.wait(
    `!!document.querySelector(${JSON.stringify(row(a) + ' [role=alert]')})`,
  );
  await settled();
  await state(a, true, 1);
  check(
    !(await c.evaluate(
      `!!window.likeInjection || !!document.querySelector('.comment img')`,
    )),
    'failed PUT keeps selection without executing error detail',
  );
  writeFailure = false;
  check(
    (await actorRequest('owner', likePath(a))).data.liked,
    'failed removal did not change backend',
  );
  await c.click(button(a));
  await state(a, false, 0);
  await settled();
  check(
    !(await c.evaluate(
      `!!document.querySelector(${JSON.stringify(row(a) + ' [role=alert]')})`,
    )),
    'successful retry clears row error',
  );
  dropCsrf = true;
  await c.click(button(a));
  await until(() => writes.at(-1).status === 403);
  await c.wait(
    `document.querySelector(${JSON.stringify(row(a) + ' [role=alert]')})?.textContent.includes('Обновите страницу')`,
  );
  await settled();
  await state(a, false, 0);
  check(
    await c.evaluate(
      `!!document.querySelector('#new-comment') && !!document.querySelector('.player video')`,
    ),
    'CSRF denial leaves allowed discussion and playback',
  );
  dropCsrf = false;
  check(
    !(await actorRequest('owner', likePath(a))).data.liked,
    'CSRF denial cannot create a like',
  );

  holdRead = true;
  await refetch();
  await until(() => held.size === 1);
  await c.click(button(a));
  await state(a, true, 1);
  release();
  await settled();
  check(
    await c.evaluate(
      `document.querySelector(${JSON.stringify(button(a))}).getAttribute('aria-pressed')==='true'`,
    ),
    'late list snapshot cannot overwrite a confirmed like',
  );
  await c.click(button(a));
  await state(a, false, 0);
  await settled();

  holdWriteAfter = true;
  await c.click(button(a));
  await c.click(button(b));
  await until(
    () => held.size === 2 && writes.slice(-2).every((x) => x.status === 200),
  );
  const reads = listCalls();
  releaseOne();
  await c.wait(
    `[...document.querySelectorAll('.comment button[aria-pressed=true]')].length===1`,
  );
  check(
    listCalls() === reads,
    'list waits while another comment command is pending',
  );
  holdWriteAfter = false;
  release();
  await state(a, true, 1);
  await state(b, true, 1);
  await settled();
  check(
    (await actorRequest('owner', likePath(a))).data.liked &&
      (await actorRequest('owner', likePath(b))).data.liked,
    'parallel likes preserve both confirmed results',
  );
  await c.click(button(a));
  await state(a, false, 0);
  await settled();
  await c.click(button(b));
  await state(b, false, 0);
  await settled();

  readFailure = true;
  await c.click(button(a));
  await c.wait(
    `document.querySelector('.discussion__more')?.textContent.trim()==='Повторить' && !document.querySelector('.comment')`,
  );
  check(
    (await actorRequest('owner', likePath(a))).data.liked,
    'accepted like remains persisted even when its list refresh fails',
  );
  readFailure = false;
  await c.click('.discussion__more');
  await state(a, true, 1);
  await settled();
  check(true, 'list retry recovers actual selected state after refresh error');
  await actorRequest('invited', likePath(a), 'PUT', { liked: true });
  await c.reload();
  await state(a, true, 2);
  check(true, 'independent viewers contribute to one aggregate');
  await logout();
  await state(a, false, 2);
  check(true, 'guest sees aggregate without another viewer selection');
  await login('stranger');
  await state(a, false, 2);
  await api('/auth/logout', 'POST');
  await c.click(button(a));
  await c.wait(
    `!!document.querySelector('.discussion__guest') && !document.querySelector('.avatar-button')`,
  );
  await state(a, false, 2);
  check(
    writes.at(-1).status === 401 &&
      !(await actorRequest('stranger', likePath(a))).data.liked,
    'real session expiry cannot create a like or keep viewer selection',
  );

  await login();
  holdWriteAfter = true;
  await c.click(button(a));
  await until(() => held.size === 1 && writes.at(-1).status === 200);
  await spa(v.other);
  holdWriteAfter = false;
  release();
  await pause(200);
  check(
    !(await c.evaluate(
      `!!document.querySelector(${JSON.stringify(row(a))}) || !!document.querySelector('.comment [role=alert]')`,
    )),
    'late success cannot affect another video or row',
  );
  await navigate();
  await state(a, false, 1);
  check(
    !(await actorRequest('owner', likePath(a))).data.liked,
    'return reads removal committed before response cancellation',
  );
  holdWriteBefore = true;
  writeFailure = true;
  await c.click(button(a));
  await until(() => held.size === 1);
  await spa(v.other);
  holdWriteBefore = false;
  release();
  await until(() => writes.at(-1).status === 503);
  writeFailure = false;
  await pause(200);
  check(
    !(await c.evaluate(`!!document.querySelector('.comment [role=alert]')`)),
    'late failure cannot add a row error to another video',
  );
  await navigate();
  holdWriteAfter = true;
  await c.click(button(a));
  await until(() => held.size === 1 && writes.at(-1).status === 200);
  await logout();
  await login('invited');
  holdWriteAfter = false;
  release();
  await settled();
  await state(a, true, 2);
  check(
    true,
    'late owner response after account switch does not overwrite invited viewer state',
  );

  await login();
  await state(a, true, 2);
  await c.click(button(a));
  await state(a, false, 1);
  await settled();
  holdWriteAfter = true;
  await c.click(button(a));
  await until(() => held.size === 1 && writes.at(-1).status === 200);
  const beforeSort = listCalls();
  await choose('popular');
  await pause(150);
  check(
    listCalls() === beforeSort &&
      !(await c.evaluate(`!!document.querySelector('.comment')`)),
    'sort change waits for pending mutation before requesting its server order',
  );
  holdWriteAfter = false;
  release();
  await settled();
  check(
    (await uiIds())[0] === a,
    'confirmed like is reflected in newly chosen popular order',
  );
  await c.click(button(a));
  await state(a, false, 1);
  await settled();
  await actorRequest('owner', likePath(a), 'PUT', { liked: false });
  await actorRequest('invited', likePath(a), 'PUT', { liked: false });
  await actorRequest('invited', likePath(old), 'PUT', { liked: true });
  await actorRequest('invited', likePath(seeded[1].id), 'PUT', { liked: true });
  await c.reload();
  await choose('popular');
  await settled();
  check(
    JSON.stringify((await uiIds()).slice(0, 2)) ===
      JSON.stringify([seeded[1].id, old]),
    'popular equal counts prefer the newer comment',
  );
  const all = await allComments('popular');
  await loadAll(all.length);
  await c.click(button(other));
  await settled();
  const ordered = await allComments('popular');
  check(
    JSON.stringify(await uiIds()) ===
      JSON.stringify(ordered.map((x) => x.id)) &&
      ordered.findIndex((x) => x.id === other) < 20,
    'like from a later page uses the fresh global server order without duplicate IDs',
  );
  await c.click(button(other));
  await settled();

  await choose('newest');
  await settled();
  holdWriteBefore = true;
  await c.click(button(a));
  await until(() => held.size === 1);
  await visibility('private');
  holdWriteBefore = false;
  release();
  await until(() => writes.at(-1).status === 404);
  await c.wait(
    `!!document.querySelector('.watch-page__private-discussion') && !document.querySelector('.discussion') && !!document.querySelector('.player video')`,
  );
  check(
    await c.evaluate(
      `${queryClient}.getQueryCache().findAll({queryKey:['comments']}).every(q=>q.queryKey[2]!=='${v.public}')`,
    ),
    'access revocation removes every comment sort while retaining owner playback',
  );
  check(
    (await actorRequest('owner', likePath(a))).status === 404,
    'private comment cannot expose its former count',
  );
  await visibility('public');
  await navigate();
  await state(a, false, 0);
  check(
    !(await actorRequest('owner', likePath(a))).data.liked,
    'denied like is absent after public restoration',
  );

  await c.viewport(390, 844, true);
  await c.evaluate(
    `document.querySelector(${JSON.stringify(button(a))}).focus()`,
  );
  await key('Enter');
  await state(a, true, 1);
  await settled();
  check(
    await c.evaluate(
      `document.activeElement===document.querySelector(${JSON.stringify(button(a))}) && document.activeElement.getAttribute('aria-label')==='Снять лайк комментария'`,
    ),
    'phone keyboard like keeps focus and accessible selected label',
  );
  await key('Space');
  await state(a, false, 0);
  await settled();
  check(true, 'Space can remove the like without finding the button again');
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
        Object.keys(x.body).length === 1 && typeof x.body.liked === 'boolean',
    ),
    'every command carries one bool without author or counter fields',
  );
  console.log(
    JSON.stringify({
      profile: 'comment-likes',
      browser: firefox ? 'Firefox' : 'Chromium',
      assertions,
      status: 'pass',
    }),
  );
} catch (error) {
  console.error(
    JSON.stringify({
      profile: 'comment-likes',
      assertions,
      writes: writes.slice(-4),
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
