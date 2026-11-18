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
const viewer = 'library';
const runToken = crypto.randomUUID().replaceAll('-', '').slice(0, 12);
const root = '/feed/';
const paths = ['subscriptions', 'history', 'liked', 'playlists'];
const listingPaths = [
  '/api/v1/videos/subscriptions',
  '/api/v1/videos/history',
  '/api/v1/videos/liked',
  '/api/v1/playlists/mine',
  '/api/v1/channels/subscriptions',
];
const cards = async () =>
  JSON.parse(
    await c.evaluate(
      "JSON.stringify([...document.querySelectorAll('.video-card__preview')].map(x=>new URL(x.href).searchParams.get('v')))",
    ),
  );
async function all(person, path) {
  let cursor,
    rows = [];
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
async function loaded(kind) {
  await c.wait(
    `!!document.querySelector('.library-page[aria-label=${JSON.stringify(kind === 'subscriptions' ? 'Подписки' : kind === 'liked' ? 'Понравившиеся' : kind === 'history' ? 'История просмотра' : 'Плейлисты')} ]')`,
  );
  await c.wait(
    `!${client}.isFetching({queryKey:[${JSON.stringify(kind === 'playlists' ? 'library-playlists' : 'library-videos')}]})`,
  );
}
async function loadAll(kind, target) {
  const selector = kind === 'playlists' ? '.playlist-card' : '.video-card';
  await loaded(kind);
  for (
    let i = 0;
    i < 5 &&
    (await c.evaluate(`document.querySelectorAll('${selector}').length`)) <
      target;
    i++
  ) {
    await c.click(
      kind === 'playlists'
        ? '.library-page__pagination button'
        : '.pagination button',
    );
    await loaded(kind);
  }
  assert.equal(
    await c.evaluate(`document.querySelectorAll('${selector}').length`),
    target,
  );
}
async function refreshLibrary() {
  await c.evaluate(
    `void ${client}.refetchQueries({predicate:q=>['library-videos','library-playlists'].includes(q.queryKey[0]),type:'active'})`,
  );
  await until(
    async () =>
      !(await c.evaluate(
        `${client}.isFetching({predicate:q=>['library-videos','library-playlists'].includes(q.queryKey[0])})`,
      )),
  );
}
async function viewerWrite(path, body) {
  const r = await actorRequest(viewer, path, 'PUT', body);
  assert.equal(r.status, 200);
  return r.data;
}
let videoRows = [];
async function register(body) {
  const guest = await fetch(host + '/auth/me');
  const cookies = guest.headers.getSetCookie().map((x) => x.split(';')[0]);
  const csrf = cookies.find((x) => x.startsWith('csrftoken=')).slice(10);
  const response = await fetch(host + '/auth/register', {
    method: 'POST',
    headers: {
      cookie: cookies.join('; '),
      'x-csrftoken': decodeURIComponent(csrf),
      'content-type': 'application/json',
    },
    body: JSON.stringify({
      ...body,
      password: 'abcdef',
      password_confirmation: 'abcdef',
    }),
  });
  assert.equal(response.status, 201);
  return JSON.parse(await response.text(), (key, value, context) =>
    key.endsWith('_id') && typeof value === 'number' ? context.source : value,
  );
}
try {
  const tag = 'viewer_' + runToken;
  const email = tag + '@example.com';
  await until(async () => {
    try {
      return (await fetch(host + '/health/ready')).ok;
    } catch {
      return false;
    }
  });
  const registered = await register({ name: 'Зритель библиотеки', email, tag });
  fixture.people.library = { id: registered.account_id, email, tag };
  await c.viewport(1440, 900);
  if (!firefox)
    await c.send('Emulation.setFocusEmulationEnabled', { enabled: true });
  // Guests never read the personal APIs, even on direct routes.
  for (const kind of paths) {
    const before = calls.length;
    await c.navigate(base + root + kind);
    await c.wait("!!document.querySelector('.library-page__login')");
    check(
      !calls
        .slice(before)
        .some((x) => listingPaths.includes(x.path.split('?')[0])),
      'guest makes no personal list request: ' + kind,
    );
  }
  await c.click('.library-page__login');
  check(
    await c.evaluate(
      "new URLSearchParams(location.search).get('returnTo')==='/feed/playlists'",
    ),
    'guest login keeps exact destination',
  );
  await c.fill('#login-email', fixture.people[viewer].email);
  await c.fill('#login-password', 'abcdef');
  await c.click('.auth-card button[type=submit]');
  await c.wait(
    "document.querySelector('.library-page .page-state h1')?.textContent==='У вас пока нет плейлистов'",
  );
  check(
    true,
    'real login returns to empty own playlist page without creating anything',
  );
  for (const [kind, title] of [
    ['subscriptions', 'В подписках пока нет видео'],
    ['history', 'История просмотра пуста'],
    ['liked', 'Нет понравившихся видео'],
  ]) {
    await spa(root + kind);
    await c.wait(
      `document.querySelector('.library-page .page-state h1')?.textContent===${JSON.stringify(title)}`,
    );
    check(true, 'real empty list: ' + kind);
  }
  // Populate this browser's viewer using real session/CSRF APIs in the isolated DB.
  await viewerWrite('/channels/' + fixture.people.owner.tag + '/subscription', {
    subscribed: true,
  });
  videoRows = (await all(viewer, '/videos/feed')).slice(0, 21);
  assert.equal(videoRows.length, 21);
  for (const row of videoRows) {
    await viewerWrite('/videos/' + row.id + '/like', { liked: true });
    await viewerWrite('/videos/' + row.id + '/progress', {
      position_seconds: 1,
    });
  }
  // More than one channel page; no fake subscription response or cursor.
  for (let i = 0; i < 21; i++) {
    const tag = 'lib_' + runToken + '_' + i;
    let guest = await fetch(host + '/auth/me');
    const cookies = guest.headers.getSetCookie().map((x) => x.split(';')[0]);
    const csrf = cookies.find((x) => x.startsWith('csrftoken=')).slice(10);
    const body = {
      name: 'Канал ' + String.fromCharCode(65 + i),
      email: tag + '@example.com',
      tag,
      password: 'abcdef',
      password_confirmation: 'abcdef',
    };
    const r = await fetch(host + '/auth/register', {
      method: 'POST',
      headers: {
        cookie: cookies.join('; '),
        'x-csrftoken': decodeURIComponent(csrf),
        'content-type': 'application/json',
      },
      body: JSON.stringify(body),
    });
    assert.equal(r.status, 201);
    await viewerWrite('/channels/' + tag + '/subscription', {
      subscribed: true,
    });
  }
  await c.navigate(base + root + 'subscriptions');
  await c.wait(
    "document.querySelectorAll('.subscribed-channels__channel').length===7",
  );
  check(true, 'sidebar initially shows seven real subscribed channels');
  await c.evaluate(
    "[...document.querySelectorAll('.subscribed-channels button')].find(x=>x.textContent.trim()==='Развернуть').click()",
  );
  await c.wait(
    "document.querySelectorAll('.subscribed-channels__channel').length===20",
  );
  check(true, 'expand shows the full first server channel page');
  await c.evaluate(
    "[...document.querySelectorAll('.subscribed-channels button')].find(x=>x.textContent.trim()==='Ещё каналы').click()",
  );
  await c.wait(
    "document.querySelectorAll('.subscribed-channels__channel').length===22",
  );
  check(
    (await all(viewer, '/channels/subscriptions'))
      .map((x) => x.tag)
      .join('|') ===
      (await c.evaluate(
        "[...document.querySelectorAll('.subscribed-channels__channel')].map(x=>decodeURIComponent(x.pathname.slice(2))).join('|')",
      )),
    'channel cursor pages preserve server order',
  );
  check(
    !(await c.evaluate(
      "!!window.libraryInjection || !!document.querySelector('.subscribed-channels img')",
    )),
    'channel names use text and initials without external image requests',
  );
  await loadAll('subscriptions', 41);
  check(
    (await cards()).join('|') ===
      (await all(viewer, '/videos/subscriptions')).map((x) => x.id).join('|'),
    'subscription videos use exact ordered IDs across 20/20/1',
  );
  check(
    await c.evaluate(
      "!!document.querySelector('.library-page .video-grid') && !document.querySelector('.library-page .video-list')",
    ),
    'subscriptions use the shared home grid',
  );
  check(
    (await c.evaluate(
      "document.querySelector('#main-navigation a[aria-current=page]')?.getAttribute('href')",
    )) === '/feed/subscriptions',
    'sidebar marks current feed',
  );
  await c.click('.navigation__channels-toggle');
  check(
    !(await c.evaluate("!!document.querySelector('.subscribed-channels')")),
    'channel disclosure is independent of subscription navigation',
  );
  await c.click('.navigation__channels-toggle');
  await c.wait(
    "document.querySelectorAll('.subscribed-channels__channel').length===7",
  );
  await c.click('.subscribed-channels__channel');
  await c.wait("!!document.querySelector('.channel-page')");
  check(
    await c.evaluate("location.pathname.startsWith('/@lib_')"),
    'subscribed channel link opens actual public channel',
  );
  for (const kind of ['liked', 'history']) {
    await spa(root + kind);
    await loadAll(kind, 21);
    check(
      (await cards()).join('|') ===
        (await all(viewer, '/videos/' + kind)).map((x) => x.id).join('|'),
      'personal ' + kind + ' follows actual server chronology and cursor',
    );
    check(
      await c.evaluate(
        "!!document.querySelector('.video-list .video-card--horizontal') && !!document.querySelector('.video-card__watched')",
      ),
      'horizontal list preserves saved progress: ' + kind,
    );
  }
  // A failed first load and failed continuation never masquerade as empty success.
  failurePath = '/api/v1/videos/liked';
  await spa(root + 'history');
  await spa(root + 'liked');
  await c.wait(
    "document.querySelector('.library-page .page-state h1')?.textContent==='Не удалось загрузить видео'",
  );
  check((await cards()).length === 0, 'read failure hides prior private list');
  failurePath = '';
  await c.click('.library-page .page-state button');
  await loaded('liked');
  check((await cards()).length === 20, 'retry loads real first page');
  failurePath = '/api/v1/videos/liked';
  await c.click('.pagination button');
  await c.wait(
    "document.querySelector('.pagination button')?.textContent.includes('Повторить')",
  );
  check(
    (await cards()).length === 20,
    'continuation failure retains confirmed page without false empty',
  );
  failurePath = '';
  await c.click('.pagination button');
  await loaded('liked');
  check(
    (await cards()).length === 21,
    'continuation retry appends once without duplicates',
  );
  // Liking through the UI must refresh this viewer's collection on return.
  const target = videoRows[0].id;
  await spa('/watch?v=' + target);
  await c.wait(
    "document.querySelector('.video-like button')?.getAttribute('aria-pressed')==='true'",
  );
  await c.click('.video-like button');
  await c.wait(
    "document.querySelector('.video-like button')?.getAttribute('aria-pressed')==='false'",
  );
  await spa(root + 'liked');
  await loaded('liked');
  check(
    !(await cards()).includes(target),
    'confirmed unlike is absent on return to personal list',
  );
  await viewerWrite('/videos/' + target + '/progress', { position_seconds: 2 });
  await spa(root + 'history');
  await loaded('history');
  check(
    (await cards())[0] === target,
    'new accepted progress raises one history record',
  );
  await spa('/@' + fixture.people.owner.tag);
  await c.wait(
    "document.querySelector('.subscribe button')?.textContent.includes('Вы подписаны')",
  );
  await c.click('.subscribe button');
  await c.wait(
    "document.querySelector('.subscribe button')?.textContent.includes('Подписаться')",
  );
  await c.wait(
    `![...document.querySelectorAll('.subscribed-channels__channel')].some(x=>x.pathname===${JSON.stringify('/@' + fixture.people.owner.tag)})`,
  );
  await spa(root + 'subscriptions');
  await c.wait(
    "document.querySelector('.library-page .page-state h1')?.textContent==='В подписках пока нет видео'",
  );
  check(true, 'confirmed unsubscribe refreshes sidebar and subscription feed');
  await viewerWrite('/channels/' + fixture.people.owner.tag + '/subscription', {
    subscribed: true,
  });
  // Commands initiated before navigation must reconcile the newly mounted collection.
  await viewerWrite('/videos/' + target + '/like', { liked: true });
  await spa('/watch?v=' + target);
  await c.wait(
    "document.querySelector('.video-like button')?.getAttribute('aria-pressed')==='true'",
  );
  holdBeforePath = '/api/v1/videos/' + target + '/like';
  await c.click('.video-like button');
  await until(() => held.size > 0);
  await spa(root + 'liked');
  await loaded('liked');
  check(
    (await cards()).includes(target),
    'new list confirms the still uncommitted like before delayed command',
  );
  release();
  await until(async () => !(await cards()).includes(target));
  await loaded('liked');
  check(
    (await actorRequest(viewer, '/videos/' + target + '/like')).data.liked ===
      false,
    'late confirmed unlike updates personal list after initiating page unmount',
  );
  await spa('/@' + fixture.people.owner.tag);
  await c.wait(
    "document.querySelector('.subscribe button')?.textContent.includes('Вы подписаны')",
  );
  holdBeforePath =
    '/api/v1/channels/' + fixture.people.owner.tag + '/subscription';
  await c.click('.subscribe button');
  await until(() => held.size > 0);
  await spa(root + 'subscriptions');
  await loaded('subscriptions');
  check(
    (await cards()).length > 0,
    'subscription feed can read before an uncommitted unsubscribe',
  );
  release();
  await c.wait(
    "document.querySelector('.library-page .page-state h1')?.textContent==='В подписках пока нет видео'",
  );
  check(
    (
      await actorRequest(
        viewer,
        '/channels/' + fixture.people.owner.tag + '/subscription',
      )
    ).data.subscribed === false,
    'late confirmed unsubscribe refreshes feed after initiating page unmount',
  );
  // Own playlists use the existing endpoint, with exact IDs and ordinary-view links.
  await fillLogin('owner', root + 'playlists');
  await loadAll('playlists', 45);
  check(
    await c.evaluate(
      "[...document.querySelectorAll('.playlist-card h2')].some(x=>x.textContent.includes('<img')) && !window.frontendInject",
    ),
    'playlist titles render safely',
  );
  const playlistHrefs = JSON.parse(
    await c.evaluate(
      "JSON.stringify([...document.querySelectorAll('.playlist-card__preview')].map(x=>x.getAttribute('href')))",
    ),
  );
  check(
    playlistHrefs.some((x) => x.includes(fixture.playlists.private)),
    'own private playlists are included',
  );
  await c.evaluate(
    `document.querySelector('a.playlist-card__preview[href="/playlist?list=${fixture.playlists.private}"]').click()`,
  );
  await c.wait("!!document.querySelector('.playlist-contents')");
  check(
    await c.evaluate(
      `location.pathname.includes('/playlists') && new URLSearchParams(location.search).get('list')===${JSON.stringify(fixture.playlists.private)}`,
    ),
    'private playlist opens ordinary existing contents, without Studio',
  );
  // Old personal response remains late after real new login; never paints for new viewer.
  await spa(root + 'playlists');
  await loaded('playlists');
  const oldScope = await c.evaluate(`${store}.scope`);
  holdAfterPath = '/api/v1/playlists/mine';
  await c.evaluate(
    `void ${client}.refetchQueries({queryKey:['library-playlists'],type:'active'})`,
  );
  await until(() => held.size > 0);
  await fillLogin(viewer, root + 'playlists');
  await c.wait(
    "document.querySelector('.library-page .page-state h1')?.textContent==='У вас пока нет плейлистов'",
  );
  release();
  await c.wait(`!${client}.isFetching({queryKey:['library-playlists']})`);
  check(
    await c.evaluate(
      `!document.querySelector('.playlist-card') && ${client}.getQueryCache().getAll().filter(q=>q.queryKey.includes(${JSON.stringify(oldScope)})).every(q=>q.state.data===undefined)`,
    ),
    'late owner playlist response cannot restore former data/cache',
  );
  // Back to an earlier personal kind creates fresh observers, no disabled old snapshot.
  await spa(root + 'liked');
  await loaded('liked');
  await spa(root + 'history');
  await loaded('history');
  holdAfterPath = '/api/v1/videos/liked';
  await spa(root + 'liked');
  await until(() => held.size > 0);
  check(
    (await cards()).length === 0,
    'switching personal kinds waits for fresh data rather than cached snapshot',
  );
  release();
  await loaded('liked');
  // Revoking private access removes both history and liked metadata on re-read.
  await actorRequest('owner', '/videos/' + v.private + '/permissions', 'PUT', {
    email: fixture.people[viewer].email,
    allowed: true,
  });
  await viewerWrite('/videos/' + v.private + '/like', { liked: true });
  await viewerWrite('/videos/' + v.private + '/progress', {
    position_seconds: 1,
  });
  await spa(root + 'history');
  await loaded('history');
  check(
    (await cards())[0] === v.private,
    'permitted private video appears in account history',
  );
  await actorRequest('owner', '/videos/' + v.private + '/permissions', 'PUT', {
    email: fixture.people[viewer].email,
    allowed: false,
  });
  await refreshLibrary();
  check(
    !(await cards()).includes(v.private),
    'revocation removes previously visible private history',
  );
  await spa(root + 'liked');
  await loaded('liked');
  check(
    !(await cards()).includes(v.private),
    'revoked liked video remains absent',
  );
  // Real session loss clears both sidebar and current personal results.
  await control('lost', '__session_fixture');
  jars.clear();
  await refreshLibrary();
  await c.wait(
    `!${store}.account && !${store}.changing && !!document.querySelector('.library-page__login')`,
  );
  check(
    (await cards()).length === 0 &&
      !(await c.evaluate("!!document.querySelector('.subscribed-channels')")),
    'actual 401 clears personal cards and subscribed channels',
  );
  // Mobile drawer is reachable by keyboard, and closes/restores its trigger.
  await login(viewer, root + 'history');
  await loaded('history');
  await c.viewport(390, 844, true);
  await c.click('.mobile-navigation button');
  await c.wait("!!document.querySelector('.navigation--open')");
  check(
    await c.evaluate(
      "document.activeElement.closest('#main-navigation')!==null && document.querySelector('#main-navigation').getAttribute('aria-modal')==='true'",
    ),
    'phone drawer takes focus and exposes personal navigation',
  );
  await c.evaluate(
    "document.querySelector('.navigation--open').dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))",
  );
  await c.wait("!document.querySelector('.navigation--open')");
  check(
    await c.evaluate('document.activeElement.getClientRects().length>0'),
    'closing phone drawer returns focus to a visible trigger',
  );
  await c.click('.mobile-navigation button');
  await c.click('#main-navigation a[href="/feed/liked"]');
  await loaded('liked');
  check(
    await c.evaluate(
      "location.pathname==='/feed/liked' && !document.querySelector('.navigation--open')",
    ),
    'phone personal navigation closes drawer and opens real list',
  );
  await c.viewport(320, 700, true);
  check(
    await c.evaluate('document.documentElement.scrollWidth<=innerWidth'),
    'personal page fits phone width',
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
    'no unexpected runtime errors: ' + errors.join('|'),
  );
  console.log(
    JSON.stringify({
      profile: 'library',
      browser: firefox ? 'Firefox' : 'Chromium',
      assertions,
      status: 'pass',
    }),
  );
} catch (error) {
  console.error(
    JSON.stringify({
      profile: 'library',
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
