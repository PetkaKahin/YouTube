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
const source = await readFile('/media/portrait.mp4');
let holdMe = false,
  meFailure = false,
  createFailure = 0,
  settingsFailure = false,
  dropCsrf = false,
  holdCreateBefore = false,
  holdCreateAfter = false,
  holdSettingsId,
  stopTransfer = true;
const held = new Set(),
  pendingTransfer = new Set(),
  sockets = new Set(),
  calls = [],
  creates = [];
const hold = (set) => new Promise((resolve) => set.add(resolve));
function release(set = held) {
  for (const resolve of set) resolve();
  set.clear();
}
const server = createServer(async (req, res) => {
  try {
    if (req.url === '/fixture-source') {
      res
        .writeHead(200, {
          'content-type': 'application/octet-stream',
          'cache-control': 'no-store',
        })
        .end(source);
      return;
    }
    const api = req.url.startsWith('/api/'),
      storage = req.url.startsWith('/uploads/'),
      url = new URL(req.url, 'http://localhost'),
      create = req.url === '/api/v1/videos' && req.method === 'POST',
      start =
        /^\/api\/v1\/videos\/\d+\/upload$/.test(url.pathname) &&
        req.method === 'POST',
      settings = /^\/api\/v1\/videos\/(\d+)\/settings$/.exec(url.pathname)?.[1],
      me = req.url === '/api/v1/auth/me',
      call = {
        path: storage ? 'part' : req.url,
        method: req.method,
        status: undefined,
      };
    if (api || storage) calls.push(call);
    if (me && holdMe) await hold(held);
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    let entry;
    if (create) {
      const body = JSON.parse(Buffer.concat(chunks).toString());
      entry = {
        fields: Object.keys(body),
        titleLength: [...body.title].length,
        descriptionLength: [...body.description].length,
        visibility: body.visibility,
        status: undefined,
      };
      creates.push(entry);
      if (holdCreateBefore) await hold(held);
    }
    if (
      (me && meFailure) ||
      (settings && settingsFailure) ||
      (create && createFailure)
    ) {
      const status = create ? createFailure : 503;
      call.status = status;
      if (entry) entry.status = status;
      res
        .writeHead(status, {
          'content-type': 'application/problem+json',
          'cache-control': 'no-store',
        })
        .end(
          JSON.stringify(
            status === 422
              ? {
                  code: 'validation_error',
                  errors: [
                    {
                      location: ['body', 'description'],
                      code: 'invalid_length',
                    },
                  ],
                }
              : {
                  code: 'unavailable',
                  detail:
                    '<img src=x onerror=window.uploadStartInjection=true>',
                },
          ),
        );
      return;
    }
    if (start && stopTransfer) {
      await hold(pendingTransfer);
      res
        .writeHead(503, {
          'content-type': 'application/problem+json',
          'cache-control': 'no-store',
        })
        .end('{"code":"unavailable"}');
      return;
    }
    const headers = new Headers();
    for (const name of ['cookie', 'content-type', 'x-csrftoken', 'range'])
      if (req.headers[name]) headers.set(name, req.headers[name]);
    if (create && dropCsrf) headers.delete('x-csrftoken');
    const response = await fetch(
      api || storage
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
    const body = Buffer.from(await response.arrayBuffer());
    if (entry) {
      entry.status = response.status;
      if (response.ok)
        entry.id = JSON.parse(body.toString(), (key, value, context) =>
          key === 'id' && typeof value === 'number' ? context.source : value,
        ).id;
    }
    if (create && holdCreateAfter) await hold(held);
    if (settings && settings === holdSettingsId) {
      holdSettingsId = undefined;
      await hold(held);
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
  upload = '/studio/upload',
  queryClient =
    "Object.values(document.querySelector('#app').__vue_app__._context.provides).find(x=>typeof x?.getQueryCache==='function')",
  jars = new Map(),
  marker = 'F22-' + crypto.randomUUID().slice(0, 8),
  saveButton = '.upload-dialog__buttons button[type=submit]';
let assertions = 0;
const check = (value, message) => {
  assert.ok(value, message);
  assertions++;
};
async function until(predicate) {
  for (let i = 0; i < 150; i++) {
    if (await predicate()) return;
    await pause(100);
  }
  throw new Error('Upload-start condition timed out');
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
async function navigate(path = upload) {
  await c.navigate(base + path);
}
async function spa(path) {
  await c.evaluate(
    `document.querySelector('#app').__vue_app__.config.globalProperties.$router.push(${JSON.stringify(path)})`,
  );
  await c.wait(`location.pathname===${JSON.stringify(path.split('?')[0])}`);
}
async function login(person = 'author', to = upload) {
  await navigate('/login?returnTo=' + encodeURIComponent(to));
  await submitLogin(person);
}
async function submitLogin(person = 'author') {
  await c.fill('#login-email', fixture.people[person].email);
  await c.fill('#login-password', 'abcdef');
  await c.click('.auth-card button[type=submit]');
  await c.wait(
    `location.pathname==='/studio/upload' && document.querySelector('.upload-dialog')?.open`,
  );
}
async function open() {
  await navigate();
  await c.wait(
    `document.querySelector('.upload-dialog')?.open && !!document.querySelector('.upload-drop')`,
  );
}
async function select(
  name = 'Исходник.unknown',
  { size, count = 1, drop = false, empty = false } = {},
) {
  await c.evaluate(
    `(async()=>{const bytes=await(await fetch('/fixture-source')).arrayBuffer(),d=new DataTransfer();for(let i=0;i<${count};i++){const f=new File([${empty ? 'new Uint8Array()' : 'bytes'}],${JSON.stringify(name)},{type:'application/octet-stream'});${size === undefined ? '' : `Object.defineProperty(f,'size',{value:${size}});`}d.items.add(f);}const input=document.querySelector('#video-file');${drop ? "document.querySelector('.upload-drop').dispatchEvent(new DragEvent('drop',{dataTransfer:d,bubbles:true,cancelable:true}));" : "input.files=d.files;input.dispatchEvent(new Event('change',{bubbles:true}));"}})()`,
  );
}
async function metadata(title = marker, description = '') {
  await c.fill('#video-title', title);
  await c.fill('#video-description', description);
}
async function next() {
  await c.click('.upload-dialog__buttons button[type=submit]');
  await c.wait(
    `document.querySelector('.upload-steps [aria-current]')?.textContent.includes('Доступ')`,
  );
}
async function visibility(value = 'private') {
  await c.click('#video-' + value);
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
async function close() {
  await c.click('[aria-label="Закрыть загрузку видео"]');
  await c.wait(
    `location.pathname==='/studio/content/videos' && !document.querySelector('.upload-dialog')`,
  );
  release(pendingTransfer);
}
async function drafts(person = 'author') {
  const result = [];
  let cursor;
  do {
    const r = await actorRequest(
      person,
      '/videos/mine?publication=draft' +
        (cursor ? '&cursor=' + encodeURIComponent(cursor) : ''),
    );
    assert.equal(r.status, 200);
    result.push(...r.data.items);
    cursor = r.data.next_cursor;
  } while (cursor);
  return result;
}
async function draftCreated() {
  await until(() => pendingTransfer.size === 1);
  return creates.at(-1).id;
}
try {
  for (const person of ['author', 'other']) {
    const suffix = crypto.randomUUID().replaceAll('-', '').slice(0, 12);
    fixture.people[person] = {
      name: 'Автор Загрузки',
      tag: 'upload_' + suffix,
      email: 'upload-' + suffix + '@example.com',
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
  await navigate();
  await until(() => held.size === 1);
  check(
    !(await c.evaluate(
      `!!document.querySelector('.upload-dialog') || !!document.querySelector('#video-file')`,
    )),
    'file form waits for confirmed identity',
  );
  holdMe = false;
  release();
  await c.wait(
    `document.querySelector('.upload-page .page-state h1')?.textContent==='Войдите, чтобы добавить видео'`,
  );
  check(
    creates.length === 0 &&
      !calls.some((x) => x.path.startsWith('/api/v1/videos/')),
    'guest does not create or read private video state',
  );
  await c.click('.upload-page .page-state .button');
  await c.wait(`location.pathname==='/login'`);
  check(
    await c.evaluate(
      `new URLSearchParams(location.search).get('returnTo')==='/studio/upload'`,
    ),
    'guest login preserves upload destination',
  );
  await submitLogin();
  check(
    await c.evaluate(
      `!document.querySelector('#video-file').accept && !document.querySelector('.upload-dialog__buttons button[type=submit]') && document.querySelector('.upload-drop__limits').textContent.includes('60 минут') && document.querySelector('.upload-drop__limits').textContent.includes('без звука') && document.querySelector('.upload-drop__limits').textContent.includes('ниже 360p')`,
    ),
    'empty picker has no extension filter or premature save and explains server limits',
  );
  await select('empty', { empty: true });
  check(
    await c.evaluate(
      `!!document.querySelector('.upload-drop .form-error') && !document.querySelector('#video-title')`,
    ),
    'zero-byte file is rejected before metadata or draft',
  );
  await select('large', { size: 5000000001 });
  check(
    await c.evaluate(
      `document.querySelector('.upload-drop .form-error').textContent.includes('5 ГБ')`,
    ),
    'size over five billion bytes is rejected locally',
  );
  await select('two', { count: 2, drop: true });
  check(
    await c.evaluate(
      `document.querySelector('.upload-drop .form-error').textContent.includes('один видеофайл')`,
    ),
    'dropping multiple files does not silently pick the first',
  );
  await select('Без расширения', { size: 5000000000 });
  await c.wait(`!!document.querySelector('#video-title')`);
  check(
    await c.evaluate(
      `document.querySelector('#video-title').value==='Без расширения' && document.querySelector('.upload-summary__body p').textContent.trim()==='Без расширения'`,
    ),
    'exact size limit and extensionless name reach metadata without transfer',
  );
  check(
    creates.length === 0,
    'choosing source is still local and creates no server object',
  );
  await close();
  await open();
  const longName = 'я'.repeat(101) + '.strange';
  await select(longName, { drop: true });
  await c.wait(`!!document.querySelector('#video-title')`);
  check(
    await c.evaluate(
      `document.querySelector('#video-title').value===${JSON.stringify('я'.repeat(101))} && document.querySelector('#video-title-hint').textContent==='101/100'`,
    ),
    'long filename prefills whole title without silent truncation',
  );
  await select('замена.odd');
  check(
    await c.evaluate(
      `document.querySelector('#video-title').value==='замена' && document.querySelector('.upload-summary__body p').textContent.trim()==='замена.odd'`,
    ),
    'source can be changed before a draft exists',
  );
  const beforeInvalid = creates.length;
  for (const [title, description, target] of [
    ['   ', '', 'video-title'],
    ['😀'.repeat(101), '', 'video-title'],
    ['Текст', 'я'.repeat(5001), 'video-description'],
    ['\ud800', '', 'video-title'],
  ]) {
    await metadata(title, description);
    await c.evaluate(`document.querySelector('.upload-form').requestSubmit()`);
    await c.wait(
      `document.activeElement.id==='${target}' && document.querySelector('#${target}').getAttribute('aria-invalid')==='true'`,
    );
    check(true, 'invalid text stays in form and focuses explained field');
  }
  check(
    creates.length === beforeInvalid,
    'invalid metadata does not create any draft',
  );
  const title = '😀'.repeat(100),
    description = 'я'.repeat(5000);
  await metadata(' ' + title + ' ', description);
  await next();
  check(
    await c.evaluate(
      `!document.querySelector('#video-public').checked && !document.querySelector('#video-private').checked && document.querySelectorAll('#video-visibility input').length===2 && !document.querySelector('input[value=unlisted]')`,
    ),
    'access step requires explicit public or private choice',
  );
  await c.click(saveButton);
  await c.wait(
    `document.querySelector('#video-visibility-error')?.textContent.includes('Выберите') && document.activeElement.id==='video-private'`,
  );
  check(
    creates.length === beforeInvalid,
    'missing visibility blocks create and focuses access',
  );
  await visibility('public');
  await c.click('.upload-dialog__buttons button:first-child');
  await c.wait(
    `document.querySelector('.upload-steps [aria-current]')?.textContent.includes('Информация')`,
  );
  check(
    await c.evaluate(
      `document.querySelector('#video-title').value===${JSON.stringify(' ' + title + ' ')} && document.querySelector('#video-description').value.length===5000`,
    ),
    'Back preserves title and full description',
  );
  await next();
  createFailure = 503;
  await c.click(saveButton);
  await c.wait(
    `!!document.querySelector('.upload-error') && !document.querySelector('${saveButton}').disabled`,
  );
  check(
    (await drafts()).length === 0 &&
      !(await c.evaluate(
        `!!document.querySelector('.upload-dialog__saved') || !!window.uploadStartInjection`,
      )),
    'failed create leaves no draft or publication claim',
  );
  check(
    await c.evaluate(
      `document.querySelector('#video-title').value===${JSON.stringify(' ' + title + ' ')} && document.querySelector('#video-description').value.length===5000 && document.querySelector('.upload-summary__body p').textContent.includes('замена.odd')`,
    ),
    'failed create preserves metadata, visibility and same File',
  );
  createFailure = 422;
  await c.click(saveButton);
  await c.wait(
    `document.activeElement.id==='video-description' && document.querySelector('#video-description').getAttribute('aria-invalid')==='true' && !document.querySelector('#video-description').disabled`,
  );
  check(
    true,
    'server field error unlocks information step and focuses its input',
  );
  createFailure = 0;
  await metadata(' ' + title + ' ', description);
  await next();
  dropCsrf = true;
  await c.click(saveButton);
  await until(() => creates.at(-1).status === 403);
  await c.wait(
    `document.querySelector('.upload-error')?.textContent.includes('Обновите страницу') && !document.querySelector('${saveButton}').disabled`,
  );
  dropCsrf = false;
  check(
    (await drafts()).length === 0 &&
      (await c.evaluate(
        `document.querySelector('.upload-dialog').open && document.querySelector('#video-title').value.length===202`,
      )),
    'real CSRF rejection keeps draft local without closing allowed form',
  );
  holdCreateBefore = true;
  const beforeDouble = creates.length;
  await c.evaluate(
    `const f=document.querySelector('.upload-form');for(let i=0;i<4;i++)f.requestSubmit()`,
  );
  await until(() => held.size === 1);
  check(
    creates.length === beforeDouble + 1 &&
      (await c.evaluate(
        `document.querySelector('#video-title').disabled && document.querySelector('.upload-progress').textContent.includes('Сохраняем сведения') && !document.querySelector('.upload-dialog__saved')`,
      )),
    'rapid save sends one creation and shows saving metadata without publication success',
  );
  holdCreateBefore = false;
  release();
  const first = await draftCreated();
  const firstSettings = await actorRequest(
    'author',
    '/videos/' + first + '/settings',
  );
  check(
    firstSettings.status === 200 &&
      firstSettings.data.title === title &&
      firstSettings.data.description === description &&
      firstSettings.data.visibility === 'public' &&
      firstSettings.data.publication === 'draft',
    'server stores trimmed Unicode boundary metadata and explicit public as draft',
  );
  check(
    BigInt(first) > 9007199254740991n &&
      (await c.evaluate(
        `new URLSearchParams(location.search).get('video')==='${first}' && !document.querySelector('.upload-watch-link') && !document.querySelector('.upload-dialog__saved')`,
      )),
    'created exact ID enters URL without pretending draft is published',
  );
  check(
    (await actorRequest('other', '/videos/' + first + '/settings')).status ===
      404 && (await actorRequest('other', '/videos/' + first)).status === 404,
    'public visibility draft is still private to its owner and unwatchable',
  );
  check(
    !creates
      .at(-1)
      .fields.some(
        (x) => !['title', 'description', 'visibility'].includes(x),
      ) &&
      creates.at(-1).titleLength === 100 &&
      creates.at(-1).descriptionLength === 5000,
    'creation command contains only metadata fields with validated lengths',
  );
  await close();
  check(
    await c.evaluate(
      `new URLSearchParams(location.search).get('publication')==='draft'`,
    ),
    'close after creation returns to draft catalogue',
  );

  await open();
  const unsafe =
    marker + ' <img src=x onerror=window.uploadStartInjection=true>';
  await select('source');
  await metadata(unsafe, '<script>window.uploadStartInjection=true</script>');
  await next();
  await visibility('private');
  await c.click(saveButton);
  const second = await draftCreated();
  const secondSettings = await actorRequest(
    'author',
    '/videos/' + second + '/settings',
  );
  check(
    secondSettings.data.visibility === 'private' &&
      secondSettings.data.publication === 'draft' &&
      !(await c.evaluate(
        `!!document.querySelector('.upload-dialog h2 img') || !!window.uploadStartInjection`,
      )),
    'private draft stores text literally without executing heading HTML',
  );
  await close();
  holdSettingsId = first;
  await navigate(upload + '?video=' + first);
  await until(() => held.size === 1);
  await spa(upload + '?video=' + second);
  await c.wait(
    `document.querySelector('.upload-dialog .page-state h1')?.textContent==='Передача файла не завершена'`,
  );
  release();
  await pause(200);
  check(
    await c.evaluate(
      `new URLSearchParams(location.search).get('video')==='${second}' && !document.querySelector('#video-file')`,
    ),
    'late old settings cannot hydrate another draft after URL switch',
  );
  settingsFailure = true;
  await navigate(upload + '?video=' + second);
  await c.wait(
    `document.querySelector('.upload-dialog .page-state h1')?.textContent==='Не удалось загрузить сведения'`,
  );
  check(
    !(await c.evaluate(`!!document.querySelector('#video-title')`)),
    'settings read error does not leave old form',
  );
  settingsFailure = false;
  await c.click('.upload-dialog .page-state button');
  await c.wait(
    `document.querySelector('.upload-dialog .page-state h1')?.textContent==='Передача файла не завершена'`,
  );
  check(
    true,
    'restored unaccepted draft requires new source instead of unsafe automatic resume',
  );
  await c.click('.upload-dialog .page-state .button');
  await c.wait(`!!document.querySelector('.upload-drop')`);
  await select('new');
  check(
    await c.evaluate(
      `!new URLSearchParams(location.search).has('video') && document.querySelector('#video-title').value==='new'`,
    ),
    'new upload after File loss uses a separate local form',
  );
  await close();

  await open();
  await select('old viewer');
  await metadata(marker + ' accepted');
  await next();
  await visibility('private');
  holdCreateAfter = true;
  await c.click(saveButton);
  await until(() => held.size === 1 && creates.at(-1).status === 201);
  const acceptedId = creates.at(-1).id;
  await spa('/');
  await login('other');
  await select('new viewer');
  await metadata(marker + ' retained');
  holdCreateAfter = false;
  release();
  await pause(200);
  check(
    await c.evaluate(
      `document.querySelector('#video-title').value===${JSON.stringify(marker + ' retained')} && !new URLSearchParams(location.search).has('video') && !document.querySelector('.upload-error')`,
    ),
    'late accepted create cannot clear or redirect new account form',
  );
  check(
    (await actorRequest('author', '/videos/' + acceptedId + '/settings'))
      .status === 200 &&
      (await actorRequest('other', '/videos/' + acceptedId + '/settings'))
        .status === 404,
    'cancelled response leaves real old-owner draft without granting next user access',
  );
  await next();
  await visibility('public');
  await api('/auth/logout', 'POST');
  await c.click(saveButton);
  await c.wait(
    `document.querySelector('.upload-page .page-state h1')?.textContent==='Войдите, чтобы добавить видео'`,
  );
  check(
    creates.at(-1).status === 401 && (await drafts('other')).length === 0,
    'expired session removes File and form without creating unauthorized draft',
  );
  await login();
  meFailure = true;
  await c.evaluate(
    `void ${queryClient}.refetchQueries({queryKey:['session']})`,
  );
  await c.wait(
    `document.querySelector('.upload-page .page-state h1')?.textContent==='Не удалось проверить аккаунт'`,
  );
  check(
    !(await c.evaluate(`!!document.querySelector('.upload-dialog')`)),
    'identity failure closes upload and stops showing personal metadata',
  );
  meFailure = false;
  await c.click('.upload-page .page-state button');
  await c.wait(`!!document.querySelector('.upload-drop')`);
  check(true, 'identity retry starts clean confirmed form');
  for (const value of ['0', first + '&video=' + second]) {
    const beforeBad = calls.length;
    await navigate(upload + '?video=' + value);
    await c.wait(
      `document.querySelector('.upload-dialog .page-state h1')?.textContent==='Видео недоступно'`,
    );
    check(
      !calls.slice(beforeBad).some((x) => /\/settings$/.test(x.path)),
      'invalid or repeated restore ID is rejected before settings read',
    );
  }
  await login('other', upload + '?video=' + first);
  await c.wait(
    `document.querySelector('.upload-dialog .page-state h1')?.textContent==='Видео недоступно'`,
  );
  check(
    !(await c.evaluate(
      `!!document.querySelector('#video-title') || !!document.querySelector('#video-file')`,
    )),
    'another owner cannot restore draft fields',
  );

  await c.viewport(390, 844, true);
  await login();
  await select('phone');
  await metadata(marker + ' phone');
  await c.evaluate(
    `document.querySelector('.upload-dialog__buttons button[type=submit]').focus()`,
  );
  await key('Enter');
  await c.wait(`document.activeElement.id==='video-private'`);
  await key('Tab');
  check(
    await c.evaluate(`document.activeElement.closest('dialog')?.open===true`),
    'phone keyboard advances into explicit access choice and keeps focus in dialog',
  );
  await key('Escape');
  await c.wait(`location.pathname==='/studio/content/videos'`);
  check(
    await c.evaluate(
      `document.activeElement.classList.contains('studio-create')`,
    ),
    'Escape closes local upload and returns focus to Create',
  );

  stopTransfer = false;
  await open();
  await select('Готовый исходник.unknown');
  await metadata(marker + ' complete');
  await next();
  await visibility('private');
  await c.click(saveButton);
  await c.wait(`!!document.querySelector('.upload-dialog__saved')`, 60000);
  const completeId = await c.evaluate(
    `new URLSearchParams(location.search).get('video')`,
  );
  const completed = await actorRequest(
    'author',
    '/videos/' + completeId + '/settings',
  );
  check(
    completed.data.publication === 'published' &&
      completed.data.upload === 'uploaded',
    'successful full transfer and publication still work with new draft guards',
  );
  await c.wait(`!!document.querySelector('.upload-watch-link')`, 60000);
  check(
    (await actorRequest('author', '/videos/' + completeId)).status === 200,
    'actual processed downloaded source becomes viewable only after server readiness',
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
      profile: 'upload-start',
      browser: firefox ? 'Firefox' : 'Chromium',
      assertions,
      status: 'pass',
    }),
  );
} catch (error) {
  console.error(
    JSON.stringify({
      profile: 'upload-start',
      assertions,
      creates: creates.slice(-3),
      path: await c.evaluate('location.pathname+location.search'),
      state: await c.evaluate(
        "document.querySelector('.upload-dialog .page-state h1,.upload-page .page-state h1')?.textContent",
      ),
      errors: c.errors,
    }),
  );
  throw error;
} finally {
  release();
  release(pendingTransfer);
  await c.close();
  for (const socket of sockets) socket.destroy();
  server.closeAllConnections();
  await new Promise((resolve) => server.close(resolve));
}
