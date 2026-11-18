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
let holdMe = false,
  meFailure = false,
  profileFailure = false,
  listFailure,
  holdProfile = false,
  holdList,
  nextFailure = false,
  badVideo = false,
  badPlaylist = false;
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
      me = url.pathname === '/api/v1/auth/me',
      profile = /^\/api\/v1\/channels\/[^/]+$/.test(url.pathname),
      kind = /\/channels\/[^/]+\/(videos|playlists)$/.exec(url.pathname)?.[1],
      call = { path: req.url, method: req.method, status: undefined };
    if (api) calls.push(call);
    if (me && holdMe) await hold();
    if (
      (me && meFailure) ||
      (profile && profileFailure) ||
      (kind && listFailure === kind) ||
      (kind && nextFailure && url.searchParams.has('cursor'))
    ) {
      call.status = 503;
      res
        .writeHead(503, {
          'content-type': 'application/problem+json',
          'cache-control': 'no-store',
        })
        .end(
          '{"code":"unavailable","detail":"<img src=x onerror=window.channelInjection=true>"}',
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
    if (kind === 'videos' && badVideo && response.status === 200)
      body = Buffer.from(
        body
          .toString()
          .replace(
            /("channel"\s*:\s*\{\s*"id"\s*:\s*)\d+/g,
            (_, prefix) => prefix + fixture.people.stranger.id,
          ),
      );
    if (kind === 'playlists' && badPlaylist && response.status === 200)
      body = Buffer.from(
        body
          .toString()
          .replace(
            /("channel_id"\s*:\s*)\d+/g,
            (_, prefix) => prefix + fixture.people.stranger.id,
          ),
      );
    if (profile && holdProfile) {
      holdProfile = false;
      await hold();
    }
    if (kind && holdList === kind) {
      holdList = undefined;
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
  throw new Error('Channel condition timed out');
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
    data: raw
      ? JSON.parse(raw, (key, value, context) =>
          key === 'id' && typeof value === 'number' ? context.source : value,
        )
      : null,
  };
}
async function all(kind, person = 'owner') {
  const result = [];
  let cursor;
  do {
    const r = await actorRequest(
      person,
      '/channels/' +
        tag +
        '/' +
        kind +
        (cursor ? '?cursor=' + encodeURIComponent(cursor) : ''),
    );
    assert.equal(r.status, 200);
    result.push(...r.data.items);
    cursor = r.data.next_cursor;
  } while (cursor);
  return result;
}
async function visibility(value) {
  const settings = await actorRequest(
    'owner',
    '/videos/' + v.public + '/settings',
  );
  assert.equal(settings.status, 200);
  const r = await actorRequest(
    'owner',
    '/videos/' + v.public + '/settings',
    'PUT',
    {
      title: settings.data.title,
      description: settings.data.description,
      visibility: value,
    },
  );
  assert.equal(r.status, 200);
}
const header = "document.querySelector('.channel-page__identity p')";
const selector = (kind) =>
  kind === 'videos'
    ? '.channel-page__grid--videos .video-card__preview'
    : '.channel-page__grid--playlists .playlist-card__preview';
async function ids(kind) {
  return JSON.parse(
    await c.evaluate(
      `JSON.stringify([...document.querySelectorAll(${JSON.stringify(selector(kind))})].map(x=>new URL(x.href).searchParams.get('${kind === 'videos' ? 'v' : 'list'}')))`,
    ),
  );
}
async function settled(kind) {
  await c.wait(
    `${queryClient}.getQueryCache().findAll({queryKey:['channel-${kind}'],type:'active'}).some(q=>q.queryKey[2] && q.state.fetchStatus==='idle' && q.state.status==='success')`,
  );
}
async function open(path = root) {
  await c.navigate(base + path);
  await c.wait(
    `!!document.querySelector('.channel-page__header') || !!document.querySelector('.channel-page .page-state')`,
  );
}
async function spa(path) {
  await c.evaluate(
    `document.querySelector('#app').__vue_app__.config.globalProperties.$router.push(${JSON.stringify(path)})`,
  );
}
async function tab(kind) {
  await c.click(
    `.channel-page__tabs a:${kind === 'videos' ? 'first' : 'last'}-child`,
  );
}
async function loadAll(kind) {
  while (
    await c.evaluate(`!!document.querySelector('.channel-page__more button')`)
  ) {
    await c.click('.channel-page__more button');
    await settled(kind);
  }
  return ids(kind);
}
async function login(person = 'owner', path = root) {
  await c.navigate(base + '/login?returnTo=' + encodeURIComponent(path));
  await c.fill('#login-email', fixture.people[person].email);
  await c.fill('#login-password', 'abcdef');
  await c.click('.auth-card button[type=submit]');
  await c.wait(
    `location.pathname===${JSON.stringify(path)} && !!document.querySelector('.channel-page__header') && !!document.querySelector('.avatar-button')`,
  );
}
async function logout() {
  await c.click('.avatar-button');
  await c.click('#logout-button');
  await c.wait(
    `!document.querySelector('.avatar-button') && !!document.querySelector('.channel-page__header')`,
  );
}
async function refetch(kind) {
  await c.evaluate(
    `void ${queryClient}.refetchQueries({queryKey:['${kind}'],type:'active'})`,
  );
}
async function retry() {
  await c.click('.channel-page .page-state button');
}
async function keyEnter() {
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
  await visibility('public');
  const expectedVideos = await all('videos'),
    expectedPlaylists = await all('playlists');
  assert.equal(expectedVideos.length, 41);
  assert.equal(expectedPlaylists.length, 41);
  await c.viewport(1280, 900);
  if (!firefox)
    await c.send('Emulation.setFocusEmulationEnabled', { enabled: true });
  holdMe = true;
  const before = calls.length;
  await open();
  await until(() => held.size === 1);
  check(
    !calls.slice(before).some((x) => x.path.includes('/channels/')) &&
      !(await c.evaluate(
        `!!document.querySelector('.channel-page__header') || !!document.querySelector('.video-card')`,
      )),
    'channel waits for confirmed viewer before requesting profile or lists',
  );
  holdMe = false;
  release();
  await settled('videos');
  check(
    await c.evaluate(
      `${header}.textContent==='@${tag}' && document.querySelector('.channel-page h1').textContent==='Общий автор' && document.title==='Общий автор - YouTube'`,
    ),
    'direct route renders real name, unique tag and document title',
  );
  check(
    await c.evaluate(
      `document.querySelectorAll('.channel-page__tabs a').length===2 && document.querySelector('.channel-page__tabs [aria-current]').textContent==='Видео' && !document.querySelector('.channel-page a[href*="/studio"]')`,
    ),
    'public channel exposes only its two scope tabs and no creator management',
  );
  check(
    JSON.stringify(await ids('videos')) ===
      JSON.stringify(expectedVideos.slice(0, 20).map((x) => x.id)),
    'first video page uses real server order and exact IDs',
  );
  check(
    BigInt(expectedVideos[0].id) > 9007199254740991n,
    'catalogue IDs exceed Number safe integer',
  );

  nextFailure = true;
  await c.click('.channel-page__more button');
  await c.wait(
    `!!document.querySelector('.channel-page__more [role=alert]') && !document.querySelector('.channel-page__more button').disabled`,
  );
  check(
    (await ids('videos')).length === 20,
    'video continuation error retains the confirmed page',
  );
  nextFailure = false;
  await c.click('.channel-page__more button');
  await settled('videos');
  await c.wait(
    `document.querySelectorAll('${selector('videos')}').length===40`,
  );
  const loaded = await loadAll('videos');
  check(
    JSON.stringify(loaded) ===
      JSON.stringify(expectedVideos.map((x) => x.id)) &&
      new Set(loaded).size === 41,
    'video cursor pages finish in server order without duplicates',
  );
  check(
    ![v.private, v.hidden, v.pending, v.draft].some((id) =>
      loaded.includes(id),
    ),
    'guest catalogue excludes private, hidden, draft and unready videos',
  );
  check(
    await c.evaluate(
      `document.querySelector('.video-card__preview[href="/watch?v=${v.public}"]').closest('.video-card').textContent.includes('<script>') && !document.querySelector('.video-card script') && !window.frontendInject`,
    ),
    'real HTML-like video title remains text',
  );

  await tab('playlists');
  await settled('playlists');
  check(
    await c.evaluate(
      `location.pathname.endsWith('/playlists') && document.querySelector('.channel-page__tabs [aria-current]').textContent==='Плейлисты'`,
    ),
    'playlist tab and active state persist in URL',
  );
  nextFailure = true;
  await c.click('.channel-page__more button');
  await c.wait(
    `!!document.querySelector('.channel-page__more [role=alert]') && !document.querySelector('.channel-page__more button').disabled`,
  );
  check(
    (await ids('playlists')).length === 20,
    'playlist continuation failure keeps first page',
  );
  nextFailure = false;
  await c.click('.channel-page__more button');
  await settled('playlists');
  const playlists = await loadAll('playlists');
  check(
    JSON.stringify(playlists) ===
      JSON.stringify(expectedPlaylists.map((x) => x.id)) &&
      new Set(playlists).size === 41,
    'playlist cursor pages keep server creation order without duplicates',
  );
  check(
    ![
      fixture.playlists.private,
      fixture.playlists.unlisted,
      fixture.playlists.empty_private,
      fixture.playlists.empty_unlisted,
    ].some((id) => playlists.includes(id)),
    'private and unlisted playlists are undiscoverable',
  );
  check(
    await c.evaluate(
      `document.querySelector('.playlist-card__preview[href$="list=${fixture.playlists.public}"]').closest('.playlist-card').querySelector('h2').textContent.includes('<img') && !document.querySelector('.playlist-card img') && !window.frontendInject`,
    ),
    'HTML-like playlist title is rendered literally',
  );
  check(
    await c.evaluate(
      `${queryClient}.getQueryCache().findAll({queryKey:['channel-videos']}).every(q=>!q.queryKey[2])`,
    ),
    'switching tabs removes the prior video query instead of retaining a disabled snapshot',
  );

  holdList = 'videos';
  await tab('videos');
  await until(() => held.size === 1);
  check(
    !(await c.evaluate(
      `!!document.querySelector('.video-card') || !!document.querySelector('.playlist-card')`,
    )),
    'return to a tab shows no old cards before fresh response',
  );
  release();
  await settled('videos');
  holdList = 'playlists';
  await tab('playlists');
  await until(() => held.size === 1);
  await tab('videos');
  await settled('videos');
  release();
  await pause(200);
  check(
    !(await c.evaluate(`!!document.querySelector('.playlist-card')`)),
    'late playlist response cannot replace current video tab',
  );
  holdProfile = true;
  await spa('/@' + fixture.people.stranger.tag);
  await until(() => held.size === 1);
  await spa(root);
  await settled('videos');
  release();
  await pause(200);
  check(
    await c.evaluate(`${header}.textContent==='@${tag}'`),
    'late profile cannot put another same-named channel under current URL',
  );

  await spa('/@' + fixture.people.stranger.tag);
  await settled('videos');
  check(
    await c.evaluate(
      `${header}.textContent==='@${fixture.people.stranger.tag}' && document.querySelector('.channel-page h1').textContent==='Общий автор' && document.querySelector('.channel-page__content').textContent.includes('На канале пока нет видео')`,
    ),
    'same display names retain independent tags and empty contents',
  );
  await tab('playlists');
  await settled('playlists');
  check(
    await c.evaluate(
      `document.querySelector('.channel-page__content').textContent.includes('На канале пока нет публичных плейлистов')`,
    ),
    'empty playlist tab is distinct from failure',
  );
  const suffix = crypto.randomUUID().replaceAll('-', '').slice(0, 12),
    person = 'new';
  fixture.people[person] = {
    name: 'Общий автор',
    tag: 'channel_' + suffix,
    email: 'channel-' + suffix + '@example.com',
  };
  const registered = await actorRequest('owner', '/auth/register', 'POST', {
    ...fixture.people[person],
    password: 'abcdef',
    password_confirmation: 'abcdef',
  });
  assert.equal(registered.status, 201);
  await login(person, '/@' + fixture.people[person].tag);
  await settled('videos');
  check(
    await c.evaluate(
      `${header}.textContent==='@${fixture.people[person].tag}' && document.querySelector('.channel-page__content').textContent.includes('На канале пока нет видео') && !document.querySelector('.channel-page a[href*="/studio"]')`,
    ),
    'newly registered own channel is a normal empty public channel',
  );
  await tab('playlists');
  await settled('playlists');
  check(
    (await ids('playlists')).length === 0,
    'new channel has no invented playlists',
  );

  await login('owner');
  await settled('videos');
  const ownerIds = await loadAll('videos');
  check(
    JSON.stringify(ownerIds) ===
      JSON.stringify(expectedVideos.map((x) => x.id)),
    'owner sees the same public videos as guest',
  );
  await tab('playlists');
  await settled('playlists');
  check(
    JSON.stringify(await loadAll('playlists')) ===
      JSON.stringify(expectedPlaylists.map((x) => x.id)),
    'owner public tab excludes personal private/unlisted playlists',
  );
  await login('invited');
  await settled('videos');
  check(
    JSON.stringify(await loadAll('videos')) ===
      JSON.stringify(expectedVideos.map((x) => x.id)) &&
      (await actorRequest('invited', '/videos/' + v.private)).status === 200,
    'real private invitation grants direct viewing without adding a public channel card',
  );

  meFailure = true;
  await refetch('session');
  await c.wait(
    `document.querySelector('.channel-page .page-state h1')?.textContent==='Не удалось проверить аккаунт'`,
  );
  check(
    !(await c.evaluate(
      `!!document.querySelector('.channel-page__header') || !!document.querySelector('.video-card')`,
    )),
    'identity failure removes prior profile and catalogue',
  );
  meFailure = false;
  await retry();
  await settled('videos');
  check(
    (await ids('videos')).length > 0,
    'identity retry restores confirmed channel',
  );
  profileFailure = true;
  await refetch('channel');
  await c.wait(
    `document.querySelector('.channel-page .page-state h1')?.textContent==='Не удалось загрузить канал'`,
  );
  check(
    !(await c.evaluate(
      `!!document.querySelector('.channel-page__header') || !!document.querySelector('.video-card')`,
    )),
    'profile refresh failure leaves no previous identity or cards',
  );
  profileFailure = false;
  await retry();
  await settled('videos');

  for (const kind of ['videos', 'playlists']) {
    if (kind === 'playlists') {
      await tab(kind);
      await settled(kind);
    }
    listFailure = kind;
    await refetch('channel-' + kind);
    await c.wait(
      `document.querySelector('.channel-page .page-state h1')?.textContent==='Не удалось загрузить список'`,
    );
    check(
      await c.evaluate(
        `!!document.querySelector('.channel-page__header') && !document.querySelector('${selector(kind)}') && !window.channelInjection`,
      ),
      kind +
        ' failure preserves correct profile without stale rows or executable error',
    );
    listFailure = undefined;
    await retry();
    await settled(kind);
    check((await ids(kind)).length > 0, kind + ' list retry recovers');
  }
  badPlaylist = true;
  await refetch('channel-playlists');
  await c.wait(
    `document.querySelector('.channel-page .page-state h1')?.textContent==='Не удалось загрузить список'`,
  );
  check(
    (await ids('playlists')).length === 0,
    'malformed playlist DTO from another channel cannot appear under this profile',
  );
  badPlaylist = false;
  await retry();
  await settled('playlists');
  await tab('videos');
  await settled('videos');
  badVideo = true;
  await refetch('channel-videos');
  await c.wait(
    `document.querySelector('.channel-page .page-state h1')?.textContent==='Не удалось загрузить список'`,
  );
  check(
    (await ids('videos')).length === 0,
    'malformed video DTO from another channel is rejected',
  );
  badVideo = false;
  await retry();
  await settled('videos');

  await login('owner', root + '/videos');
  await settled('videos');
  await loadAll('videos');
  await tab('playlists');
  await settled('playlists');
  await visibility('private');
  await c.evaluate(
    `window.channelFlash=false;window.channelObserver=new MutationObserver(()=>{if(document.querySelector('.video-card__preview[href="/watch?v=${v.public}"]'))window.channelFlash=true;});window.channelObserver.observe(document.querySelector('.channel-page'),{subtree:true,childList:true});`,
  );
  holdList = 'videos';
  await c.evaluate('history.back()');
  await until(() => held.size === 1);
  check(
    !(await c.evaluate(
      `window.channelFlash || !!document.querySelector('.video-card')`,
    )),
    'Back waits for fresh tab data without flashing a now-private card',
  );
  release();
  await settled('videos');
  const revoked = await loadAll('videos');
  check(
    !revoked.includes(v.public) && revoked.length === 40,
    'new channel read enforces real public-to-private change even for owner',
  );
  await c.evaluate('window.channelObserver.disconnect()');
  await visibility('public');
  await refetch('channel-videos');
  await settled('videos');
  await tab('playlists');
  await settled('playlists');
  holdList = 'videos';
  await c.evaluate('history.back()');
  await until(() => held.size === 1);
  release();
  await settled('videos');
  await c.evaluate('history.forward()');
  await settled('playlists');
  check(
    await c.evaluate(
      `location.pathname.endsWith('/playlists') && document.querySelector('.channel-page__tabs [aria-current]').textContent==='Плейлисты'`,
    ),
    'Forward restores the playlist URL and active section',
  );
  await c.reload();
  await settled('playlists');
  check(
    await c.evaluate(
      `location.pathname.endsWith('/playlists') && document.title==='Общий автор - YouTube'`,
    ),
    'reload preserves selected tab and channel title',
  );

  await logout();
  await open('/@' + tag.toUpperCase() + '/videos');
  await settled('videos');
  check(
    await c.evaluate(`${header}.textContent==='@${tag}'`),
    'uppercase tag resolves to canonical channel identity',
  );
  await open('/@missing_channel_123');
  await c.wait(
    `document.querySelector('.channel-page .page-state h1')?.textContent==='Канал недоступен'`,
  );
  check(
    !(await c.evaluate(`!!document.querySelector('.channel-page__header')`)),
    'unknown channel hides previous profile',
  );
  const invalidBefore = calls.length;
  await open('/@%3Cbad%3E');
  await c.wait(
    `document.querySelector('.channel-page .page-state h1')?.textContent==='Канал недоступен'`,
  );
  check(
    !calls.slice(invalidBefore).some((x) => x.path.includes('/channels/')),
    'invalid tag is rejected before channel API',
  );

  await open(root);
  await settled('videos');
  await loadAll('videos');
  await c.click('.video-card__preview[href="/watch?v=' + v.public + '"]');
  await c.wait(`!!document.querySelector('.watch-page h1')`);
  check(
    await c.evaluate(
      `location.pathname==='/watch' && new URLSearchParams(location.search).get('v')==='${v.public}'`,
    ),
    'channel video link opens exact logical publication',
  );
  await open(root + '/playlists');
  await settled('playlists');
  await c.click(
    '.playlist-card__preview[href$="list=' + fixture.playlists.empty + '"]',
  );
  await c.wait(`!!document.querySelector('.playlist-contents__header')`);
  check(
    await c.evaluate(
      `new URLSearchParams(location.search).get('list')==='${fixture.playlists.empty}' && ${header}.textContent==='@${tag}' && document.querySelector('.playlist-contents').textContent.includes('Здесь пока нет доступных видео')`,
    ),
    'playlist card opens empty contents on its real author channel',
  );
  const selectedCalls = calls.length;
  await tab('playlists');
  await settled('playlists');
  check(
    !(await c.evaluate(`!!document.querySelector('.playlist-contents')`)) &&
      calls
        .slice(selectedCalls)
        .some((x) => x.path.includes('/channels/' + tag + '/playlists')),
    'closing contents reloads current public playlist catalogue',
  );
  await c.navigate(base + '/playlist?list=' + fixture.playlists.empty_unlisted);
  await c.wait(`!!document.querySelector('.playlist-contents__header')`);
  check(
    await c.evaluate(
      `${header}.textContent==='@${tag}' && new URLSearchParams(location.search).get('list')==='${fixture.playlists.empty_unlisted}'`,
    ),
    'direct unlisted link still composes the resolved channel and contents',
  );
  await open(root + '/playlists?list=' + fixture.playlists.private);
  await c.wait(
    `document.querySelector('.playlist-contents .page-state h1')?.textContent==='Плейлист недоступен'`,
  );
  check(
    await c.evaluate(
      `!!document.querySelector('.channel-page__header') && !document.querySelector('.playlist-contents__header')`,
    ),
    'private contents failure retains only the public channel profile',
  );

  await c.viewport(390, 844, true);
  await open(root);
  await settled('videos');
  await c.evaluate(
    `document.querySelector('.channel-page__tabs a:last-child').focus()`,
  );
  await keyEnter();
  await settled('playlists');
  check(
    await c.evaluate(
      `location.pathname.endsWith('/playlists') && document.querySelector('.channel-page__tabs [aria-current]').textContent==='Плейлисты'`,
    ),
    'phone-width keyboard navigation reaches playlist tab',
  );
  await c.evaluate(`document.querySelector('.playlist-card__preview').focus()`);
  await keyEnter();
  await c.wait(`!!document.querySelector('.playlist-contents__header')`);
  check(
    await c.evaluate(
      `!!document.querySelector('.channel-page__header') && !!new URLSearchParams(location.search).get('list')`,
    ),
    'phone keyboard opens playlist contents',
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
      profile: 'channel-page',
      browser: firefox ? 'Firefox' : 'Chromium',
      assertions,
      status: 'pass',
    }),
  );
} catch (error) {
  console.error(
    JSON.stringify({
      profile: 'channel-page',
      assertions,
      path: await c.evaluate('location.pathname+location.search'),
      state: await c.evaluate(
        "document.querySelector('.channel-page .page-state h1')?.textContent",
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
