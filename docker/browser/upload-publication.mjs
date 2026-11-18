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
if (!fixture?.videos?.silent) throw Error('Upload fixture not ready');
const firefox = process.argv.includes('--firefox'),
  v = fixture.videos;
const host = 'http://youtube-frontend-upload-gateway:8080/api/v1';
const source = await readFile('/media/portrait.mp4');
let refusal = 0,
  dropCsrf = false,
  tamper = '',
  holdBefore = false,
  holdAfter = false,
  disconnectAfter = false,
  pendingProcessing = false;
const held = new Set(),
  sockets = new Set(),
  calls = [],
  creates = [],
  jars = new Map();
const hold = () => new Promise((resolve) => held.add(resolve));
const release = () => {
  for (const resume of held) resume();
  held.clear();
};
const parse = (raw) =>
  JSON.parse(raw, (key, value, context) =>
    ['id', 'owner_id'].includes(key) && typeof value === 'number'
      ? context.source
      : value,
  );
const server = createServer(async (req, res) => {
  try {
    if (req.url === '/fixture-source') {
      res
        .writeHead(200, { 'content-type': 'application/octet-stream' })
        .end(source);
      return;
    }
    const storage = req.url.startsWith('/uploads/'),
      api = req.url.startsWith('/api/');
    const publish = /^\/api\/v1\/videos\/(\d+)\/publish$/.exec(req.url);
    const kind = storage
      ? 'part'
      : req.url === '/api/v1/videos' && req.method === 'POST'
        ? 'create'
        : publish
          ? 'publish'
          : req.url.endsWith('/complete')
            ? 'complete'
            : 'other';
    const call = {
      kind,
      method: req.method,
      status: undefined,
      ...(publish ? { id: publish[1] } : {}),
    };
    if (api || storage) calls.push(call);
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    const headers = new Headers();
    for (const key of ['cookie', 'content-type', 'x-csrftoken', 'range'])
      if (req.headers[key]) headers.set(key, req.headers[key]);
    if (publish) {
      const snapshot = await fetch(
        host + '/videos/' + publish[1] + '/settings',
        { headers },
      );
      if (snapshot.ok) {
        const before = parse(await snapshot.text());
        call.beforeAdded = before.added_at;
        call.beforeCreated = before.created_at;
      }
    }
    if (publish && dropCsrf) headers.delete('x-csrftoken');
    if (publish && holdBefore) await hold();
    if (publish && refusal) {
      call.status = refusal;
      res
        .writeHead(refusal, { 'content-type': 'application/problem+json' })
        .end('{"code":"unavailable"}');
      return;
    }
    const response = await fetch(
      (api || storage
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
    let result = Buffer.from(await response.arrayBuffer());
    if (kind === 'create' && response.ok)
      creates.push(parse(result.toString()));
    if (publish && response.ok) {
      const value = parse(result.toString());
      call.confirmed = value.publication;
      call.created = value.created_at;
      if (holdAfter) await hold();
      if (disconnectAfter) {
        res.destroy();
        return;
      }
      if (tamper === 'publication')
        result = Buffer.from(
          result
            .toString()
            .replace(/"publication":"published"/, '"publication":"draft"'),
        );
      if (tamper === 'upload')
        result = Buffer.from(
          result
            .toString()
            .replace(/"upload":"uploaded"/, '"upload":"uploading"'),
        );
      if (tamper === 'owner')
        result = Buffer.from(
          result.toString().replace(/"owner_id":\d+/, '"owner_id":1'),
        );
    }
    if (pendingProcessing && req.url.endsWith('/processing') && response.ok) {
      const value = parse(result.toString());
      Object.assign(value, {
        processing: 'pending',
        qualities: [],
        available_qualities: [],
        error_code: null,
        hd: false,
        uhd: false,
        hdr: false,
        uhd_hdr: false,
      });
      result = Buffer.from(JSON.stringify(value));
    }
    res.statusCode = response.status;
    for (const key of ['content-type', 'cache-control', 'location'])
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
  base = 'http://127.0.0.1:4189';
const marker = 'F25-' + crypto.randomUUID().slice(0, 8),
  submit = '.upload-dialog__buttons button[type=submit]';
let assertions = 0;
const check = (value, message) => {
  assert.ok(value, message);
  assertions++;
};
const safeErrors = () =>
  c.errors.map((x) => x.replace(/https?:\/\/\S+/g, '[URL redacted]'));
const count = (kind) => calls.filter((x) => x.kind === kind).length;
async function until(fn) {
  for (let i = 0; i < 600; i++) {
    if (await fn()) return;
    await pause(100);
  }
  throw Error('Publication condition timed out');
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

async function api(path, method = 'GET') {
  return JSON.parse(
    await c.evaluate(
      `(async()=>{const token=document.cookie.split('; ').find(x=>x.startsWith('csrftoken='));const r=await fetch('/api/v1'+${JSON.stringify(path)},{method:${JSON.stringify(method)},headers:token?{'x-csrftoken':decodeURIComponent(token.slice(10))}:{}});return JSON.stringify({status:r.status,data:r.status===204?null:await r.json()});})()`,
    ),
  );
}
async function guest(path) {
  const r = await fetch(host + path);
  const raw = await r.text();
  return { status: r.status, data: raw ? parse(raw) : null };
}
async function settings(id) {
  const r = await actorRequest('owner', '/videos/' + id + '/settings');
  assert.equal(r.status, 200);
  return r.data;
}
async function ready(id) {
  await until(
    async () =>
      (await actorRequest('owner', '/videos/' + id + '/processing')).data
        .available_qualities.length > 0,
  );
}
async function open(id) {
  await c.navigate(base + '/studio/upload' + (id ? '?video=' + id : ''));
  await c.wait(
    "document.querySelector('.upload-dialog')?.open && (!!document.querySelector('.upload-drop') || !!document.querySelector('#video-title'))",
  );
}
async function login(person = 'owner') {
  await c.navigate(
    base + '/login?returnTo=' + encodeURIComponent('/studio/upload'),
  );
  await c.fill('#login-email', fixture.people[person].email);
  await c.fill('#login-password', 'abcdef');
  await c.click('.auth-card button[type=submit]');
  await c.wait("!!document.querySelector('.upload-drop')");
}
async function prepare(visibility = 'public') {
  await c.evaluate(
    `(async()=>{const b=await(await fetch('/fixture-source')).arrayBuffer(),d=new DataTransfer();d.items.add(new File([b],'ready.unknown',{type:'application/octet-stream'}));const input=document.querySelector('#video-file');input.files=d.files;input.dispatchEvent(new Event('change',{bubbles:true}));})()`,
  );
  await c.wait("!!document.querySelector('#video-title')");
  await c.fill('#video-title', marker + ' ' + creates.length);
  await c.fill(
    '#video-description',
    'Публикация <img src=x onerror=window.publicationInjection=1>',
  );
  await c.click(submit);
  await c.wait("document.activeElement.id==='video-private'");
  await c.click('#video-' + visibility);
}
async function save() {
  await c.click(submit);
}
async function saved() {
  await c.wait("!!document.querySelector('.upload-dialog__saved')", 60000);
  return c.evaluate("new URLSearchParams(location.search).get('video')");
}
async function error() {
  await c.wait(
    `!!document.querySelector('.upload-error') && !document.querySelector('${submit}').disabled`,
    30000,
  );
}
async function close() {
  await c.click('[aria-label="Закрыть загрузку видео"]');
  await c.wait("location.pathname==='/studio/content/videos'");
  release();
}
async function fresh(visibility = 'public') {
  await open();
  await prepare(visibility);
}
try {
  await c.viewport(1280, 900);
  if (!firefox)
    await c.send('Emulation.setFocusEmulationEnabled', { enabled: true });
  await login();
  pendingProcessing = true;
  await prepare();
  await save();
  const first = await saved(),
    original = creates.at(-1);
  check(
    first === original.id && original.publication === 'draft',
    'exact draft ID survives confirmed publication',
  );
  const firstState = await settings(first);
  check(
    firstState.publication === 'published' &&
      firstState.upload === 'uploaded' &&
      firstState.visibility === 'public',
    'real server confirms accepted public publication',
  );
  check(
    firstState.created_at === original.created_at &&
      !!firstState.added_at &&
      firstState.added_at ===
        calls.find((x) => x.kind === 'publish' && x.id === first).beforeAdded,
    'publication preserves creation and addition dates',
  );
  await c.wait(
    "document.querySelector('.upload-result')?.textContent.includes('Ожидаем готовый вариант')",
  );
  check(
    !(await c.evaluate("!!document.querySelector('.upload-watch-link')")),
    'publication under pending processing does not promise playable stream',
  );
  pendingProcessing = false;
  await ready(first);
  await c.wait("!!document.querySelector('.upload-watch-link')");
  check(
    (await guest('/videos/' + first)).status === 200,
    'public ready publication is viewable by guest',
  );
  check(
    await c.evaluate(
      "document.querySelector('.upload-watch-link').getAttribute('href')==='/watch?v='+new URLSearchParams(location.search).get('video') && !window.publicationInjection",
    ),
    'watch link uses exact ID and metadata remains text',
  );
  await close();
  await c.wait(
    `Array.from(document.querySelectorAll('tr')).some(row=>row.textContent.includes(${JSON.stringify(marker)}) && !!row.querySelector('time'))`,
  );
  check(
    await c.evaluate(
      `Array.from(document.querySelectorAll('tr')).some(row=>row.textContent.includes(${JSON.stringify(marker)})&&!!row.querySelector('time[datetime='+${JSON.stringify(JSON.stringify(original.created_at))}+']'))`,
    ),
    'Studio publication row uses original creation date',
  );
  await open(first);
  check(
    await c.evaluate("!!document.querySelector('.upload-dialog__saved')"),
    'reload restores confirmed publication',
  );
  await c.fill('#video-description', 'Исправленное описание');
  await c.wait("!document.querySelector('.upload-dialog__saved')");
  check(true, 'editing restored metadata clears old saved confirmation');
  await c.click(submit);
  await c.wait(`!!document.querySelector('${submit}')`);
  await save();
  await saved();
  check(
    (await settings(first)).description === 'Исправленное описание' &&
      creates.length === 1,
    'edited published metadata can save without a second draft',
  );

  await fresh('private');
  holdBefore = true;
  const pubBefore = count('publish'),
    partsBefore = count('part');
  await save();
  await until(() => held.size > 0);
  const privateId = creates.at(-1).id;
  await ready(privateId);
  check(
    (await settings(privateId)).publication === 'draft' &&
      (await actorRequest('owner', '/videos/' + privateId)).status === 404,
    'ready source remains unviewable until draft publication',
  );
  check(
    await c.evaluate(
      "document.querySelector('.upload-progress').textContent.includes('Подтверждаем публикацию') && !document.querySelector('.upload-dialog__saved') && !document.querySelector('.upload-watch-link')",
    ),
    'publication pending is explicit and cannot appear saved early',
  );
  await c.evaluate(
    `(()=>{const f=document.querySelector('.upload-dialog form');for(let i=0;i<4;i++)f.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));})()`,
  );
  check(
    count('publish') === pubBefore + 1 && count('part') === partsBefore + 1,
    'repeated submit during publication produces one transfer and publish request',
  );
  holdBefore = false;
  release();
  await saved();
  await c.wait("!!document.querySelector('.upload-watch-link')");
  const privateState = await settings(privateId);
  check(
    privateState.publication === 'published' &&
      privateState.visibility === 'private' &&
      (await actorRequest('owner', '/videos/' + privateId)).status === 200,
    'late private publication keeps access setting and owner watch',
  );
  check(
    (await guest('/videos/' + privateId)).status === 404 &&
      (await actorRequest('invited', '/videos/' + privateId)).status === 404,
    'private publication never grants guest or uninvited account watch',
  );
  const feed = await guest('/videos/feed'),
    search = await guest(
      '/search?q=' + encodeURIComponent(marker) + '&type=video',
    );
  check(
    feed.status === 200 &&
      !JSON.stringify(feed.data).includes(privateId) &&
      search.status === 200 &&
      !JSON.stringify(search.data).includes(privateId),
    'private publication is absent from public feed and search',
  );

  await fresh();
  refusal = 503;
  await save();
  await error();
  const failedId = creates.at(-1).id;
  check(
    (await settings(failedId)).publication === 'draft' &&
      !(await c.evaluate(
        "!!document.querySelector('.upload-dialog__saved') || !!document.querySelector('.upload-watch-link')",
      )),
    'failed publish leaves real draft without success or watch',
  );
  const retryCounts = {
    creates: creates.length,
    parts: count('part'),
    complete: count('complete'),
  };
  refusal = 0;
  await save();
  await saved();
  check(
    creates.length === retryCounts.creates &&
      count('part') === retryCounts.parts &&
      count('complete') === retryCounts.complete &&
      creates.at(-1).id === failedId,
    'retry publication skips accepted file and preserves one draft',
  );

  for (const fault of ['publication', 'upload', 'owner']) {
    await fresh();
    tamper = fault;
    await save();
    await error();
    const id = creates.at(-1).id;
    check(
      (await settings(id)).publication === 'published' &&
        !(await c.evaluate(
          "!!document.querySelector('.upload-dialog__saved') || !!document.querySelector('.upload-watch-link')",
        )),
      'unexpected publish response ' + fault + ' cannot confirm client success',
    );
    tamper = '';
    const before = count('part');
    await save();
    await saved();
    check(
      creates.at(-1).id === id && count('part') === before,
      'reconfirm ' + fault + ' after committed result keeps same file and ID',
    );
  }
  await fresh();
  disconnectAfter = true;
  await save();
  await error();
  const uncertain = creates.at(-1).id;
  const committed = await settings(uncertain);
  check(
    committed.publication === 'published' &&
      !(await c.evaluate("!!document.querySelector('.upload-dialog__saved')")),
    'lost response after real commit remains unconfirmed in client',
  );
  disconnectAfter = false;
  await save();
  await saved();
  const repeated = await settings(uncertain);
  check(
    repeated.created_at === committed.created_at &&
      repeated.added_at === committed.added_at &&
      repeated.updated_at === committed.updated_at,
    'idempotent publish retry preserves all publication dates',
  );

  await fresh();
  dropCsrf = true;
  await save();
  await error();
  const csrfId = creates.at(-1).id;
  check(
    calls.filter((x) => x.kind === 'publish').at(-1).status === 403 &&
      (await settings(csrfId)).publication === 'draft',
    'real CSRF refusal does not publish or discard allowed draft',
  );
  dropCsrf = false;
  await save();
  await saved();
  check(creates.at(-1).id === csrfId, 'CSRF recovery confirms original draft');

  await open(v.hidden);
  await c.click(submit);
  await c.wait(
    "document.querySelector('.upload-result')?.textContent.includes('Видео скрыто')",
  );
  check(
    !(await c.evaluate("!!document.querySelector('.upload-watch-link')")) &&
      (await actorRequest('owner', '/videos/' + v.hidden)).status === 404,
    'actual hidden publication stays unavailable even to owner',
  );
  const beforeHidden = await settings(v.hidden);
  await actorRequest('owner', '/videos/' + v.hidden + '/publish', 'POST');
  check(
    (await settings(v.hidden)).moderation === 'hidden' &&
      (await settings(v.hidden)).added_at === beforeHidden.added_at,
    'real publish retry preserves moderation and original addition date',
  );

  await fresh();
  holdAfter = true;
  await save();
  await until(() => held.size > 0);
  const late = creates.at(-1).id;
  await close();
  holdAfter = false;
  await open();
  check(
    (await settings(late)).publication === 'published' &&
      (await c.evaluate(
        "!!document.querySelector('.upload-drop') && !document.querySelector('.upload-dialog__saved') && !location.search",
      )),
    'late committed publish response cannot fill a new form or redirect it',
  );

  await c.viewport(390, 844, true);
  await prepare('private');
  holdBefore = true;
  await save();
  await until(() => held.size > 0);
  check(
    await c.evaluate(
      "document.querySelector('.upload-progress').textContent.includes('Подтверждаем публикацию') && document.querySelector('.upload-progress').getAttribute('role')==='status'",
    ),
    'phone announces pending publication',
  );
  holdBefore = false;
  release();
  await saved();
  check(
    (await settings(creates.at(-1).id)).visibility === 'private',
    'phone completes explicit private publication',
  );

  await fresh();
  holdBefore = true;
  await save();
  await until(() => held.size > 0);
  const expired = creates.at(-1).id;
  await api('/auth/logout', 'POST');
  holdBefore = false;
  release();
  await c.wait(
    "document.querySelector('.upload-page .page-state h1')?.textContent==='Войдите, чтобы добавить видео'",
  );
  check(
    calls.filter((x) => x.kind === 'publish').at(-1).status === 401 &&
      (await settings(expired)).publication === 'draft',
    'real session loss at publish clears form without forbidden publication',
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
      profile: 'upload-publication',
      browser: firefox ? 'Firefox' : 'Chromium',
      assertions,
      status: 'pass',
    }),
  );
} catch (error) {
  console.error(
    JSON.stringify({
      profile: 'upload-publication',
      assertions,
      path: await c.evaluate('location.pathname'),
      state: await c.evaluate(
        "document.querySelector('.upload-progress')?.textContent",
      ),
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
