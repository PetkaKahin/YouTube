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
const v = fixture.videos;
const host = 'http://youtube-frontend-upload-gateway:8080/api/v1';
let readStatus = 503,
  meFailure = false,
  holdMe = false,
  nextFailure = false,
  readFailure = false,
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
      list = req.url.startsWith('/api/v1/playlists/mine'),
      write = req.url === '/api/v1/playlists' && req.method === 'POST';
    if (api) calls.push({ path: req.url, method: req.method });
    if (req.url === '/api/v1/auth/me' && holdMe) await hold();
    if (req.url === '/api/v1/auth/me' && meFailure) {
      res
        .writeHead(503, {
          'content-type': 'application/problem+json',
          'cache-control': 'no-store',
        })
        .end('{"code":"unavailable"}');
      return;
    }
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    let entry;
    if (write) {
      const body = JSON.parse(Buffer.concat(chunks).toString());
      entry = {
        path: req.url,
        fields: Object.keys(body),
        titleLength: [...body.title].length,
        descriptionLength: [...body.description].length,
        visibility: body.visibility,
        status: undefined,
      };
      writes.push(entry);
      if (holdWriteBefore) await hold();
    }
    if (
      (list &&
        (readFailure ||
          (nextFailure &&
            new URL(req.url, 'http://localhost').searchParams.has(
              'cursor',
            )))) ||
      (write && writeFailure)
    ) {
      if (entry) entry.status = 503;
      res
        .writeHead(list ? readStatus : 503, {
          'content-type': 'application/problem+json',
          'cache-control': 'no-store',
        })
        .end(
          JSON.stringify({
            code: 'unavailable',
            detail: '<img src=x onerror="window.studioInjection=true">',
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
  studio = '/studio/content/playlists',
  queryClient =
    "Object.values(document.querySelector('#app').__vue_app__._context.provides).find(x=>typeof x?.getQueryCache==='function')",
  jars = new Map(),
  marker = 'F21-' + crypto.randomUUID().slice(0, 8),
  submit = '.playlist-dialog button[type=submit]';
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
  throw new Error('Studio playlist condition timed out');
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
async function all(person = 'author', path = '/playlists/mine') {
  const result = [];
  let cursor;
  do {
    const r = await actorRequest(
      person,
      path + (cursor ? '?cursor=' + encodeURIComponent(cursor) : ''),
    );
    assert.equal(r.status, 200);
    result.push(...r.data.items);
    cursor = r.data.next_cursor;
  } while (cursor);
  return result;
}
async function settled() {
  await c.wait(
    `${queryClient}.getQueryCache().findAll({queryKey:['studio-playlists'],type:'active'}).some(q=>q.state.status==='success' && q.state.fetchStatus==='idle')`,
  );
}
async function navigate(path = studio) {
  await c.navigate(base + path);
}
async function spa(path) {
  await c.evaluate(
    `document.querySelector('#app').__vue_app__.config.globalProperties.$router.push(${JSON.stringify(path)})`,
  );
  await c.wait(`location.pathname===${JSON.stringify(path)}`);
}
async function login(person = 'author', to = studio) {
  await navigate('/login?returnTo=' + encodeURIComponent(to));
  await submitLogin(person, to);
}
async function submitLogin(person = 'author', to = studio) {
  await c.fill('#login-email', fixture.people[person].email);
  await c.fill('#login-password', 'abcdef');
  await c.click('.auth-card button[type=submit]');
  await c.wait(
    `location.pathname===${JSON.stringify(to)} && !!document.querySelector('.studio-header .studio-avatar')`,
  );
  await settled();
}
async function logout() {
  await c.click('.studio-header .studio-avatar');
  await c.click('#studio-logout');
  await c.wait(`!!document.querySelector('.studio-playlists__login')`);
}
async function open() {
  await c.click('.studio-create');
  await c.click('#studio-create-menu button');
  await c.wait(
    `document.querySelector('.playlist-dialog')?.open && document.activeElement.id==='playlist-title'`,
  );
}
async function mode(value) {
  await c.evaluate(
    `(()=>{const s=document.querySelector('#playlist-visibility');s.value=${JSON.stringify(value)};s.dispatchEvent(new Event('change',{bubbles:true}));})()`,
  );
}
async function fill(title, description = '', visibility = 'public') {
  await c.fill('#playlist-title', title);
  await c.fill('#playlist-description', description);
  await mode(visibility);
}
async function saved(title) {
  await c.wait(
    `!document.querySelector('.playlist-dialog').open && document.querySelector('.studio-playlists__notice')?.textContent==='Плейлист создан'`,
  );
  await settled();
  await c.wait(
    `document.querySelector('.studio-table strong')?.textContent===${JSON.stringify(title.trim())}`,
  );
}
async function create(title, description = '', visibility = 'public') {
  await open();
  await fill(title, description, visibility);
  await c.click(submit);
  await saved(title);
  return (await all())[0];
}
async function ids() {
  return JSON.parse(
    await c.evaluate(
      `JSON.stringify([...document.querySelectorAll('.studio-table tbody tr')].map(x=>x.dataset.playlistId))`,
    ),
  );
}
async function loadAll() {
  while (
    await c.evaluate(
      `!!document.querySelector('.studio-playlists__more button')`,
    )
  ) {
    await c.click('.studio-playlists__more button');
    await settled();
  }
  return ids();
}
async function refetch(kind = 'studio-playlists') {
  await c.evaluate(
    `void ${queryClient}.refetchQueries({queryKey:['${kind}'],type:'active'})`,
  );
}
async function key(value) {
  if (firefox) {
    await c.key({ Escape: '\uE00C', Tab: '\uE004', Enter: '\uE007' }[value]);
    return;
  }
  const code = { Escape: 27, Tab: 9, Enter: 13 }[value];
  await c.send('Input.dispatchKeyEvent', {
    type: 'keyDown',
    key: value,
    windowsVirtualKeyCode: code,
    ...(value === 'Enter' ? { text: '\r' } : {}),
  });
  await c.send('Input.dispatchKeyEvent', {
    type: 'keyUp',
    key: value,
    windowsVirtualKeyCode: code,
  });
}
try {
  for (const person of ['author', 'other']) {
    const suffix = crypto.randomUUID().replaceAll('-', '').slice(0, 12);
    fixture.people[person] = {
      name: 'Автор Студии',
      tag: 'studio_' + suffix,
      email: 'studio-' + suffix + '@example.com',
    };
    const r = await actorRequest('owner', '/auth/register', 'POST', {
      ...fixture.people[person],
      password: 'abcdef',
      password_confirmation: 'abcdef',
    });
    assert.equal(r.status, 201);
  }
  await c.viewport(1280, 900);
  if (!firefox)
    await c.send('Emulation.setFocusEmulationEnabled', { enabled: true });
  holdMe = true;
  const beforeIdentity = calls.length;
  await navigate();
  await until(() => held.size === 1);
  check(
    !calls
      .slice(beforeIdentity)
      .some((x) => x.path.includes('/playlists/mine')) &&
      !(await c.evaluate(`!!document.querySelector('.studio-create')`)),
    'initial identity wait does not request personal list or offer creator actions',
  );
  holdMe = false;
  release();
  await c.wait(`!!document.querySelector('.studio-playlists__login')`);
  check(
    !calls.some((x) => x.path.includes('/playlists/mine')),
    'guest receives sign-in without personal read',
  );
  await c.click('.studio-playlists__login .button');
  await c.wait(`location.pathname==='/login'`);
  check(
    await c.evaluate(
      `new URLSearchParams(location.search).get('returnTo')===${JSON.stringify(studio)}`,
    ),
    'Studio guest sign-in retains return destination',
  );
  await submitLogin();
  check(
    await c.evaluate(
      `document.querySelector('.studio-playlists .page-state h1')?.textContent==='Пока нет плейлистов'`,
    ),
    'new owner has real empty list',
  );

  await open();
  check(
    await c.evaluate(
      `document.querySelector('#playlist-visibility').value==='public' && document.querySelector('${submit}').disabled`,
    ),
    'new modal defaults to public with disabled empty title',
  );
  const invalidBefore = writes.length;
  for (const [title, description, target] of [
    ['   ', '', 'playlist-title'],
    ['😀'.repeat(101), '', 'playlist-title'],
    ['Название', 'я'.repeat(5001), 'playlist-description'],
    ['\0', '', 'playlist-title'],
  ]) {
    await fill(title, description);
    await c.evaluate(
      `document.querySelector('.playlist-dialog form').requestSubmit()`,
    );
    await c.wait(`document.activeElement.id==='${target}'`);
    check(
      await c.evaluate(
        `document.querySelector('#${target}').getAttribute('aria-invalid')==='true'`,
      ),
      'invalid draft is retained, explained and focused',
    );
  }
  check(
    writes.length === invalidBefore,
    'invalid or oversized drafts do not send creation',
  );
  await fill('😀'.repeat(101));
  check(
    await c.evaluate(
      `document.querySelector('#playlist-title').value.length===202 && document.querySelector('#playlist-title-hint').textContent==='101/100'`,
    ),
    'overlong Unicode title is not silently truncated',
  );
  await fill('Граница', 'я'.repeat(5001));
  check(
    await c.evaluate(
      `document.querySelector('#playlist-description').value.length===5001 && document.querySelector('#playlist-description-hint').textContent==='5001/5000'`,
    ),
    'overlong description stays intact with explicit limit',
  );
  await fill('Черновик отмены');
  await key('Escape');
  await c.wait(`!document.querySelector('.playlist-dialog').open`);
  check(
    await c.evaluate(
      `document.activeElement.classList.contains('studio-create') && document.querySelector('#playlist-title').value===''`,
    ),
    'Escape clears draft and restores Create focus',
  );
  await open();
  await fill('Новая форма');
  await c.evaluate(
    `const d=document.querySelector('.playlist-dialog');d.dispatchEvent(new Event('close'))`,
  );
  check(
    await c.evaluate(
      `document.querySelector('#playlist-title').value==='Новая форма' && document.querySelector('.playlist-dialog').open`,
    ),
    'late close event cannot clear an already reopened form',
  );

  const title = '😀'.repeat(100),
    description = 'я'.repeat(5000);
  await fill('  ' + title + '  ', description);
  await c.evaluate(`document.querySelector('${submit}').focus()`);
  await key('Tab');
  check(
    await c.evaluate(`document.activeElement.closest('dialog')?.open===true`),
    'Tab cycles within modal',
  );
  holdWriteBefore = true;
  const beforeDouble = writes.length;
  await c.evaluate(
    `const f=document.querySelector('.playlist-dialog form');for(let i=0;i<4;i++)f.requestSubmit()`,
  );
  await until(() => held.size === 1);
  await key('Escape');
  check(
    writes.length === beforeDouble + 1 &&
      (await c.evaluate(
        `document.querySelector('.playlist-dialog').open && document.querySelector('#playlist-title').disabled && document.querySelector('[aria-label="Закрыть создание плейлиста"]').disabled && !document.querySelector('.studio-playlists__notice')`,
      )),
    'synchronous double submit sends one command without premature success, edit or close',
  );
  check(
    writes.at(-1).titleLength === 100 &&
      writes.at(-1).descriptionLength === 5000 &&
      JSON.stringify(writes.at(-1).fields.sort()) ===
        '["description","title","visibility"]',
    'command trims title and sends only three form fields',
  );
  holdWriteBefore = false;
  release();
  await saved(title);
  const boundary = (await all())[0];
  check(
    boundary.title === title &&
      boundary.description === description &&
      boundary.visibility === 'public',
    'real server accepts exact Unicode title and description limits',
  );
  check(
    BigInt(boundary.id) > 9007199254740991n &&
      (await c.evaluate(
        `document.querySelector('.studio-table__playlist').getAttribute('href')==='/playlist?list=${boundary.id}'`,
      )),
    'created ID and open link retain exact int64',
  );
  check(
    await c.evaluate(
      `document.activeElement.classList.contains('studio-create') && !document.querySelector('#playlist-title').value && !document.querySelector('#playlist-description').value`,
    ),
    'confirmed creation closes, clears fields and restores focus',
  );

  const unsafe = marker + ' <img src=x onerror=window.studioInjection=true>',
    unlisted = await create(marker + ' ссылка', '', 'unlisted'),
    privateList = await create(
      unsafe,
      '<script>window.studioInjection=true</script>\nСтрока',
      'private',
    );
  check(
    (await ids()).length === 3 &&
      (await c.evaluate(
        `!document.querySelector('.studio-table img,.studio-table script') && !window.studioInjection`,
      )),
    'all three visibility modes and literal text appear in own table',
  );
  check(
    (await actorRequest('other', '/playlists/' + unlisted.id)).status === 200 &&
      (await actorRequest('other', '/playlists/' + privateList.id)).status ===
        404,
    'unlisted is directly readable while private is owner only',
  );
  const publicLists = await all(
    'author',
    '/channels/' + fixture.people.author.tag + '/playlists',
  );
  check(
    publicLists.length === 1 && publicLists[0].id === boundary.id,
    'own public channel reveals only public playlists',
  );

  await open();
  await fill(marker + ' retry', 'Сохранённый текст', 'private');
  writeFailure = true;
  await c.click(submit);
  await c.wait(
    `!!document.querySelector('.playlist-dialog [role=alert]') && !document.querySelector('${submit}').disabled`,
  );
  check(
    await c.evaluate(
      `document.querySelector('#playlist-title').value===${JSON.stringify(marker + ' retry')} && document.querySelector('#playlist-description').value==='Сохранённый текст' && document.querySelector('#playlist-visibility').value==='private' && !window.studioInjection`,
    ),
    '503 retains full retryable draft without executable error detail',
  );
  writeFailure = false;
  check((await all()).length === 3, 'failed creation did not insert a row');
  disconnect = true;
  await c.click(submit);
  await until(() => writes.at(-1).status === 0);
  await c.wait(
    `!!document.querySelector('.playlist-dialog [role=alert]') && !document.querySelector('${submit}').disabled`,
  );
  disconnect = false;
  check(
    (await all()).length === 3 &&
      (await c.evaluate(
        `document.querySelector('#playlist-title').value===${JSON.stringify(marker + ' retry')}`,
      )),
    'network refusal preserves draft without insertion',
  );
  dropCsrf = true;
  await c.click(submit);
  await until(() => writes.at(-1).status === 403);
  await c.wait(
    `document.querySelector('.playlist-dialog [role=alert]')?.textContent.includes('Обновите страницу') && !document.querySelector('${submit}').disabled`,
  );
  dropCsrf = false;
  check(
    (await all()).length === 3 &&
      (await c.evaluate(
        `document.querySelector('.playlist-dialog').open && !!document.querySelector('.studio-table')`,
      )),
    'real CSRF rejection leaves draft and confirmed list without insertion',
  );
  await fill('\ud800');
  await c.click(submit);
  await until(() => writes.at(-1).status === 422);
  await c.wait(
    `!!document.querySelector('.playlist-dialog [role=alert]') && !document.querySelector('${submit}').disabled`,
  );
  check(
    (await all()).length === 3,
    'real server validation preserves rejected draft and leaves no bad row',
  );
  await fill(marker + ' invalid mode', '', 'invalid');
  await c.click(submit);
  await until(() => writes.at(-1).status === 422);
  await c.wait(
    `document.activeElement.id==='playlist-visibility' && document.querySelector('#playlist-visibility').getAttribute('aria-invalid')==='true' && !document.querySelector('${submit}').disabled`,
  );
  check(
    (await all()).length === 3,
    'real server field validation is mapped and focused without insertion',
  );
  await fill(marker + ' retry', '', 'private');
  await c.click(submit);
  await saved(marker + ' retry');
  check(
    (await all()).length === 4,
    'explicit retry creates exactly one confirmed row',
  );

  for (let i = 0; i < 37; i++) {
    const r = await actorRequest('author', '/playlists', 'POST', {
      title: marker + ' page ' + i,
      description: '',
      visibility: ['public', 'private', 'unlisted'][i % 3],
    });
    assert.equal(r.status, 201);
  }
  await c.reload();
  await settled();
  check((await ids()).length === 20, 'own first page is twenty rows');
  nextFailure = true;
  await c.click('.studio-playlists__more button');
  await c.wait(
    `document.querySelector('.studio-playlists__more button')?.textContent.includes('Повторить')`,
  );
  check((await ids()).length === 20, 'next-page failure keeps confirmed rows');
  nextFailure = false;
  await c.click('.studio-playlists__more button');
  await settled();
  await c.wait(
    `document.querySelectorAll('.studio-table tbody tr').length===40`,
  );
  await c.evaluate(
    `document.querySelector('.studio-playlists__more').scrollIntoView({block:'center'})`,
  );
  await c.wait(
    `document.querySelectorAll('.studio-table tbody tr').length===41`,
  );
  const expected = await all();
  check(
    JSON.stringify(await ids()) === JSON.stringify(expected.map((x) => x.id)) &&
      new Set(await ids()).size === 41,
    'real cursor and scroll reach all three modes in server order without duplicates',
  );
  holdRead = true;
  await refetch();
  await until(() => held.size === 1);
  await create(marker + ' newest');
  release();
  await settled();
  check(
    (await ids()).length === 20 &&
      (await c.evaluate(
        `document.querySelector('.studio-table strong').textContent===${JSON.stringify(marker + ' newest')}`,
      )),
    'creation cancels old list read and restarts at first cursor with new row',
  );
  readFailure = true;
  await refetch();
  await c.wait(
    `document.querySelector('.studio-playlists .page-state h1')?.textContent==='Не удалось загрузить плейлисты'`,
  );
  check(
    !(await c.evaluate(`!!document.querySelector('.studio-table')`)),
    'background read error cannot masquerade as old successful table',
  );
  readFailure = false;
  await c.click('.studio-playlists .page-state button');
  await settled();
  check((await ids()).length === 20, 'list retry restores current first page');
  readFailure = true;
  await c.reload();
  await c.wait(
    `document.querySelector('.studio-playlists .page-state h1')?.textContent==='Не удалось загрузить плейлисты'`,
  );
  readFailure = false;
  await c.click('.studio-playlists .page-state button');
  await settled();
  check(true, 'initial owner read error recovers');
  readStatus = 403;
  readFailure = true;
  await refetch();
  await c.wait(
    `document.querySelector('.studio-playlists .page-state h1')?.textContent==='Плейлисты недоступны'`,
  );
  check(
    await c.evaluate(
      `!document.querySelector('.studio-table') && ${queryClient}.getQueryCache().findAll({queryKey:['studio-playlists']}).every(q=>!q.state.data)`,
    ),
    'owner list denial clears private snapshots and rows',
  );
  readFailure = false;
  readStatus = 503;
  await c.click('.studio-playlists .page-state button');
  await settled();
  check(
    (await ids()).length === 20,
    'denied list can recover after a fresh allowed read',
  );

  await c.click('.studio-table__playlist');
  await c.wait(`!!document.querySelector('.playlist-contents__header')`);
  check(
    await c.evaluate(
      `location.pathname==='/@${fixture.people.author.tag}/playlists' && !!new URLSearchParams(location.search).get('list')`,
    ),
    'own table opens created playlist through real direct resolver',
  );
  await navigate('/@' + fixture.people.author.tag);
  await c.wait(`!!document.querySelector('.masthead .studio-create')`);
  await c.click('.masthead .studio-create');
  await c.click('#studio-create-menu button');
  await c.wait(
    `location.pathname===${JSON.stringify(studio)} && document.querySelector('.playlist-dialog')?.open`,
  );
  check(
    await c.evaluate(
      `!new URLSearchParams(location.search).has('create') && document.querySelector('#playlist-title').value===''`,
    ),
    'public-header Create opens existing Studio form and consumes create URL once',
  );
  await key('Escape');
  await open();
  await fill(marker + ' local secret');
  await spa('/');
  await c.wait(`!document.querySelector('.playlist-dialog')`);
  check(
    await c.evaluate(
      `!JSON.stringify(${queryClient}.getMutationCache().getAll().map(m=>m.state)).includes(${JSON.stringify(marker + ' local secret')})`,
    ),
    'unsaved local draft never enters mutation cache',
  );
  await spa(studio);
  await settled();
  holdRead = true;
  await refetch();
  await until(() => held.size === 1);
  await spa('/');
  release();
  await pause(200);
  check(
    await c.evaluate(
      `location.pathname==='/' && !document.querySelector('.studio-table') && ${queryClient}.getQueryCache().findAll({queryKey:['studio-playlists']}).every(q=>!q.state.data)`,
    ),
    'late own list cannot restore private rows after leaving',
  );

  await navigate(studio);
  await settled();
  await open();
  await fill(marker + ' accepted old', 'секрет', 'private');
  holdWriteAfter = true;
  await c.click(submit);
  await until(() => held.size === 1 && writes.at(-1).status === 201);
  await spa('/');
  await login('other');
  await open();
  await fill(marker + ' next viewer');
  holdWriteAfter = false;
  release();
  await pause(250);
  check(
    await c.evaluate(
      `document.querySelector('.playlist-dialog').open && document.querySelector('#playlist-title').value===${JSON.stringify(marker + ' next viewer')} && !document.querySelector('.studio-playlists__notice') && !document.querySelector('.studio-table')`,
    ),
    'late accepted response cannot close another viewer modal or expose old table',
  );
  check(
    (await all('author')).some((x) => x.title === marker + ' accepted old') &&
      (await all('other')).length === 0,
    'cancelled response does not roll back committed old-owner creation or change next owner',
  );
  await api('/auth/logout', 'POST');
  await c.click(submit);
  await c.wait(
    `!!document.querySelector('.studio-playlists__login') && !document.querySelector('.playlist-dialog')`,
  );
  check(
    writes.at(-1).status === 401 && (await all('other')).length === 0,
    'real expired session closes draft and creates no unauthorized row',
  );

  await login('author');
  meFailure = true;
  await refetch('session');
  await c.wait(
    `document.querySelector('.studio-playlists .page-state h1')?.textContent==='Не удалось проверить аккаунт'`,
  );
  check(
    !(await c.evaluate(
      `!!document.querySelector('.studio-table') || !!document.querySelector('.studio-create')`,
    )),
    'identity refresh error hides personal table and creation',
  );
  meFailure = false;
  await c.click('.studio-playlists .page-state button');
  await settled();
  check(
    (await ids()).length > 0,
    'identity retry restores confirmed owner data',
  );
  await login('owner');
  const settingsBefore = await actorRequest(
    'owner',
    '/videos/' + v.public + '/settings',
  );
  await open();
  await fill(marker + ' выбор', '', 'private');
  await c.click(submit);
  await saved(marker + ' выбор');
  const ownerCreated = (await all('owner'))[0];
  const choices = await all('owner', '/videos/' + v.public + '/playlists');
  const settingsAfter = await actorRequest(
    'owner',
    '/videos/' + v.public + '/settings',
  );
  check(
    choices.some((x) => x.id === ownerCreated.id && !x.selected) &&
      ownerCreated.visibility === 'private',
    'new playlist is available as unselected choice for own video',
  );
  check(
    JSON.stringify(settingsBefore.data) === JSON.stringify(settingsAfter.data),
    'creating playlist leaves video settings and privacy unchanged',
  );
  await navigate('/studio/video/' + v.public + '/edit');
  await c.wait(
    `!!document.querySelector('.playlist-choose') && !document.querySelector('.playlist-choose').disabled`,
  );
  await c.click('.playlist-choose');
  await c.wait(
    `!!document.querySelector('.playlist-create .create-playlist button') && !document.querySelector('.playlist-create .create-playlist button').disabled`,
  );
  await c.click('.playlist-create .create-playlist button');
  await c.wait(`document.querySelector('.playlist-dialog')?.open`);
  await fill(marker + ' compact', '', 'unlisted');
  await c.click(submit);
  await c.wait(
    `!document.querySelector('.playlist-dialog').open && [...document.querySelectorAll('.playlist-choices label')].some(x=>x.textContent.includes(${JSON.stringify(marker + ' compact')}))`,
  );
  check(
    await c.evaluate(
      `[...document.querySelectorAll('.playlist-choices label')].find(x=>x.textContent.includes(${JSON.stringify(marker + ' compact')})).querySelector('input').checked===false`,
    ),
    'compact creator refreshes real video choices without selecting new playlist',
  );
  const compactSettings = await actorRequest(
    'owner',
    '/videos/' + v.public + '/settings',
  );
  check(
    JSON.stringify(settingsBefore.data) ===
      JSON.stringify(compactSettings.data),
    'compact creation leaves video metadata and privacy unchanged',
  );
  await navigate(studio);
  await settled();
  await logout();
  check(
    !(await c.evaluate(
      `!!document.querySelector('.studio-table') || !!document.querySelector('.studio-create') || !!document.querySelector('.playlist-dialog')`,
    )),
    'Studio logout removes all personal rows and creator controls',
  );

  await c.viewport(390, 844, true);
  await login('author');
  await open();
  await fill(marker + ' телефон');
  await c.evaluate(`document.querySelector('${submit}').focus()`);
  await key('Enter');
  await saved(marker + ' телефон');
  check(
    (await all())[0].title === marker + ' телефон',
    'phone keyboard creates confirmed playlist',
  );
  await c.click('.studio-create');
  await key('Escape');
  check(
    !(await c.evaluate(`!!document.querySelector('#studio-create-menu')`)),
    'Escape closes phone create menu',
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
      profile: 'studio-playlists',
      browser: firefox ? 'Firefox' : 'Chromium',
      assertions,
      status: 'pass',
    }),
  );
} catch (error) {
  console.error(
    JSON.stringify({
      profile: 'studio-playlists',
      assertions,
      writes: writes.slice(-4),
      path: await c.evaluate('location.pathname+location.search'),
      state: await c.evaluate(
        "document.querySelector('.studio-playlists .page-state h1')?.textContent",
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
