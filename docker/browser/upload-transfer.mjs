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
if (!fixture?.videos?.silent) throw new Error('Upload fixture not ready');
const firefox = process.argv.includes('--firefox');
const host = 'http://youtube-frontend-upload-gateway:8080/api/v1';
const sources = {
  small: await readFile('/media/portrait.mp4'),
  large: await readFile('/media/multipart.mp4'),
  invalid: await readFile('/media/captions.vtt'),
};
const partSize = 16 * 1024 * 1024;
let selectedSource = 'small',
  throttle = false,
  failPart = 0,
  partStatus = 503,
  holdPart = 0,
  dropPart = false,
  falsePartAck = false,
  holdComplete = false,
  completeFailure = 0,
  falseComplete = false,
  processingFault = '',
  planFault = '',
  mutateStartSize = false,
  dropCsrf = false;
const held = new Set(),
  sockets = new Set(),
  calls = [],
  parts = [],
  plans = [],
  creates = [],
  bodies = new Map();
const hold = () => new Promise((resolve) => held.add(resolve));
function release() {
  for (const resolve of held) resolve();
  held.clear();
}
const server = createServer(async (req, res) => {
  try {
    if (req.url === '/fixture-source') {
      res
        .writeHead(200, {
          'content-type': 'application/octet-stream',
          'cache-control': 'no-store',
        })
        .end(sources[selectedSource]);
      return;
    }
    const api = req.url.startsWith('/api/'),
      storage = req.url.startsWith('/uploads/'),
      url = new URL(req.url, 'http://localhost'),
      create = req.url === '/api/v1/videos' && req.method === 'POST',
      start = /^\/api\/v1\/videos\/(\d+)\/upload$/.exec(url.pathname),
      control =
        /^\/api\/v1\/videos\/(\d+)\/upload\/(\d+)\/(refresh|complete)$/.exec(
          url.pathname,
        ),
      kind = storage
        ? 'part'
        : create
          ? 'create'
          : start
            ? 'start'
            : (control?.[3] ??
              (url.pathname.endsWith('/publish') ? 'publish' : 'other')),
      call = {
        kind,
        method: req.method,
        status: undefined,
        ...(start ? { video: start[1] } : {}),
        ...(control ? { video: control[1], upload: control[2] } : {}),
      };
    if (api || storage) calls.push(call);
    const number = storage
      ? Number(url.searchParams.get('partNumber'))
      : undefined;
    const chunks = [];
    for await (const chunk of req) {
      chunks.push(chunk);
      if (storage && throttle) await pause(12);
    }
    let body = chunks.length ? Buffer.concat(chunks) : undefined;
    let part;
    if (storage) {
      const expected = bodies.get('current'),
        slice = expected.subarray((number - 1) * partSize, number * partSize);
      part = {
        number,
        bytes: body?.length,
        correct: body?.equals(slice) ?? false,
        status: undefined,
      };
      parts.push(part);
      if (failPart === number) {
        // Make the first part durable before this controlled second-part failure.
        await until(() =>
          parts.some((x) => x.number === 1 && x.status === 200),
        );
        part.status = partStatus;
        call.status = partStatus;
        res.writeHead(partStatus, { 'cache-control': 'no-store' }).end();
        return;
      }
      if (dropPart) {
        part.status = 0;
        call.status = 0;
        res.destroy();
        return;
      }
      if (falsePartAck) {
        part.status = 200;
        call.status = 200;
        res.writeHead(200, { 'cache-control': 'no-store' }).end();
        return;
      }
    }
    if (kind === 'complete' && holdComplete) await hold();
    if (kind === 'complete' && completeFailure) {
      call.status = completeFailure;
      res
        .writeHead(completeFailure, {
          'content-type': 'application/problem+json',
          'cache-control': 'no-store',
        })
        .end('{"code":"unavailable"}');
      return;
    }
    if (start && mutateStartSize) body = Buffer.from('{"size":5000000001}');
    const headers = new Headers();
    for (const name of ['cookie', 'content-type', 'x-csrftoken', 'range'])
      if (req.headers[name]) headers.set(name, req.headers[name]);
    if (dropCsrf && ['start', 'refresh', 'complete'].includes(kind))
      headers.delete('x-csrftoken');
    const response = await fetch(
      api || storage
        ? 'http://youtube-frontend-upload-gateway:8080' + req.url
        : 'http://node:5173' + req.url,
      { method: req.method, headers, body, redirect: 'manual' },
    );
    call.status = response.status;
    if (part) part.status = response.status;
    let result = Buffer.from(await response.arrayBuffer());
    if (
      processingFault &&
      url.pathname.endsWith('/processing') &&
      response.ok
    ) {
      const state = JSON.parse(result.toString());
      Object.assign(state, {
        processing: 'failed',
        error_code: processingFault,
        qualities: [],
        available_qualities: [],
        hd: false,
        uhd: false,
        hdr: false,
        uhd_hdr: false,
      });
      result = Buffer.from(JSON.stringify(state));
    }
    if (create && response.ok)
      creates.push(
        JSON.parse(result.toString(), (key, value, context) =>
          key === 'id' && typeof value === 'number' ? context.source : value,
        ).id,
      );
    if (['start', 'refresh', 'complete'].includes(kind) && response.ok) {
      const raw = result.toString(),
        data = JSON.parse(raw, (key, value, context) =>
          key === 'id' && typeof value === 'number' ? context.source : value,
        );
      plans.push({
        kind,
        id: data.id,
        video: start?.[1] ?? control?.[1],
        numbers: data.parts.map((x) => x.number),
        sizes: data.parts.map((x) => x.size),
        status: data.status,
      });
      if (kind === 'complete' && falseComplete)
        result = Buffer.from(
          raw.replace(/"status":"uploaded"/, '"status":"uploading"'),
        );
      if (kind === 'start' && planFault) {
        if (planFault === 'foreign')
          result = Buffer.from(
            raw.replace(
              /"url":"[^"]+"/,
              '"url":"https://invalid.example/uploads/part"',
            ),
          );
        if (planFault === 'size')
          result = Buffer.from(
            raw.replace(/"size":\d+/, `"size":${data.size + 1}`),
          );
      }
    }
    if (storage && holdPart === number) await hold();
    res.statusCode = response.status;
    for (const name of ['content-type', 'location', 'cache-control'])
      if (response.headers.has(name))
        res.setHeader(name, response.headers.get(name));
    if (response.headers.getSetCookie().length)
      res.setHeader('set-cookie', response.headers.getSetCookie());
    res.end(result);
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
  jars = new Map(),
  marker = 'F23-' + crypto.randomUUID().slice(0, 8),
  submit = '.upload-dialog__buttons button[type=submit]';
let assertions = 0;
const safeErrors = () =>
  c.errors.map((error) => error.replace(/https?:\/\/\S+/g, '[URL redacted]'));
const check = (value, message) => {
  assert.ok(value, message);
  assertions++;
};
async function until(predicate) {
  for (let i = 0; i < 200; i++) {
    if (await predicate()) return;
    await pause(100);
  }
  throw new Error('Upload transfer condition timed out');
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
      `(async()=>{const cookie=document.cookie.split('; ').find(x=>x.startsWith('csrftoken='));const r=await fetch('/api/v1'+${JSON.stringify(path)},{method:${JSON.stringify(method)},headers:{...(cookie?{'x-csrftoken':decodeURIComponent(cookie.slice(10))}:{})}});return JSON.stringify({status:r.status,data:r.status===204?null:await r.json()});})()`,
    ),
  );
}
const count = (kind) => calls.filter((x) => x.kind === kind).length;
async function login(person = 'author') {
  await c.navigate(base + '/login?returnTo=' + encodeURIComponent(upload));
  await c.fill('#login-email', fixture.people[person].email);
  await c.fill('#login-password', 'abcdef');
  await c.click('.auth-card button[type=submit]');
  await c.wait(
    `document.querySelector('.upload-dialog')?.open && !!document.querySelector('.upload-drop')`,
  );
}
async function open() {
  await c.navigate(base + upload);
  await c.wait(
    `document.querySelector('.upload-dialog')?.open && !!document.querySelector('.upload-drop')`,
  );
}
async function prepare(kind = 'small', label = marker) {
  selectedSource = kind;
  bodies.set('current', sources[kind]);
  await c.evaluate(
    `(async()=>{const b=await(await fetch('/fixture-source')).arrayBuffer(),d=new DataTransfer();d.items.add(new File([b],${JSON.stringify(kind + '.unknown')},{type:'application/octet-stream'}));const input=document.querySelector('#video-file');input.files=d.files;input.dispatchEvent(new Event('change',{bubbles:true}));})()`,
  );
  await c.wait(`!!document.querySelector('#video-title')`);
  await c.fill('#video-title', label);
  await c.click(submit);
  await c.wait(`document.activeElement.id==='video-private'`);
  await c.click('#video-private');
}
async function save() {
  await c.click(submit);
}
async function error() {
  await c.wait(
    `!!document.querySelector('.upload-error') && !document.querySelector('${submit}').disabled`,
    30000,
  );
}
async function saved() {
  await c.wait(`!!document.querySelector('.upload-dialog__saved')`, 60000);
  return c.evaluate(`new URLSearchParams(location.search).get('video')`);
}
async function close() {
  await c.click('[aria-label="Закрыть загрузку видео"]');
  await c.wait(`location.pathname==='/studio/content/videos'`);
  release();
}
async function settings(id, person = 'author') {
  const r = await actorRequest(person, '/videos/' + id + '/settings');
  assert.equal(r.status, 200);
  return r.data;
}
async function ensureDraft(id) {
  const state = await settings(id);
  return state.publication === 'draft' && state.upload !== 'uploaded';
}
try {
  for (const person of ['author', 'other']) {
    const suffix = crypto.randomUUID().replaceAll('-', '').slice(0, 12);
    fixture.people[person] = {
      name: 'Передача Видео',
      tag: 'transfer_' + suffix,
      email: 'transfer-' + suffix + '@example.com',
    };
    assert.equal(
      (
        await actorRequest('owner', '/auth/register', 'POST', {
          ...fixture.people[person],
          password: 'abcdef',
          password_confirmation: 'abcdef',
        })
      ).status,
      201,
    );
  }
  await c.viewport(1280, 900);
  if (!firefox)
    await c.send('Emulation.setFocusEmulationEnabled', { enabled: true });
  await login();
  throttle = true;
  failPart = 2;
  await prepare('large');
  const createBefore = creates.length,
    completeBefore = count('complete');
  await save();
  await c.wait(
    `document.querySelector('.upload-progress progress')?.value>0 && document.querySelector('.upload-progress progress').value<document.querySelector('.upload-progress progress').max`,
    30000,
  );
  const measured = JSON.parse(
    await c.evaluate(
      `JSON.stringify({value:document.querySelector('.upload-progress progress').value,max:document.querySelector('.upload-progress progress').max,text:document.querySelector('.upload-progress').textContent})`,
    ),
  );
  check(
    measured.max === sources.large.length &&
      measured.value > 0 &&
      measured.value < measured.max &&
      measured.text.includes(
        Math.floor((measured.value / measured.max) * 100) + '%',
      ),
    'slow transfer shows actual byte progress with correct denominator',
  );
  await error();
  throttle = false;
  const largeId = creates.at(-1);
  await until(() => parts.some((x) => x.number === 1 && x.status === 200));
  check(
    creates.length === createBefore + 1 &&
      count('complete') === completeBefore &&
      (await ensureDraft(largeId)),
    'one failed real multipart part leaves draft without completion or publication',
  );
  check(
    parts.slice(-2).every((x) => x.correct) &&
      parts.some((x) => x.number === 1 && x.bytes === partSize) &&
      parts.some(
        (x) => x.number === 2 && x.bytes === sources.large.length - partSize,
      ),
    'File.slice sent exact two real source boundaries',
  );
  check(
    await c.evaluate(
      `!document.querySelector('.upload-dialog__saved') && !document.querySelector('.upload-watch-link') && document.querySelector('.upload-summary__body p').textContent.includes('large.unknown')`,
    ),
    'failure retains same source and offers no false readiness',
  );
  const beforeRetryParts = parts.length,
    beforeRetryCreates = creates.length;
  failPart = 0;
  holdComplete = true;
  completeFailure = 503;
  await save();
  await until(() => held.size === 1);
  const refreshed = plans.filter((x) => x.kind === 'refresh').at(-1);
  check(
    refreshed.video === largeId &&
      JSON.stringify(refreshed.numbers) === '[2]' &&
      creates.length === beforeRetryCreates,
    'retry keeps draft/upload context and server returns only missing part',
  );
  check(
    parts.length === beforeRetryParts + 1 &&
      parts.at(-1).number === 2 &&
      parts.at(-1).correct &&
      parts.at(-1).status === 200,
    'retry transfers only missing bytes without resending accepted part',
  );
  check(
    await c.evaluate(
      `document.querySelector('.upload-progress').textContent.includes('Подтверждаем загрузку') && !document.querySelector('.upload-dialog__saved') && !document.querySelector('.upload-watch-link')`,
    ),
    'all bytes sent waits for complete and never claims ready',
  );
  holdComplete = false;
  release();
  await error();
  check(
    (await ensureDraft(largeId)) && count('publish') === 0,
    'complete failure does not publish or claim accepted source',
  );
  holdComplete = true;
  const noMoreParts = parts.length;
  await save();
  await until(() => held.size === 1);
  check(
    plans.filter((x) => x.kind === 'refresh').at(-1).numbers.length === 0 &&
      parts.length === noMoreParts,
    'repeat completion uses empty missing-parts plan without duplicate transfer',
  );
  const ids = calls
    .filter(
      (x) => ['refresh', 'complete'].includes(x.kind) && x.video === largeId,
    )
    .map((x) => x.upload);
  check(new Set(ids).size === 1, 'all retries use the same upload identifier');
  await close();
  holdComplete = false;
  completeFailure = 0;

  await open();
  await prepare();
  holdPart = 1;
  await save();
  await until(() => held.size === 1 && parts.at(-1).status === 200);
  const smallId = creates.at(-1);
  await c.wait(
    `document.querySelector('.upload-progress progress')?.value===document.querySelector('.upload-progress progress')?.max`,
  );
  check(
    await c.evaluate(
      `document.querySelector('.upload-progress progress')?.value===document.querySelector('.upload-progress progress')?.max && !document.querySelector('.upload-dialog__saved')`,
    ),
    'client 100 percent while PUT response waits is not server acceptance',
  );
  holdComplete = true;
  holdPart = 0;
  release();
  await until(() => held.size === 1);
  check(
    await ensureDraft(smallId),
    'before complete server still reports unaccepted draft',
  );
  holdComplete = false;
  release();
  await saved();
  check(
    (await settings(smallId)).upload === 'uploaded' &&
      (await settings(smallId)).publication === 'published',
    'real complete and publish eventually confirm uploaded video',
  );

  await open();
  await prepare();
  falsePartAck = true;
  await save();
  await error();
  const falseId = creates.at(-1);
  check(
    calls.filter((x) => x.kind === 'complete').at(-1).status === 409 &&
      (await ensureDraft(falseId)),
    'false PUT success cannot bypass real server check for missing part',
  );
  check(
    await c.evaluate(
      `!document.querySelector('.upload-dialog__saved') && !document.querySelector('.upload-watch-link') && document.querySelector('.upload-progress').textContent.includes('не подтверждён')`,
    ),
    'false complete path shows unconfirmed error rather than video ready',
  );
  falsePartAck = false;
  const falseRetry = parts.length;
  await save();
  await saved();
  check(
    parts.length === falseRetry + 1 && creates.at(-1) === falseId,
    'manual retry of missing part completes same draft exactly once',
  );

  await open();
  await prepare();
  dropPart = true;
  await save();
  await error();
  const brokenId = creates.at(-1);
  dropPart = false;
  check(
    await ensureDraft(brokenId),
    'network disconnect before storage leaves unaccepted draft',
  );
  await save();
  await saved();
  check(creates.at(-1) === brokenId, 'network retry keeps original draft');

  await open();
  await prepare();
  falseComplete = true;
  const beforeFalseComplete = count('publish');
  await save();
  await error();
  const uncertainId = creates.at(-1);
  check(
    (await settings(uncertainId)).upload === 'uploaded' &&
      count('publish') === beforeFalseComplete &&
      (await c.evaluate(`!document.querySelector('.upload-dialog__saved')`)),
    'unexpected complete status cannot trigger publication despite successful HTTP',
  );
  falseComplete = false;
  const beforeConfirmedRetry = parts.length;
  await save();
  await saved();
  check(
    creates.at(-1) === uncertainId && parts.length === beforeConfirmedRetry,
    'retry rechecks confirmed source without duplicate draft or parts',
  );

  for (const fault of ['foreign', 'size']) {
    await open();
    await prepare();
    planFault = fault;
    const before = parts.length;
    await save();
    await error();
    check(
      parts.length === before &&
        !(await c.evaluate(
          `performance.getEntriesByType('resource').some(x=>x.name.startsWith('https://invalid.example/'))`,
        )),
      'invalid plan ' + fault + ' sends neither bytes nor foreign request',
    );
    planFault = '';
    await close();
  }
  await open();
  await prepare();
  mutateStartSize = true;
  const beforeSize = parts.length;
  await save();
  await error();
  mutateStartSize = false;
  check(
    calls.filter((x) => x.kind === 'start').at(-1).status === 422 &&
      parts.length === beforeSize &&
      (await ensureDraft(creates.at(-1))),
    'real API rejects oversized declared transfer without part requests',
  );
  await close();

  await open();
  await prepare();
  dropCsrf = true;
  await save();
  await error();
  dropCsrf = false;
  check(
    calls.filter((x) => x.kind === 'start').at(-1).status === 403 &&
      (await ensureDraft(creates.at(-1))),
    'real CSRF refusal preserves draft and source without transfer',
  );
  await save();
  await saved();
  check(true, 'CSRF recovery can complete same selected source');
  await open();
  await prepare();
  holdPart = 1;
  await save();
  await until(() => held.size === 1);
  const leavingId = creates.at(-1),
    beforeLeaveComplete = count('complete');
  await close();
  holdPart = 0;
  await pause(200);
  check(
    count('complete') === beforeLeaveComplete &&
      (await ensureDraft(leavingId)) &&
      (await c.evaluate(`!document.querySelector('.upload-dialog')`)),
    'closing during transfer aborts client and ignores late part without completing draft',
  );
  await open();
  await prepare();
  holdComplete = true;
  await save();
  await until(() => held.size === 1);
  await api('/auth/logout', 'POST');
  holdComplete = false;
  release();
  await c.wait(
    `document.querySelector('.upload-page .page-state h1')?.textContent==='Войдите, чтобы добавить видео'`,
  );
  check(
    calls.filter((x) => x.kind === 'complete').at(-1).status === 401,
    'session expiry at complete clears private transfer UI',
  );

  await login();
  await prepare('invalid', marker + ' bad content');
  await save();
  const invalidId = await saved();
  await c.wait(
    `document.querySelector('.upload-progress').textContent.includes('Не удалось обработать видео') || document.querySelector('.upload-result')?.textContent.includes('ошибкой')`,
    60000,
  );
  const invalidState = await actorRequest(
    'author',
    '/videos/' + invalidId + '/processing',
  );
  check(
    invalidState.data.processing === 'failed' &&
      invalidState.data.available_qualities.length === 0 &&
      !(await c.evaluate(`!!document.querySelector('.upload-watch-link')`)),
    'server content rejection after byte acceptance never offers playable video',
  );
  check(
    (await actorRequest('author', '/videos/' + invalidId)).status === 404,
    'accepted non-video source remains unavailable through ordinary watch API',
  );

  await open();
  await prepare();
  processingFault = 'duration_limit';
  await save();
  await saved();
  await c.wait(
    `document.querySelector('.upload-progress').textContent.includes('Не удалось обработать видео')`,
  );
  check(
    await c.evaluate(
      `!document.querySelector('.upload-watch-link') && document.querySelector('.upload-result').textContent.includes('просмотр недоступен') && !document.querySelector('.upload-progress progress')`,
    ),
    'duration-limit rejection DTO shows processing failure rather than transfer or ready',
  );
  processingFault = '';

  await c.viewport(390, 844, true);
  await open();
  await prepare();
  holdComplete = true;
  await save();
  await until(() => held.size === 1);
  check(
    await c.evaluate(
      `document.querySelector('.upload-progress').textContent.includes('Подтверждаем загрузку')`,
    ),
    'phone viewport has the same unconfirmed transfer state',
  );
  holdComplete = false;
  release();
  await saved();
  check(
    true,
    'phone successful transfer can finish without false progress stages',
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
      profile: 'upload-transfer',
      browser: firefox ? 'Firefox' : 'Chromium',
      assertions,
      status: 'pass',
    }),
  );
} catch (error) {
  console.error(
    JSON.stringify({
      profile: 'upload-transfer',
      assertions,
      parts: parts.slice(-4),
      calls: calls.slice(-6),
      path: await c.evaluate('location.pathname+location.search'),
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
