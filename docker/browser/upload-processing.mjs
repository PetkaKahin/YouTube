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
const v = fixture.videos;
const overrides = new Map(),
  calls = [],
  held = new Set(),
  sockets = new Set();
let refusal = 0,
  holding = false;
const release = () => {
  for (const resume of held) resume();
  held.clear();
};
const server = createServer(async (req, res) => {
  try {
    const match = /^\/api\/v1\/videos\/(\d+)\/processing$/.exec(req.url);
    const call = match ? { video: match[1], status: undefined } : undefined;
    if (call) calls.push(call);
    const fault = refusal,
      override = match && overrides.get(match[1]);
    const headers = new Headers();
    for (const key of ['cookie', 'content-type', 'x-csrftoken', 'range'])
      if (req.headers[key]) headers.set(key, req.headers[key]);
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    const response = await fetch(
      (req.url.startsWith('/api/')
        ? 'http://youtube-frontend-upload-gateway:8080'
        : 'http://node:5173') + req.url,
      {
        method: req.method,
        headers,
        body: chunks.length ? Buffer.concat(chunks) : undefined,
        redirect: 'manual',
      },
    );
    let result = Buffer.from(await response.arrayBuffer());
    const status = match && fault && response.ok ? fault : response.status;
    if (match && response.ok && override) {
      result = Buffer.from(
        JSON.stringify({ ...JSON.parse(result.toString()), ...override }),
      );
    }
    if (match && fault && response.ok)
      result = Buffer.from('{"code":"unavailable"}');
    if (match && holding) await new Promise((resolve) => held.add(resolve));
    if (call) call.status = status;
    res.statusCode = status;
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
          .map(([key, value]) => key + ': ' + value)
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
const c = firefox ? await firefoxBrowser() : await browser();
const base = 'http://127.0.0.1:4189';
const url = (id = v.silent) => '/studio/upload?video=' + id;
const queryClient =
  "Object.values(document.querySelector('#app').__vue_app__._context.provides).find(x=>typeof x?.getQueryCache==='function')";
let assertions = 0;
function check(value, message) {
  assert.ok(value, message);
  assertions++;
}
const safeErrors = () =>
  c.errors.map((x) => x.replace(/https?:\/\/\S+/g, '[URL redacted]'));
const rows = () =>
  c.evaluate(
    "document.querySelector('.upload-processing ul')?.textContent ?? ''",
  );
const label = () =>
  c.evaluate("document.querySelector('.upload-processing h4')?.textContent");
async function until(fn) {
  for (let i = 0; i < 200; i++) {
    if (await fn()) return;
    await pause(100);
  }
  throw new Error('Processing condition timed out');
}
function quality(quality, status, range = 'sdr') {
  return {
    quality,
    range,
    status,
    error_code: status === 'failed' ? 'ffmpeg_failed' : null,
  };
}
function state(processing, qualities = [], error_code = null, id = v.silent) {
  const available_qualities = qualities.filter((q) => q.status === 'ready');
  overrides.set(id, {
    upload: 'uploaded',
    processing,
    error_code,
    qualities,
    available_qualities,
    hd: available_qualities.some((q) => q.quality >= 720),
    uhd: available_qualities.some((q) => q.quality === 2160),
    hdr: available_qualities.some((q) => q.range === 'pq'),
    uhd_hdr: available_qualities.some(
      (q) => q.quality === 2160 && q.range === 'pq',
    ),
    thumbnail: { status: 'pending', error_code: null },
    subtitles: [],
  });
}
async function open(id = v.silent) {
  await c.navigate(base + url(id));
  await c.wait(
    "document.querySelector('.upload-dialog')?.open && !!document.querySelector('.upload-processing')",
  );
}
async function api(path, method = 'GET') {
  return JSON.parse(
    await c.evaluate(
      `(async()=>{const token=document.cookie.split('; ').find(x=>x.startsWith('csrftoken='));const r=await fetch('/api/v1'+${JSON.stringify(path)},{method:${JSON.stringify(method)},headers:token?{'x-csrftoken':decodeURIComponent(token.slice(10))}:{}});return JSON.stringify({status:r.status,data:r.status===204?null:await r.json()});})()`,
    ),
  );
}
async function login(person = 'owner') {
  await c.navigate(base + '/login?returnTo=' + encodeURIComponent(url()));
  await c.fill('#login-email', fixture.people[person].email);
  await c.fill('#login-password', 'abcdef');
  await c.click('.auth-card button[type=submit]');
}
async function key(key) {
  if (firefox) {
    await c.key(key === 'Enter' ? '\uE007' : '\uE00C');
    return;
  }
  await c.send('Input.dispatchKeyEvent', {
    type: 'keyDown',
    key,
    windowsVirtualKeyCode: key === 'Enter' ? 13 : 27,
    ...(key === 'Enter' ? { text: '\r' } : {}),
  });
  await c.send('Input.dispatchKeyEvent', {
    type: 'keyUp',
    key,
    windowsVirtualKeyCode: key === 'Enter' ? 13 : 27,
  });
}
try {
  await c.viewport(1280, 900);
  if (!firefox)
    await c.send('Emulation.setFocusEmulationEnabled', { enabled: true });
  await login();
  await c.wait("!!document.querySelector('.upload-watch-link')");
  const real = await api('/videos/' + v.silent + '/processing');
  check(
    real.status === 200 &&
      real.data.processing === 'ready' &&
      real.data.available_qualities.length > 0,
    'real silent source is ready through owner API',
  );
  check(
    (await label()) === 'Обработка завершена',
    'unmodified real ready response appears in interface',
  );
  check(
    await c.evaluate(
      "!document.querySelector('.upload-processing .form-error') && !document.querySelector('.upload-progress progress')",
    ),
    'absent audio/subtitles cause no processing error or percentage',
  );

  state('pending');
  holding = true;
  await open();
  await until(() => held.size > 0);
  check(
    (await label()) === 'Проверяем состояние обработки',
    'initial loading is distinct from server pending',
  );
  check(
    await c.evaluate(
      "!document.querySelector('.upload-watch-link') && document.querySelector('.upload-progress').textContent.includes('Загрузка видео завершена')",
    ),
    'accepted source stays separate from unknown processing readiness',
  );
  holding = false;
  release();
  await c.wait(
    "document.querySelector('.upload-processing h4').textContent==='Ожидаем обработку видео'",
  );
  check(
    !(await c.evaluate("!!document.querySelector('.upload-watch-link')")),
    'pending has no playable variant',
  );
  state('processing', [
    quality(360, 'pending'),
    quality(720, 'pending'),
    quality(2160, 'pending'),
  ]);
  await c.wait(
    "document.querySelector('.upload-processing ul')?.textContent.includes('Ожидает обработки')",
  );
  check(
    !(await rows()).includes('Обрабатывается'),
    'queued qualities are labelled waiting',
  );
  state('processing', [
    quality(360, 'processing'),
    quality(720, 'pending'),
    quality(2160, 'pending'),
  ]);
  await c.wait(
    "document.querySelector('.upload-processing ul')?.textContent.includes('360p — Обрабатывается')",
  );
  check(
    (await rows()).includes('720p — Ожидает обработки'),
    'running and queued qualities are distinct',
  );
  state('processing', [
    quality(360, 'ready'),
    quality(720, 'processing'),
    quality(2160, 'pending'),
  ]);
  await c.wait("!!document.querySelector('.upload-watch-link')");
  check(
    (await label()) === 'Видео обрабатывается' &&
      (await rows()).includes('360p — Готово'),
    'ready low quality enables watch while full ladder still processes',
  );
  check(
    await c.evaluate(
      "!document.querySelector('.upload-progress progress') && !document.querySelector('.upload-processing').textContent.includes('%')",
    ),
    'background stage never invents a numeric percentage',
  );
  state('processing', [
    quality(360, 'ready'),
    quality(720, 'ready'),
    quality(2160, 'processing'),
  ]);
  await c.wait(
    "document.querySelector('.upload-processing ul').textContent.includes('720p — Готово')",
  );
  check(
    (await label()) === 'Видео обрабатывается',
    'late HD does not mean unfinished 4K is complete',
  );
  state('ready', [
    quality(360, 'ready'),
    quality(720, 'ready'),
    quality(2160, 'ready'),
    quality(2160, 'ready', 'pq'),
  ]);
  await c.wait(
    "document.querySelector('.upload-processing ul').textContent.includes('2160p HDR — Готово')",
  );
  check(
    (await label()) === 'Обработка завершена',
    'late 4K/HDR uses terminal server readiness',
  );
  const readyCalls = calls.length;
  await pause(2300);
  check(
    calls.length === readyCalls,
    'terminal AV readiness stops polling even when thumbnail is pending',
  );

  state('partial', [
    quality(360, 'ready'),
    quality(720, 'failed'),
    quality(2160, 'failed'),
  ]);
  await open();
  await c.wait(
    "document.querySelector('.upload-processing h4').textContent==='Видео готово частично'",
  );
  check(
    await c.evaluate(
      "!!document.querySelector('.upload-watch-link') && document.querySelector('.upload-processing').textContent.includes('Часть вариантов не удалось подготовить')",
    ),
    'partial failure preserves playable low quality with explicit warning',
  );
  check(
    (await rows()).includes('360p — Готово') &&
      (await rows()).includes('720p — Не удалось подготовить') &&
      (await rows()).includes('2160p — Не удалось подготовить'),
    'partial outcome lists both ready and failed variants',
  );
  check(
    await c.evaluate(
      "document.querySelectorAll('.upload-processing button').length===0",
    ),
    'terminal processing has no unrequested retry job action',
  );
  const partialCalls = calls.length;
  await pause(2300);
  check(
    calls.length === partialCalls,
    'terminal partial failure does not run endless polling',
  );
  await c.reload();
  await c.wait(
    "document.querySelector('.upload-processing h4')?.textContent==='Видео готово частично'",
  );
  check(!!(await rows()), 'reload restores current server quality list');

  for (const [code, expected] of [
    ['duration_limit', 'превышает 60 минут'],
    ['source_size_changed', 'Размер принятого исходника не совпадает'],
    ['no_video', 'не содержит видеодорожки'],
    ['invalid_duration', 'Не удалось определить длительность'],
    ['invalid_media', 'Файл повреждён или его формат не поддерживается'],
  ]) {
    state('failed', [], code);
    await open();
    await c.wait(
      `document.querySelector('.upload-processing .form-error')?.textContent.includes(${JSON.stringify(expected)})`,
    );
    check(
      await c.evaluate(
        "!document.querySelector('.upload-watch-link') && document.querySelector('.upload-processing').textContent.includes('Просмотр недоступен')",
      ),
      'full source rejection ' +
        code +
        ' explains reason without offering watch',
    );
  }
  state('failed', [quality(360, 'failed'), quality(720, 'failed')]);
  await open();
  await c.wait(
    "document.querySelector('.upload-processing h4').textContent==='Не удалось обработать видео'",
  );
  check(
    !(await c.evaluate("!!document.querySelector('.upload-watch-link')")) &&
      (await rows()).includes('360p — Не удалось подготовить'),
    'failure of all qualities keeps their states and prevents watch',
  );
  state(
    'failed',
    [],
    '<img src=x onerror=window.__processingLeak=1> /run/secrets/private internal-job-123',
  );
  await open();
  await c.wait(
    "document.querySelector('.upload-processing h4').textContent==='Не удалось обработать видео'",
  );
  check(
    await c.evaluate(
      "!window.__processingLeak && !document.querySelector('.upload-dialog').textContent.includes('/run/secrets/') && !document.querySelector('.upload-dialog').textContent.includes('internal-job-123') && document.querySelector('.upload-processing').textContent.includes('Сервер не смог подготовить видео')",
    ),
    'unknown error code never exposes technical data or injects HTML',
  );

  state('processing', [quality(360, 'ready'), quality(720, 'processing')]);
  await open();
  await c.wait("!!document.querySelector('.upload-watch-link')");
  refusal = 503;
  await c.wait("!!document.querySelector('.upload-processing button')");
  check(
    (await label()) === 'Состояние обработки недоступно' &&
      (await rows()) === '',
    'background read failure hides stale quality claims',
  );
  check(
    !(await c.evaluate("!!document.querySelector('.upload-watch-link')")),
    'background read failure withdraws old watch link',
  );
  await c.click('.upload-dialog__buttons button[type=submit]');
  await c.wait("!!document.querySelector('.upload-result')");
  check(
    await c.evaluate(
      "document.querySelector('.upload-result').textContent.includes('Не удалось проверить готовность видео')",
    ),
    'saved publication does not claim readiness after processing read failure',
  );
  refusal = 0;
  holding = true;
  const retryCalls = calls.length;
  await c.evaluate(
    "document.querySelector('.upload-processing button').focus()",
  );
  await key('Enter');
  await until(() => held.size > 0);
  check(
    await c.evaluate(
      "document.querySelector('.upload-processing').getAttribute('aria-busy')==='true' && document.querySelector('.upload-processing button').disabled",
    ),
    'keyboard retry announces busy and disables repeated request',
  );
  await c.click('.upload-processing button');
  check(
    calls.length === retryCalls + 1,
    'disabled repeated retry does not duplicate request',
  );
  holding = false;
  release();
  await c.wait("!!document.querySelector('.upload-watch-link')");
  check(
    (await label()) === 'Видео обрабатывается' &&
      (await rows()).includes('360p — Готово'),
    'manual read retry restores actual partial progress and polling',
  );

  refusal = 404;
  await c.wait(
    "document.querySelector('.upload-dialog .page-state h1')?.textContent==='Видео недоступно'",
  );
  const deniedState = JSON.parse(
    await c.evaluate(
      `JSON.stringify({fields:!!document.querySelector('#video-title'),link:!!document.querySelector('.upload-watch-link'),queries:${queryClient}.getQueryCache().getAll().filter(q=>['upload-settings','upload-processing'].includes(q.queryKey[0])).map(q=>({kind:q.queryKey[0],hasData:q.state.data!==undefined}))})`,
    ),
  );
  check(
    !deniedState.fields &&
      !deniedState.link &&
      deniedState.queries.every((q) => !q.hasData),
    'loss of processing access clears private data: ' +
      JSON.stringify(deniedState),
  );
  refusal = 0;
  state('failed', [], 'duration_limit');
  holding = true;
  await open();
  await until(() => held.size > 0);
  holding = false;
  await c.evaluate(
    `(async()=>{await document.querySelector('#app').__vue_app__.config.globalProperties.$router.push(${JSON.stringify(url(v.private))});return true;})()`,
  );
  await c.wait("!!document.querySelector('.upload-watch-link')");
  release();
  check(
    (await label()) === 'Обработка завершена' &&
      (await c.evaluate(
        `new URLSearchParams(location.search).get('video')===${JSON.stringify(v.private)} && !document.querySelector('.upload-processing').textContent.includes('60 минут')`,
      )),
    'late processing response cannot replace another video state',
  );

  await c.viewport(390, 844, true);
  state('partial', [quality(360, 'ready'), quality(2160, 'failed')]);
  await open();
  await c.wait(
    "document.querySelector('.upload-processing h4').textContent==='Видео готово частично'",
  );
  check(
    await c.evaluate(
      "!!document.querySelector('.upload-watch-link') && document.querySelector('.upload-progress').getAttribute('role')==='status'",
    ),
    'phone has same partial readiness and status announcement',
  );
  await key('Escape');
  await c.wait("location.pathname==='/studio/content/videos'");
  check(
    await c.evaluate("!document.querySelector('.upload-dialog')"),
    'phone keyboard closes processing dialog',
  );

  state('processing', [quality(360, 'ready'), quality(720, 'pending')]);
  await open();
  await c.wait("!!document.querySelector('.upload-watch-link')");
  check(
    (await api('/auth/logout', 'POST')).status === 204,
    'real session can expire independently of processing observer',
  );
  await c.wait(
    "document.querySelector('.upload-page .page-state h1')?.textContent==='Войдите, чтобы добавить видео'",
  );
  check(
    calls.some((x) => x.status === 401) &&
      (await c.evaluate(
        `!document.querySelector('.upload-dialog') && ${queryClient}.getQueryCache().getAll().every(q=>!['upload-settings','upload-processing'].includes(q.queryKey[0]))`,
      )),
    'real processing 401 removes private form and queries',
  );
  await login('invited');
  await c.wait(
    "document.querySelector('.upload-dialog .page-state h1')?.textContent==='Видео недоступно'",
  );
  const denied = await api('/videos/' + v.silent + '/processing');
  check(
    denied.status === 404 && !JSON.stringify(denied.data).includes('qualities'),
    'actual non-owner API denies processing data',
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
      profile: 'upload-processing',
      browser: firefox ? 'Firefox' : 'Chromium',
      assertions,
      status: 'pass',
    }),
  );
} catch (error) {
  console.error(
    JSON.stringify({
      profile: 'upload-processing',
      assertions,
      state: await label(),
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
