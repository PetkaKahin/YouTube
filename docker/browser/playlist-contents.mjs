import assert from 'node:assert/strict';
import { createServer, request as httpRequest } from 'node:http';
import { readFile } from 'node:fs/promises';
import { browser, pause } from './cdp.mjs';
import { firefoxBrowser } from './bidi.mjs';

let fixture;
for (let i = 0; i < 1200; i++) {
  try {
    fixture = JSON.parse(await readFile('/work/watch-fixture.json', 'utf8'));
    if (fixture.playlists?.empty_private) break;
  } catch {}
  await pause(100);
}
if (!fixture?.playlists?.empty_private)
  throw new Error('Channel fixture needs --media --channel --links');
const firefox = process.argv.includes('--firefox'),
  v = fixture.videos,
  tag = fixture.people.owner.tag,
  root = '/@' + tag,
  host = 'http://youtube-frontend-upload-gateway:8080/api/v1';
const p = fixture.playlists;
let meFailure = false,
  holdMe = false,
  failure = false,
  nextFailure = false,
  holdId,
  badChannel = false;
const held = new Set(),
  sockets = new Set(),
  calls = [];
const hold = () => new Promise((resolve) => held.add(resolve));
function release() {
  for (const resolve of held) resolve();
  held.clear();
}
const server = createServer(async (req, res) => {
  try {
    const api = req.url.startsWith('/api/'),
      url = new URL(req.url, 'http://localhost'),
      list = /^\/api\/v1\/playlists\/(\d+)$/.exec(url.pathname)?.[1],
      me = url.pathname === '/api/v1/auth/me',
      call = { path: req.url, method: req.method, status: undefined };
    if (api) calls.push(call);
    if (me && holdMe) await hold();
    if (
      (me && meFailure) ||
      (list && (failure || (nextFailure && url.searchParams.has('cursor'))))
    ) {
      call.status = 503;
      res
        .writeHead(503, {
          'content-type': 'application/problem+json',
          'cache-control': 'no-store',
        })
        .end(
          '{"code":"unavailable","detail":"<img src=x onerror=window.playlistInjection=true>"}',
        );
      return;
    }
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    const headers = new Headers();
    for (const name of ['cookie', 'content-type', 'x-csrftoken', 'range'])
      if (req.headers[name]) headers.set(name, req.headers[name]);
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
    call.status = response.status;
    let body = Buffer.from(await response.arrayBuffer());
    if (list && badChannel && response.status === 200)
      body = Buffer.from(
        body
          .toString()
          .replace(
            /("channel_id"\s*:\s*)\d+/g,
            (_, prefix) => prefix + fixture.people.stranger.id,
          ),
      );
    if (list && holdId === list) {
      holdId = undefined;
      await hold();
    }
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
  jars = new Map(),
  card = (id) =>
    `.playlist-contents .video-card__preview[href="/watch?v=${id}"]`,
  canonical = (id, target = tag) => '/@' + target + '/playlists?list=' + id,
  direct = (id) => '/playlist?list=' + id;
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
  throw new Error('Playlist contents condition timed out');
}
function wireBody(body) {
  if (body?.playlist_ids) {
    return (
      '{"playlist_ids":[' +
      body.playlist_ids
        .map((id) => {
          assert.match(String(id), /^[1-9][0-9]*$/);
          return id;
        })
        .join(',') +
      ']}'
    );
  }
  return body === undefined ? undefined : JSON.stringify(body);
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
    body: wireBody(body),
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
async function allContents(person = 'stranger', id = p.public) {
  const result = [];
  let cursor;
  do {
    const r = await actorRequest(
      person,
      '/playlists/' +
        id +
        (cursor ? '?cursor=' + encodeURIComponent(cursor) : ''),
    );
    assert.equal(r.status, 200);
    result.push(...r.data.items);
    cursor = r.data.next_cursor;
  } while (cursor);
  return result;
}
async function selection(id) {
  const result = [];
  let cursor;
  do {
    const r = await actorRequest(
      'owner',
      '/videos/' +
        id +
        '/playlists' +
        (cursor ? '?cursor=' + encodeURIComponent(cursor) : ''),
    );
    assert.equal(r.status, 200);
    result.push(...r.data.items.filter((x) => x.selected).map((x) => x.id));
    cursor = r.data.next_cursor;
  } while (cursor);
  return result;
}
async function setSelection(id, ids) {
  const r = await actorRequest('owner', '/videos/' + id + '/playlists', 'PUT', {
    playlist_ids: ids,
  });
  assert.equal(r.status, 200);
}
async function permission(allowed) {
  const r = await actorRequest(
    'owner',
    '/videos/' + v.private + '/permissions',
    'PUT',
    { email: fixture.people.invited.email, allowed },
  );
  assert.equal(r.status, 200);
}
async function api(path, method = 'GET', body) {
  return JSON.parse(
    await c.evaluate(
      `(async()=>{const cookie=document.cookie.split('; ').find(x=>x.startsWith('csrftoken='));const r=await fetch('/api/v1'+${JSON.stringify(path)},{method:${JSON.stringify(method)},headers:{'content-type':'application/json',...(cookie?{'x-csrftoken':decodeURIComponent(cookie.slice(10))}:{})},body:${body === undefined ? 'undefined' : JSON.stringify(JSON.stringify(body))}});return JSON.stringify({status:r.status,data:r.status===204?null:await r.json()});})()`,
    ),
  );
}
async function navigate(path) {
  await c.navigate(base + path);
}
async function settled(id) {
  await c.wait(
    `${queryClient}.getQueryCache().findAll({queryKey:['playlist'],type:'active'}).some(q=>q.queryKey[2]==='${id}'&&q.state.fetchStatus==='idle'&&q.state.status==='success')`,
  );
}
async function ready(id) {
  await c.wait(
    `location.pathname===${JSON.stringify(root + '/playlists')} && new URLSearchParams(location.search).get('list')==='${id}' && !!document.querySelector('.playlist-contents__header')`,
  );
  await settled(id);
}
async function open(id = p.public, resolver = false) {
  await navigate(resolver ? direct(id) : canonical(id));
  await ready(id);
}
async function ids() {
  return JSON.parse(
    await c.evaluate(
      `JSON.stringify([...document.querySelectorAll('.playlist-contents__videos .video-card__preview')].map(x=>new URL(x.href).searchParams.get('v')))`,
    ),
  );
}
async function loadAll(id = p.public) {
  while (
    await c.evaluate(
      `!!document.querySelector('.playlist-contents__more button')`,
    )
  ) {
    await c.click('.playlist-contents__more button');
    await settled(id);
  }
  return ids();
}
async function login(person = 'owner', to = canonical(p.public)) {
  await navigate('/login?returnTo=' + encodeURIComponent(to));
  await submitLogin(person);
}
async function submitLogin(person) {
  await c.fill('#login-email', fixture.people[person].email);
  await c.fill('#login-password', 'abcdef');
  await c.click('.auth-card button[type=submit]');
  await c.wait(
    `location.pathname!=='/login' && !!document.querySelector('.avatar-button')`,
  );
}
async function logout() {
  await c.click('.avatar-button');
  await c.click('#logout-button');
  await c.wait(`!document.querySelector('.avatar-button')`);
}
async function spa(path) {
  await c.evaluate(
    `document.querySelector('#app').__vue_app__.config.globalProperties.$router.push(${JSON.stringify(path)})`,
  );
}
async function refetch(kind = 'playlist') {
  await c.evaluate(
    `void ${queryClient}.refetchQueries({queryKey:['${kind}'],type:'active'})`,
  );
}
async function retry(selector = '.playlist-contents .page-state button') {
  await c.click(selector);
}
async function denied(selector = '.playlist-contents') {
  await c.wait(
    `document.querySelector('${selector} .page-state h1')?.textContent==='Плейлист недоступен'`,
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
try {
  await permission(true);
  await c.viewport(1280, 900);
  if (!firefox)
    await c.send('Emulation.setFocusEmulationEnabled', { enabled: true });
  holdMe = true;
  const before = calls.length;
  await navigate(direct(p.public));
  await until(() => held.size === 1);
  check(
    !calls
      .slice(before)
      .some((x) => /^\/api\/v1\/playlists\/\d+/.test(x.path)) &&
      !(await c.evaluate(
        `!!document.querySelector('.playlist-contents__header')`,
      )),
    'direct link waits for viewer identity before resolving metadata',
  );
  holdMe = false;
  release();
  await ready(p.public);
  const expected = await allContents();
  check(
    expected.length === 41 &&
      JSON.stringify(await ids()) ===
        JSON.stringify(expected.slice(0, 20).map((x) => x.id)),
    'first guest page has twenty accessible videos in server inclusion order',
  );
  check(
    BigInt(p.public) > 9007199254740991n &&
      (await c.evaluate(
        `new URLSearchParams(location.search).get('list')==='${p.public}'`,
      )),
    'direct link preserves exact large playlist ID',
  );
  check(
    await c.evaluate(
      `document.querySelector('.channel-page__identity p').textContent==='@${tag}' && document.querySelector('.playlist-contents__header h2').textContent.includes('<img') && document.title.includes('<img')`,
    ),
    'resolver gets correct author and title from playlist metadata',
  );
  check(
    await c.evaluate(
      `!document.querySelector('.playlist-contents__header img') && !document.querySelector('.playlist-contents__description script') && document.querySelector('.playlist-contents__description').textContent.includes('<script>') && !window.frontendInject`,
    ),
    'real HTML-like title and multiline description remain text',
  );
  nextFailure = true;
  await c.click('.playlist-contents__more button');
  await c.wait(
    `!!document.querySelector('.playlist-contents__more [role=alert]') && !document.querySelector('.playlist-contents__more button').disabled`,
  );
  check(
    (await ids()).length === 20 &&
      (await c.evaluate(
        `!!document.querySelector('.playlist-contents__header')`,
      )),
    'next-page failure retains confirmed metadata and first portion',
  );
  nextFailure = false;
  await c.click('.playlist-contents__more button');
  await settled(p.public);
  await c.wait(
    `document.querySelectorAll('.playlist-contents__videos li').length===40`,
  );
  await c.evaluate(
    `document.querySelector('.playlist-contents__more').scrollIntoView({block:'center'})`,
  );
  await c.wait(
    `document.querySelectorAll('.playlist-contents__videos li').length===41`,
  );
  check(
    JSON.stringify(await ids()) === JSON.stringify(expected.map((x) => x.id)) &&
      !(await c.evaluate(
        `!!document.querySelector('.playlist-contents__more')`,
      )),
    'cursor continuation and scroll finish the list in exact order',
  );
  check(
    ![v.private, v.hidden, v.pending, v.draft].some((id) =>
      expected.map((x) => x.id).includes(id),
    ) &&
      !(await c.evaluate(
        `!!document.querySelector(${JSON.stringify(card(v.private))})`,
      )),
    'guest list discloses no private, hidden, draft or unready items',
  );
  check(
    !calls.some(
      (x) =>
        x.path.includes('/videos/' + v.private + '/preview') ||
        x.path.includes('/videos/' + v.private + '/hls/'),
    ),
    'guest browsing did not request private preview or HLS bytes',
  );

  await open(p.public);
  const moved = expected[0].id,
    selected = await selection(moved);
  await setSelection(
    moved,
    selected.filter((id) => id !== p.public),
  );
  await setSelection(moved, selected);
  const repeated = await loadAll();
  check(
    repeated.length === 41 &&
      new Set(repeated).size === 41 &&
      repeated.filter((id) => id === moved).length === 1,
    're-inclusion across cursor pages cannot duplicate a previously loaded video',
  );
  await refetch();
  await settled(p.public);
  const reordered = await loadAll();
  const newOrder = await allContents();
  check(
    newOrder.at(-1).id === moved &&
      JSON.stringify(reordered) === JSON.stringify(newOrder.map((x) => x.id)),
    'fresh read reflects removal and re-inclusion at the end',
  );

  await open(p.unlisted, true);
  check(
    (await ids()).length === 1 &&
      (await ids())[0] === v.public &&
      (await c.evaluate(
        `document.querySelector('.playlist-contents__visibility').textContent==='Доступ по ссылке'`,
      )),
    'guest direct unlisted contains only independently public video',
  );
  for (const id of [p.empty, p.empty_unlisted]) {
    await open(id, true);
    check(
      await c.evaluate(
        `document.querySelector('.playlist-contents .page-state h1')?.textContent==='Здесь пока нет доступных видео' && !document.querySelector('.playlist-contents__videos') && document.querySelector('.channel-page__identity p').textContent==='@${tag}'`,
      ),
      'empty accessible playlist resolves author without guessing a first video',
    );
  }
  await navigate('/');
  await c.wait(`!!document.querySelector('.video-grid')`);
  await spa(direct(p.empty_unlisted) + '#keep');
  await ready(p.empty_unlisted);
  check(
    await c.evaluate(`location.hash==='#keep'`),
    'resolver preserves URL fragment while replacing route',
  );
  await c.evaluate('history.back()');
  await c.wait(`location.pathname==='/'`);
  check(
    true,
    'one Back returns before the resolver without an intermediate playlist URL',
  );
  await c.evaluate('history.forward()');
  await ready(p.empty_unlisted);
  check(true, 'Forward restores canonical playlist contents');

  await navigate(direct(p.private));
  await denied('.playlist-link-page');
  check(
    !(await c.evaluate(
      `!!document.querySelector('.channel-page__header') || !!document.querySelector('.playlist-contents__header')`,
    )),
    'private direct link exposes neither title nor author to guest',
  );
  await c.click('.playlist-link-page__login');
  await c.wait(`location.pathname==='/login'`);
  check(
    await c.evaluate(
      `new URLSearchParams(location.search).get('returnTo')===${JSON.stringify(direct(p.private))}`,
    ),
    'private guest login preserves original direct link',
  );
  await submitLogin('invited');
  await denied('.playlist-link-page');
  check(
    !(await c.evaluate(`!!document.querySelector('.channel-page__header')`)),
    'invitation to a video does not reveal private playlist metadata',
  );
  await login('owner', direct(p.private));
  await ready(p.private);
  check(
    JSON.stringify(await ids()) === JSON.stringify([v.public, v.private]) &&
      (await c.evaluate(
        `document.querySelector('.playlist-contents__visibility').textContent==='Приватный плейлист'`,
      )),
    'owner sees private playlist with separate video rights',
  );
  await open(p.empty_private, true);
  check(
    await c.evaluate(
      `document.querySelector('.playlist-contents .page-state h1')?.textContent==='Здесь пока нет доступных видео'`,
    ),
    'owner empty private resolves from server channel',
  );
  await open(p.public);
  const ownerOrder = await allContents('owner');
  check(
    (await loadAll()).length === 42 &&
      !ownerOrder.some((x) => x.id === v.hidden),
    'owner sees allowed private entry but not hidden video',
  );

  await login('invited');
  await ready(p.public);
  const invitedOrder = await allContents('invited');
  check(
    JSON.stringify(await loadAll()) ===
      JSON.stringify(invitedOrder.map((x) => x.id)) &&
      invitedOrder.some((x) => x.id === v.private),
    'invited viewer public playlist respects actual email grant and order',
  );
  await open(p.unlisted, true);
  check(
    (await ids()).includes(v.private) && (await ids()).length === 2,
    'unlisted does not suppress independently allowed private video',
  );
  await c.wait(
    `document.querySelector(${JSON.stringify(card(v.private) + ' img')})?.naturalWidth>0`,
  );
  await permission(false);
  check(
    (await actorRequest('invited', '/videos/' + v.private + '/hls/master.m3u8'))
      .status === 404,
    'email revocation also closes real private manifest',
  );
  await c.evaluate(
    `document.querySelector(${JSON.stringify(card(v.private) + ' img')}).src += '?f20=revoked'`,
  );
  await c.wait(`!document.querySelector(${JSON.stringify(card(v.private))})`);
  check(
    (await ids()).length === 1 &&
      (await c.evaluate(
        `!!document.querySelector('.playlist-contents__header')`,
      )),
    'real preview denial refreshes allowed playlist and removes revoked entry',
  );
  await permission(true);
  await refetch();
  await settled(p.unlisted);
  check(
    (await ids()).includes(v.private),
    'fresh read restores entry after independent permission returns',
  );
  await logout();
  await ready(p.unlisted);
  check(
    (await ids()).length === 1 &&
      !(await c.evaluate(
        `!!document.querySelector(${JSON.stringify(card(v.private))})`,
      )),
    'logout removes private entry without closing public/unlisted playlist',
  );

  await navigate(direct(p.empty_private));
  await denied('.playlist-link-page');
  check(
    !(await c.evaluate(`!!document.querySelector('.channel-page__header')`)),
    'empty private is equally closed to guest',
  );
  await login('owner', direct(p.private));
  await ready(p.private);
  const privateTitle = await c.evaluate(
    `document.querySelector('.playlist-contents__header h2').textContent`,
  );
  await api('/auth/logout', 'POST');
  await refetch();
  await denied();
  check(
    !(await c.evaluate(
      `!!document.querySelector('.playlist-contents__header') || !!document.querySelector('.playlist-contents__videos')`,
    )) &&
      !(await c.evaluate(
        `document.title.includes(${JSON.stringify(privateTitle)})`,
      )),
    'real private denial removes metadata, cards and prior document title',
  );
  check(
    await c.evaluate(
      `${queryClient}.getQueryCache().findAll({queryKey:['playlist']}).every(q=>q.queryKey[2]!=='${p.private}' || !q.state.data)`,
    ),
    'denial removes the inaccessible query snapshot',
  );
  await refetch('session');
  await c.wait(`!document.querySelector('.avatar-button')`);

  meFailure = true;
  const beforeMe = calls.length;
  await navigate(direct(p.public));
  await c.wait(
    `document.querySelector('.playlist-link-page .page-state h1')?.textContent==='Не удалось проверить аккаунт'`,
  );
  check(
    !calls
      .slice(beforeMe)
      .some((x) => /^\/api\/v1\/playlists\/\d+/.test(x.path)) &&
      !(await c.evaluate(`!!document.querySelector('.channel-page__header')`)),
    'identity error is not treated as guest and does not resolve playlist',
  );
  meFailure = false;
  await retry('.playlist-link-page .page-state button');
  await ready(p.public);
  check(true, 'identity retry completes direct resolution');
  failure = true;
  await navigate(direct(p.public));
  await c.wait(
    `document.querySelector('.playlist-link-page .page-state h1')?.textContent==='Не удалось открыть плейлист'`,
  );
  check(
    !(await c.evaluate(
      `!!document.querySelector('.channel-page__header') || !!window.playlistInjection`,
    )),
    'resolver failure does not reveal owner or executable details',
  );
  failure = false;
  await retry('.playlist-link-page .page-state button');
  await ready(p.public);
  failure = true;
  await refetch();
  await c.wait(
    `document.querySelector('.playlist-contents .page-state h1')?.textContent==='Не удалось загрузить плейлист'`,
  );
  check(
    !(await c.evaluate(
      `!!document.querySelector('.playlist-contents__header') || !!document.querySelector('.playlist-contents__videos')`,
    )),
    'failed background contents read hides stale metadata and rows',
  );
  failure = false;
  await retry();
  await ready(p.public);
  check(true, 'contents retry restores actual state');

  holdId = p.public;
  await refetch();
  await until(() => held.size === 1);
  await spa(canonical(p.empty));
  await ready(p.empty);
  release();
  await pause(200);
  check(
    await c.evaluate(
      `document.querySelector('.playlist-contents .page-state h1')?.textContent==='Здесь пока нет доступных видео' && !document.querySelector('.playlist-contents__videos')`,
    ),
    'late list response cannot populate another playlist',
  );
  await open(p.public);
  holdId = p.public;
  await c.evaluate(
    `(async()=>{const r=document.querySelector('#app').__vue_app__.config.globalProperties.$router;await r.push(${JSON.stringify(root + '/playlists')});await r.push(${JSON.stringify(canonical(p.public))});})()`,
  );
  await until(() => held.size === 1);
  check(
    !(await c.evaluate(
      `!!document.querySelector('.playlist-contents__header') || !!document.querySelector('.playlist-contents__videos')`,
    )),
    'rapid close and reopen waits for fresh metadata without retaining old snapshot',
  );
  release();
  await ready(p.public);
  check(
    (await ids()).length === 20,
    'old cleanup cannot cancel the replacement query at the same address',
  );
  holdId = p.unlisted;
  await navigate(direct(p.unlisted));
  await until(() => held.size === 1);
  await spa(root + '/videos');
  await c.wait(`!!document.querySelector('.channel-page__grid--videos')`);
  release();
  await pause(200);
  check(
    await c.evaluate(
      `location.pathname.endsWith('/videos') && !new URLSearchParams(location.search).has('list')`,
    ),
    'late resolver cannot redirect after leaving',
  );

  await navigate(canonical(p.public, fixture.people.stranger.tag));
  await denied();
  check(
    !(await c.evaluate(
      `!!document.querySelector('.playlist-contents__header') || !!document.querySelector('.playlist-contents__videos')`,
    )),
    'same-named wrong channel cannot display another author playlist',
  );
  badChannel = true;
  await navigate(direct(p.public));
  await c.wait(
    `document.querySelector('.playlist-link-page .page-state h1')?.textContent==='Не удалось открыть плейлист'`,
  );
  check(
    !(await c.evaluate(`!!document.querySelector('.channel-page__header')`)),
    'malformed resolver DTO cannot redirect using mismatched owner',
  );
  badChannel = false;
  await retry('.playlist-link-page .page-state button');
  await ready(p.public);
  badChannel = true;
  await refetch();
  await denied();
  check(
    !(await c.evaluate(
      `!!document.querySelector('.playlist-contents__header')`,
    )),
    'malformed contents DTO hides its metadata in channel context',
  );
  badChannel = false;
  for (const value of [
    '0',
    '-1',
    '9223372036854775808',
    p.public + '&list=' + p.empty,
  ]) {
    const beforeInvalid = calls.length;
    await navigate('/playlist?list=' + value);
    await denied('.playlist-link-page');
    check(
      !calls
        .slice(beforeInvalid)
        .some((x) => /^\/api\/v1\/playlists\/\d+/.test(x.path)),
      'invalid or repeated playlist ID is rejected before API',
    );
  }
  await navigate(direct('9223372036854775807'));
  await denied('.playlist-link-page');
  check(
    !(await c.evaluate(`!!document.querySelector('.channel-page__header')`)),
    'valid missing int64 uses ordinary unavailable result without author',
  );

  await open(p.public);
  const now = await allContents();
  await loadAll();
  const first = now[0].id;
  await c.click(card(first));
  await c.wait(`!!document.querySelector('.watch-page h1')`);
  check(
    await c.evaluate(
      `location.pathname==='/watch' && new URLSearchParams(location.search).get('v')==='${first}'`,
    ),
    'playlist card opens exact video through ordinary watch permission check',
  );
  await login('invited');
  await ready(p.public);
  await c.click(card(v.private));
  await c.wait(`!!document.querySelector('.watch-page h1')`);
  await permission(false);
  await navigate('/watch?v=' + v.private);
  await c.wait(`document.body.textContent.includes('Видео недоступно')`);
  check(
    !(await c.evaluate(`!!document.querySelector('.player video')`)),
    'playlist navigation cannot bypass revoked private watch permission',
  );
  await permission(true);
  await open(p.unlisted);
  await c.click('.playlist-contents__back');
  await c.wait(
    `!new URLSearchParams(location.search).has('list') && !!document.querySelector('.channel-page__grid--playlists')`,
  );
  check(
    !(await c.evaluate(`!!document.querySelector('.playlist-contents')`)) &&
      (await c.evaluate(`document.title==='Общий автор - YouTube'`)),
    'back link closes contents and restores channel title',
  );

  await c.viewport(390, 844, true);
  await open(p.empty_unlisted, true);
  await c.evaluate(
    `document.querySelector('.playlist-contents__back').focus()`,
  );
  await enter();
  await c.wait(`!!document.querySelector('.channel-page__grid--playlists')`);
  check(
    !(await c.evaluate(`!!document.querySelector('.playlist-contents')`)),
    'phone keyboard reaches catalogue through back link',
  );
  await open(p.unlisted, true);
  await c.evaluate(
    `document.querySelector('.playlist-contents .video-card__preview').focus()`,
  );
  await enter();
  await c.wait(`!!document.querySelector('.watch-page h1')`);
  check(
    await c.evaluate(`location.pathname==='/watch'`),
    'phone keyboard can open permitted video',
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
    'no unexpected runtime errors: ' + errors.join(' | '),
  );
  console.log(
    JSON.stringify({
      profile: 'playlist-contents',
      browser: firefox ? 'Firefox' : 'Chromium',
      assertions,
      status: 'pass',
    }),
  );
} catch (error) {
  console.error(
    JSON.stringify({
      profile: 'playlist-contents',
      assertions,
      path: await c.evaluate('location.pathname+location.search'),
      state: await c.evaluate(
        "document.querySelector('.playlist-contents .page-state h1,.playlist-link-page .page-state h1')?.textContent",
      ),
      queries: JSON.parse(
        await c.evaluate(
          `JSON.stringify(${queryClient}.getQueryCache().findAll({queryKey:['playlist']}).map(q=>({key:q.queryKey,hasData:!!q.state.data,status:q.state.status,fetch:q.state.fetchStatus})))`,
        ),
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
