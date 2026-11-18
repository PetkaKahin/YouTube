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
const c = firefox ? await firefoxBrowser() : await browser(),
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
const trigger = '.save-video__trigger';
const dialog = 'dialog[aria-label="Выберите плейлист"]';
async function ready(id = v.public) {
  await c.wait(
    `document.querySelector('.player__start')?.disabled===false && document.querySelector('.watch-page__metadata h1')`,
  );
  assert.equal(
    await c.evaluate("new URLSearchParams(location.search).get('v')"),
    id,
  );
}
async function navigate(id = v.public) {
  await c.navigate(base + root + id);
  await ready(id);
}
async function openSave() {
  await c.click(trigger);
  await c.wait(
    `document.querySelector(${JSON.stringify(dialog)})?.open===true`,
  );
  await settled();
}
async function settled() {
  await c.wait(`!${client}.isFetching({queryKey:['saved-playlists']})`);
  await c.wait(
    `!document.querySelector(${JSON.stringify(dialog)})?.getAttribute('aria-busy')?.includes('true')`,
  );
}
async function closeSave() {
  await c.click(dialog + ' header button');
  await c.wait(
    `document.querySelector(${JSON.stringify(dialog)})?.open===false`,
  );
}
async function rowState(id, value) {
  await c.wait(
    `document.querySelector('[data-playlist-id="${id}"]')?.getAttribute('aria-pressed')==='${value}'`,
  );
  await settled();
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
async function all(person, path) {
  let rows = [],
    cursor;
  do {
    const r = await actorRequest(
      person,
      path + (cursor ? '?cursor=' + encodeURIComponent(cursor) : ''),
    );
    assert.equal(r.status, 200);
    rows.push(...r.data.items);
    cursor = r.data.next_cursor;
  } while (cursor);
  return rows;
}
function cardIds() {
  return c.evaluate(
    "[...document.querySelectorAll('.watch-page__feed .video-card__preview')].map(x=>new URL(x.href).searchParams.get('v')).join('|')",
  );
}
async function play() {
  await c.evaluate(
    "document.querySelector('.player video').muted=true;document.querySelector('.player__start').focus()",
  );
  await enter();
  await c.wait(
    "document.querySelector('.player video').currentTime>0 && !document.querySelector('.player video').paused",
  );
}
async function refreshVideo() {
  await c.evaluate(
    `void ${client}.refetchQueries({queryKey:['video'],type:'active'})`,
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
  const tag = 'save_' + crypto.randomUUID().replaceAll('-', '').slice(0, 12);
  const email = tag + '@example.com';
  const registered = await actorRequest('owner', '/auth/register', 'POST', {
    name: 'Зритель',
    email,
    tag,
    password: 'abcdef',
    password_confirmation: 'abcdef',
  });
  assert.equal(registered.status, 201);
  fixture.people.watcher = { id: registered.data.account_id, email, tag };
  await c.viewport(1440, 900);
  if (!firefox)
    await c.send('Emulation.setFocusEmulationEnabled', { enabled: true });
  await navigate();
  check(
    !calls.some((x) => x.path.includes('saved-playlists')),
    'guest makes no personal save read',
  );
  await c.wait(
    "document.querySelectorAll('.watch-page__feed .video-card').length===20",
  );
  const first = (await all('owner', '/videos/feed'))
    .slice(0, 20)
    .map((x) => x.id)
    .join('|');
  check(
    (await cardIds()) === first,
    'right list is exactly home feed first server page',
  );
  for (let i = 0; i < 2; i++) {
    await c.click('.watch-page__feed .pagination button');
    await c.wait(`!${client}.isFetching({queryKey:['watch-feed']})`);
  }
  check(
    (await cardIds()) ===
      (await all('owner', '/videos/feed')).map((x) => x.id).join('|'),
    'right feed continues 20/20/1 with exact IDs',
  );
  await c.click(trigger);
  await c.wait("location.pathname==='/login'");
  check(
    await c.evaluate(
      `new URLSearchParams(location.search).get('returnTo')===${JSON.stringify(root + v.public)}`,
    ),
    'bookmark guest action preserves return URL',
  );
  await c.fill('#login-email', fixture.people.owner.email);
  await c.fill('#login-password', 'abcdef');
  await c.click('.auth-card button[type=submit]');
  await ready();
  check(
    !calls.some((x) => x.method === 'PUT' && x.path.includes('/playlists/')),
    'login does not auto-save',
  );
  // Reset only this browser fixture's chosen pairs.
  const ownerLists = await all(
    'owner',
    '/videos/' + v.public + '/saved-playlists',
  );
  for (const row of ownerLists)
    if (row.selected)
      await actorRequest(
        'owner',
        '/playlists/' + row.id + '/videos/' + v.public,
        'PUT',
        { selected: false },
      );
  await openSave();
  check(
    await c.evaluate(
      `document.querySelectorAll('${dialog} .save-video__item').length===20`,
    ),
    'first save page has twenty own playlists',
  );
  check(
    await c.evaluate(
      "document.querySelector('.save-video__trigger').getAttribute('aria-pressed')==='false'",
    ),
    'global saved flag starts false',
  );
  let chosen = await c.evaluate(
    `document.querySelector('${dialog} .save-video__item').dataset.playlistId`,
  );
  const writesBefore = calls.filter(
    (x) => x.method === 'PUT' && x.path.includes('/playlists/'),
  ).length;
  holdBeforePath = '/api/v1/playlists/' + chosen + '/videos/' + v.public;
  await c.evaluate(
    `document.querySelector('[data-playlist-id="${chosen}"]').focus()`,
  );
  await enter();
  await until(() => held.size > 0);
  await c.evaluate(
    `for(let i=0;i<4;i++)document.querySelector('[data-playlist-id="${chosen}"]').click()`,
  );
  check(
    calls.filter((x) => x.method === 'PUT' && x.path.includes('/playlists/'))
      .length ===
      writesBefore + 1 &&
      (await c.evaluate(
        `document.querySelector('[data-playlist-id="${chosen}"]').getAttribute('aria-pressed')==='false'`,
      )),
    'pending selection sends one command without optimistic success',
  );
  release();
  await rowState(chosen, true);
  check(
    await c.evaluate(
      `document.querySelector(${JSON.stringify(dialog)}).open && document.querySelector('.save-video__trigger').getAttribute('aria-pressed')==='true'`,
    ),
    'keyboard save applies immediately and retains dialog',
  );
  check(
    (await actorRequest('owner', '/videos/' + v.public + '/saved-playlists'))
      .data.saved,
    'server confirms bookmark',
  );
  for (let i = 0; i < 2; i++) {
    await c.click(dialog + ' .save-video__more');
    await settled();
  }
  check(
    await c.evaluate(
      `document.querySelectorAll('${dialog} .save-video__item').length===45`,
    ),
    'selection paginates all forty-five playlists',
  );
  const last = await c.evaluate(
    `document.querySelector('${dialog} .save-video__item:last-child').dataset.playlistId`,
  );
  await c.click(`[data-playlist-id="${last}"]`);
  await rowState(last, true);
  await c.click(`[data-playlist-id="${chosen}"]`);
  await rowState(chosen, false);
  check(
    await c.evaluate(
      "document.querySelector('.save-video__trigger').getAttribute('aria-pressed')==='true'",
    ),
    'removing one pair preserves global selection beyond first page',
  );
  await closeSave();
  check(
    await c.evaluate(
      "document.activeElement.classList.contains('save-video__trigger')",
    ),
    'dialog closes back to bookmark focus',
  );
  await c.reload();
  await ready();
  await c.wait(
    "document.querySelector('.save-video__trigger').getAttribute('aria-pressed')==='true'",
  );
  check(true, 'reload reads global saved outside first page');
  await openSave();
  failurePath = '/api/v1/playlists/' + ownerLists[0].id + '/videos/' + v.public;
  await c.click(`[data-playlist-id="${ownerLists[0].id}"]`);
  await c.wait(`!!document.querySelector('${dialog} [role=alert]')`);
  check(
    await c.evaluate(
      `document.querySelector('[data-playlist-id="${ownerLists[0].id}"]').getAttribute('aria-pressed')==='false'`,
    ),
    'failed write retains confirmed selection',
  );
  failurePath = '';
  await settled();
  await c.click(`[data-playlist-id="${ownerLists[0].id}"]`);
  await rowState(ownerLists[0].id, true);
  check(true, 'explicit write retry confirms real success');
  await closeSave();
  failurePath = '/api/v1/videos/' + v.public + '/saved-playlists';
  await openSave();
  await c.wait(
    `document.querySelector('${dialog} [role=alert]')?.textContent.includes('Не удалось загрузить')`,
  );
  check(
    await c.evaluate(
      `document.querySelectorAll('${dialog} .save-video__item').length===0`,
    ),
    'failed read hides old personal choices',
  );
  failurePath = '';
  await c.click(dialog + ' .text-link');
  await settled();
  check(
    await c.evaluate(
      `document.querySelectorAll('${dialog} .save-video__item').length>0`,
    ),
    'read retry restores choices',
  );
  await closeSave();
  await openSave();
  const csrfId = ownerLists[0].id;
  stripCsrf = true;
  await c.click(`[data-playlist-id="${csrfId}"]`);
  await c.wait(
    `document.querySelector('${dialog} .form-error')?.textContent.includes('Обновите')`,
  );
  stripCsrf = false;
  await settled();
  check(
    (
      await actorRequest('owner', '/videos/' + v.public + '/saved-playlists')
    ).data.items.find((p) => p.id === csrfId).selected,
    'real CSRF denial preserves selected pair',
  );
  await closeSave();
  // Any viewer can save the other author's available video.
  await fillLogin('watcher', root + v.public);
  await ready();
  await openSave();
  check(
    await c.evaluate(
      `document.querySelector(${JSON.stringify(dialog)}).textContent.includes('У вас пока нет плейлистов')`,
    ),
    'other viewer sees their empty playlists',
  );
  await c.click(dialog + ' .create-playlist button');
  await c.wait("document.querySelector('.playlist-dialog')?.open===true");
  const title = 'Сохранено ' + crypto.randomUUID().slice(0, 8);
  await c.fill('#playlist-title', title);
  await c.fill(
    '#playlist-description',
    'Описание <img src=x onerror=window.saveInject=1>',
  );
  await c.evaluate(
    "const select=document.querySelector('#playlist-visibility');select.value='private';select.dispatchEvent(new Event('change',{bubbles:true}))",
  );
  failurePath = '/api/v1/playlists';
  await c.click('.playlist-dialog button[type=submit]');
  await c.wait("!!document.querySelector('.playlist-dialog .form-error')");
  check(
    !(await all('watcher', '/playlists/mine')).some((p) => p.title === title),
    'failed creation does not leave an empty playlist or fake success',
  );
  failurePath = '';
  await c.click('.playlist-dialog button[type=submit]');
  await c.wait("document.querySelector('.playlist-dialog')?.open===false");
  await settled();
  const mine = await all('watcher', '/videos/' + v.public + '/saved-playlists');
  const created = mine.find((x) => x.title === title);
  assert.ok(created);
  check(
    created.selected &&
      (
        await actorRequest('watcher', '/playlists/' + created.id)
      ).data.items.some((x) => x.id === v.public),
    'atomic creation contains other author video immediately',
  );
  check(
    await c.evaluate(
      `document.querySelector(${JSON.stringify(dialog)}).open && document.querySelector('.save-video__trigger').getAttribute('aria-pressed')==='true' && !window.saveInject`,
    ),
    'nested creation returns to selected chooser without HTML injection',
  );
  // Late choices never paint the next video or viewer.
  await closeSave();
  holdAfterPath = '/api/v1/videos/' + v.public + '/saved-playlists';
  await c.evaluate(
    `void ${client}.refetchQueries({queryKey:['saved-playlists'],type:'active'})`,
  );
  await until(() => held.size > 0);
  await spa(root + v.silent);
  await ready(v.silent);
  release();
  await settled();
  check(
    await c.evaluate(
      "document.querySelector('.save-video__trigger').getAttribute('aria-pressed')==='false'",
    ),
    'late saved state does not transfer to another video',
  );
  holdAfterPath = '/api/v1/videos/' + v.silent + '/saved-playlists';
  await c.evaluate(
    `void ${client}.refetchQueries({queryKey:['saved-playlists'],type:'active'})`,
  );
  await until(() => held.size > 0);
  await fillLogin('owner', root + v.silent);
  await ready(v.silent);
  release();
  await settled();
  await openSave();
  check(
    !(await c.evaluate(
      `document.querySelector(${JSON.stringify(dialog)}).textContent.includes(${JSON.stringify(title)})`,
    )),
    'late personal response cannot expose another viewer playlist',
  );
  await closeSave();
  await fillLogin('watcher', root + v.public);
  await ready();
  // Dynamic ambient uses the existing real HLS video.
  await navigate();
  await play();
  await c.wait(
    "document.querySelector('.watch-page').classList.contains('watch-page--ambient')",
  );
  await c.wait(
    "document.querySelector('.watch-page').style.getPropertyValue('--watch-ambient').trim()!=='0 0 0'",
  );
  const rgb = await c.evaluate(
    "document.querySelector('.watch-page').style.getPropertyValue('--watch-ambient').trim()",
  );
  check(
    /^\d+ \d+ \d+$/.test(rgb) && rgb !== '0 0 0',
    'ambient comes from decoded video pixels',
  );
  await c.evaluate("document.querySelector('.player video').pause()");
  await c.wait("document.querySelector('.player video').paused");
  check(
    await c.evaluate(
      "document.querySelectorAll('.player video').length===1 && document.querySelectorAll('.watch-page canvas').length===0",
    ),
    'ambient creates no extra video or retained DOM canvas',
  );
  const beforeMedia = calls.filter((x) => x.path.includes('/hls/')).length;
  await pause(1100);
  check(
    calls.filter((x) => x.path.includes('/hls/')).length === beforeMedia,
    'paused ambient does not fetch another stream',
  );
  await c.fill('.player__seek', '4');
  await c.wait("document.querySelector('.player video').currentTime>3.5");
  check(
    await c.evaluate(
      "document.querySelector('.watch-page').classList.contains('watch-page--ambient')",
    ),
    'seeking keeps decoded-frame lighting',
  );
  await c.evaluate(
    "Object.defineProperty(document,'hidden',{configurable:true,get:()=>true});document.dispatchEvent(new Event('visibilitychange'))",
  );
  await c.wait("!document.querySelector('.watch-page--ambient')");
  check(true, 'hidden page clears ambient sampling');
  await c.evaluate(
    "delete document.hidden;document.dispatchEvent(new Event('visibilitychange'))",
  );
  await c.wait("!!document.querySelector('.watch-page--ambient')");
  check(true, 'visible page resamples paused decoded frame');
  if (!firefox) {
    await c.send('Emulation.setEmulatedMedia', {
      features: [{ name: 'prefers-reduced-motion', value: 'reduce' }],
    });
    await c.wait("!document.querySelector('.watch-page--ambient')");
    check(true, 'reduced-motion disables dynamic lighting');
    await c.send('Emulation.setEmulatedMedia', {
      features: [{ name: 'prefers-reduced-motion', value: 'no-preference' }],
    });
    await c.wait("!!document.querySelector('.watch-page--ambient')");
  }
  await c.evaluate(
    "window.originalCanvasRead=CanvasRenderingContext2D.prototype.getImageData;CanvasRenderingContext2D.prototype.getImageData=function(){throw new DOMException('Canvas denied','SecurityError')};document.querySelector('.player video').dispatchEvent(new Event('seeked'))",
  );
  await c.wait("!document.querySelector('.watch-page--ambient')");
  check(
    await c.evaluate(
      "!!document.querySelector('.player video') && !document.querySelector('.player__error')",
    ),
    'canvas read failure disables lighting without breaking playback',
  );
  await c.evaluate(
    'CanvasRenderingContext2D.prototype.getImageData=window.originalCanvasRead',
  );
  // Feed failure is independent from playable primary area.
  failurePath = '/api/v1/videos/feed';
  await c.evaluate(
    `void ${client}.refetchQueries({queryKey:['watch-feed'],type:'active'})`,
  );
  await c.wait(
    "document.querySelector('.watch-page__feed .page-state')?.textContent.includes('Не удалось')",
  );
  check(
    await c.evaluate(
      "!!document.querySelector('.player video') && !document.querySelector('.watch-page__feed .video-card')",
    ),
    'feed error removes its data without destroying primary video',
  );
  failurePath = '';
  await c.click('.watch-page__feed .page-state button');
  await c.wait(
    "document.querySelectorAll('.watch-page__feed .video-card').length>0",
  );
  check(true, 'feed retry restores actual cards');
  // Real private revocation while save list and player are open.
  await actorRequest('owner', '/videos/' + v.private + '/permissions', 'PUT', {
    email: fixture.people.watcher.email,
    allowed: true,
  });
  await navigate(v.private);
  await openSave();
  await c.click(`[data-playlist-id="${created.id}"]`);
  await rowState(created.id, true);
  check(
    (await actorRequest('watcher', '/playlists/' + created.id)).data.items.some(
      (x) => x.id === v.private,
    ),
    'permitted private can be saved without granting further access',
  );
  await actorRequest('owner', '/videos/' + v.private + '/permissions', 'PUT', {
    email: fixture.people.watcher.email,
    allowed: false,
  });
  await c.evaluate(
    `void ${client}.refetchQueries({queryKey:['saved-playlists'],type:'active'})`,
  );
  await c.wait(
    "document.querySelector('.watch-page .page-state h1')?.textContent==='Видео недоступно'",
  );
  check(
    await c.evaluate(
      `!document.querySelector('.player video') && !document.querySelector('.watch-page__feed') && !document.querySelector('.watch-page--ambient') && !document.querySelector(${JSON.stringify(dialog)})`,
    ),
    'actual revocation clears player lighting list and save dialog',
  );
  await c.viewport(390, 844, true);
  await navigate();
  await openSave();
  check(
    await c.evaluate(
      `document.querySelector(${JSON.stringify(dialog)}).getBoundingClientRect().width<=innerWidth`,
    ),
    'save choices are usable at phone width',
  );
  await c.evaluate(
    `document.querySelector(${JSON.stringify(dialog)}).dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))`,
  );
  await c.wait(
    `document.querySelector(${JSON.stringify(dialog)}).open===false`,
  );
  check(
    await c.evaluate(
      "document.activeElement.classList.contains('save-video__trigger')",
    ),
    'phone Escape restores save focus',
  );
  await c.viewport(320, 700, true);
  check(
    await c.evaluate('document.documentElement.scrollWidth<=innerWidth'),
    'watch with side list fits narrow phone',
  );
  await openSave();
  await control('lost', '__session_fixture');
  jars.clear();
  await c.evaluate(
    `void ${client}.refetchQueries({queryKey:['saved-playlists'],type:'active'})`,
  );
  await c.wait(
    `!${store}.account && !${store}.changing && !document.querySelector('.save-video__item')`,
  );
  check(
    await c.evaluate(
      `!document.querySelector(${JSON.stringify(dialog)})?.open && !document.querySelector('.watch-page--ambient')`,
    ),
    'real 401 clears personal choices and old ambient scope',
  );
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
      profile: 'watch-enhancements',
      browser: firefox ? 'Firefox' : 'Chromium',
      assertions,
      status: 'pass',
    }),
  );
} catch (error) {
  console.error(
    JSON.stringify({
      profile: 'watch-enhancements',
      assertions,
      path: await c.evaluate('location.pathname'),
      errors: safeErrors(),
    }),
  );
  throw error;
} finally {
  failurePath = '';
  holdAfterPath = '';
  release();
  await c.close();
  for (const socket of sockets) socket.destroy();
  server.closeAllConnections();
  await new Promise((resolve) => server.close(resolve));
}
