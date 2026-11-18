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
if (!fixture?.videos?.silent) throw new Error('Comment fixture not ready');
const firefox = process.argv.includes('--firefox'),
  v = {
    ...fixture.videos,
    public: firefox ? fixture.videos.silent : fixture.videos.public,
    other: firefox ? fixture.videos.public : fixture.videos.silent,
  },
  marker = 'F16-' + crypto.randomUUID().slice(0, 8),
  host = 'http://youtube-frontend-upload-gateway:8080/api/v1';
let readFailure = false,
  nextFailure = false,
  postFailure = false,
  dropCsrf = false,
  holdReadSort,
  holdPostBefore = false,
  holdPostAfter = false;
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
      url = new URL(req.url, 'http://localhost'),
      comments = /^\/api\/v1\/videos\/\d+\/comments$/.test(url.pathname),
      list = comments && req.method === 'GET',
      post = comments && req.method === 'POST',
      call = { path: req.url, method: req.method, status: undefined };
    if (api) calls.push(call);
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    let entry;
    if (post) {
      const body = JSON.parse(Buffer.concat(chunks).toString());
      entry = {
        path: req.url,
        fields: Object.keys(body),
        length: [...body.text].length,
        status: undefined,
      };
      writes.push(entry);
      if (holdPostBefore) await hold();
    }
    if (
      (list &&
        (readFailure || (nextFailure && url.searchParams.has('cursor')))) ||
      (post && postFailure)
    ) {
      call.status = 503;
      if (entry) entry.status = 503;
      res
        .writeHead(503, {
          'content-type': 'application/problem+json',
          'cache-control': 'no-store',
        })
        .end(
          JSON.stringify({
            code: 'unavailable',
            detail: '<img src=x onerror=window.commentInjection=true>',
          }),
        );
      return;
    }
    const headers = new Headers();
    for (const name of ['cookie', 'content-type', 'x-csrftoken', 'range'])
      if (req.headers[name]) headers.set(name, req.headers[name]);
    if (post && dropCsrf) headers.delete('x-csrftoken');
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
    call.status = response.status;
    if (entry) entry.status = response.status;
    if (list && holdReadSort === (url.searchParams.get('sort') ?? 'newest')) {
      holdReadSort = undefined;
      await hold();
    }
    if (post && holdPostAfter) await hold();
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
  submit = '.discussion__form button[type=submit]';
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
  throw new Error('Comment condition timed out');
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
async function visibility(value, id = v.public) {
  const r = await actorRequest('owner', '/videos/' + id + '/settings', 'PUT', {
    title: 'Проверка комментариев',
    description: 'Описание',
    visibility: value,
  });
  assert.equal(r.status, 200);
}
async function navigate(id = v.public) {
  await c.navigate(base + '/watch?v=' + id);
  await c.wait(
    `!!document.querySelector('.discussion__guest') || !!document.querySelector('#new-comment') || !!document.querySelector('.discussion [role=alert]')`,
  );
}
async function spa(path) {
  await c.evaluate(
    `document.querySelector('#app').__vue_app__.config.globalProperties.$router.push(${JSON.stringify(path)})`,
  );
}
async function login(person = 'owner', id = v.public) {
  await c.navigate(
    base + '/login?returnTo=' + encodeURIComponent('/watch?v=' + id),
  );
  await submitLogin(person, id);
}
async function submitLogin(person = 'owner', id = v.public) {
  await c.fill('#login-email', fixture.people[person].email);
  await c.fill('#login-password', 'abcdef');
  await c.click('.auth-card button[type=submit]');
  await c.wait(
    `location.search==='?v=${id}' && !!document.querySelector('${id === v.private ? '.watch-page__private-discussion' : '#new-comment'}') && !!document.querySelector('.avatar-button')`,
  );
}
async function logout() {
  await c.click('.avatar-button');
  await c.click('#logout-button');
  await c.wait(
    `!document.querySelector('.avatar-button') && !!document.querySelector('.discussion__guest')`,
  );
}
async function fill(value) {
  await c.fill('#new-comment', value);
  await c.evaluate(`document.querySelector('#new-comment').focus()`);
  await c.wait(`!!document.querySelector('${submit}')`);
}
async function posted(value) {
  await c.wait(
    `document.querySelector('.discussion__notice')?.textContent.includes('опубликован') && document.querySelector('#new-comment')?.value==='' && !document.querySelector('#new-comment').disabled`,
  );
  await c.wait(
    `[...document.querySelectorAll('.comment__text')].some(x=>x.textContent===${JSON.stringify(value)})`,
  );
}
async function send(value) {
  await fill(value);
  await c.click(submit);
  await posted(value);
}
async function uiIds() {
  return JSON.parse(
    await c.evaluate(
      `JSON.stringify([...document.querySelectorAll('.comment')].map(x=>x.dataset.commentId))`,
    ),
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
async function choose(sort) {
  await c.click('.discussion__sort > button');
  await c.evaluate(
    `[...document.querySelectorAll('.discussion__menu button')].find(x=>x.textContent.includes('${sort === 'popular' ? 'популярные' : 'новые'}')).click()`,
  );
}
async function refetch() {
  await c.evaluate(
    `void ${queryClient}.refetchQueries({queryKey:['comments'],type:'active'})`,
  );
}
async function noOldQuery() {
  return c.evaluate(
    `${queryClient}.getQueryCache().findAll({queryKey:['comments']}).every(q=>q.queryKey[2]!=='${v.public}')`,
  );
}
async function key(value) {
  if (firefox) {
    await c.key(
      { Enter: '\uE007', Escape: '\uE00C', ArrowDown: '\uE015' }[value],
    );
    return;
  }
  const code = { Enter: 13, Escape: 27, ArrowDown: 40 }[value];
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
  await visibility('public');
  await visibility('public', v.private);
  await c.viewport(1280, 900);
  if (!firefox)
    await c.send('Emulation.setFocusEmulationEnabled', { enabled: true });
  await navigate(v.private);
  await c.wait(`!!document.querySelector('.discussion__empty')`);
  check(
    !(await c.evaluate(`!!document.querySelector('#new-comment')`)),
    'empty public discussion is readable by a guest without a composer',
  );
  await visibility('private', v.private);
  await navigate();
  await c.click('.discussion__guest');
  await c.wait(`location.pathname==='/login'`);
  check(
    await c.evaluate(
      `new URLSearchParams(location.search).get('returnTo')==='/watch?v=${v.public}'`,
    ),
    'guest sign-in preserves exact return URL',
  );
  const initialWrites = writes.length;
  await submitLogin();
  check(
    writes.length === initialWrites,
    'sign-in publishes no comment automatically',
  );

  for (const value of ['', ' \n\t ', '\0', 'я'.repeat(2001)]) {
    await fill(value);
    check(
      await c.evaluate(`document.querySelector('${submit}').disabled`),
      'invalid or oversized input cannot submit',
    );
  }
  check(
    await c.evaluate(
      `document.querySelector('#new-comment').value.length===2001 && document.querySelector('.discussion__form-actions span').textContent==='2001/2000'`,
    ),
    'oversized input is preserved with an explicit counter',
  );
  const boundary = marker + '😀'.repeat(2000 - marker.length);
  await send(boundary);
  check(
    (await allComments())[0].text === boundary,
    '2000 Unicode characters are saved without truncation',
  );
  await c.click('.comment__expand');
  check(
    await c.evaluate(
      `document.querySelector('.comment__expand').getAttribute('aria-expanded')==='true'`,
    ),
    'long comment can expand',
  );
  await c.click('.comment__expand');
  check(
    await c.evaluate(
      `document.querySelector('.comment__expand').getAttribute('aria-expanded')==='false'`,
    ),
    'expanded comment can collapse',
  );

  const unsafe =
    marker +
    " <img src=x onerror=window.commentInjection=true> <script>window.commentInjection=true</script> ' OR 1=1;--";
  await send(unsafe);
  check(
    await c.evaluate(
      `document.querySelector('.comment__text').textContent===${JSON.stringify(unsafe)} && !document.querySelector('.comment__text img,.comment__text script') && !window.commentInjection`,
    ),
    'HTML and SQL-like comment text remain literal DOM text',
  );
  const person = 'author';
  fixture.people[person] = {
    email: marker + '@example.com',
    tag: marker.replace('-', '_'),
    name: 'Имя Комментатора',
  };
  const registration = await actorRequest('owner', '/auth/register', 'POST', {
    ...fixture.people[person],
    password: 'abcdef',
    password_confirmation: 'abcdef',
  });
  assert.equal(registration.status, 201);
  assert.equal(
    (
      await actorRequest(person, '/videos/' + v.public + '/comments', 'POST', {
        text: marker + ' author',
      })
    ).status,
    201,
  );
  await c.reload();
  await c.wait(
    `document.querySelector('.comment__text')?.textContent===${JSON.stringify(marker + ' author')}`,
  );
  check(
    await c.evaluate(
      `document.querySelector('.comment__heading strong').textContent===${JSON.stringify(fixture.people[person].name)} && !document.querySelector('.comment__heading img') && !window.commentInjection`,
    ),
    'another author uses the actual registered display name supplied by the server',
  );

  holdPostBefore = true;
  const delayed = marker + ' single',
    before = writes.length;
  await fill(delayed);
  await c.evaluate(
    `for(let i=0;i<4;i++)document.querySelector('${submit}').click()`,
  );
  await until(() => held.size === 1);
  check(
    writes.length === before + 1 &&
      (await c.evaluate(
        `document.querySelector('#new-comment').disabled && document.querySelector('#new-comment').value===${JSON.stringify(delayed)} && ![...document.querySelectorAll('.comment__text')].some(x=>x.textContent===${JSON.stringify(delayed)})`,
      )),
    'rapid submit sends one command and leaves the pending draft unpublished',
  );
  holdPostBefore = false;
  release();
  await posted(delayed);
  check(
    (await allComments()).filter((x) => x.text === delayed).length === 1,
    'one comment was persisted after repeated clicks',
  );
  check(
    writes.at(-1).path === '/api/v1/videos/' + v.public + '/comments' &&
      JSON.stringify(writes.at(-1).fields) === '["text"]',
    'POST uses exact video ID and only text, without author or counters',
  );

  postFailure = true;
  const failed = marker + ' failed';
  await fill(failed);
  await c.click(submit);
  await c.wait(
    `document.querySelector('#comment-validation')?.textContent.length>0 && !document.querySelector('#new-comment').disabled`,
  );
  check(
    await c.evaluate(
      `document.querySelector('#new-comment').value===${JSON.stringify(failed)} && document.activeElement.id==='new-comment' && !document.querySelector('.discussion__notice') && !window.commentInjection`,
    ),
    '503 preserves draft and focus without success or executable error detail',
  );
  postFailure = false;
  check(
    !(await allComments()).some((x) => x.text === failed),
    '503 left the database unchanged',
  );
  await c.click(submit);
  await posted(failed);
  check(true, 'explicit retry publishes the retained draft');

  const invalid = marker + '\ud800';
  await fill(invalid);
  await c.click(submit);
  await until(() => writes.at(-1).status === 422);
  await c.wait(
    `!!document.querySelector('#comment-validation')?.textContent && !document.querySelector('#new-comment').disabled`,
  );
  check(
    await c.evaluate(
      `document.querySelector('#new-comment').value===${JSON.stringify(invalid)} && !document.querySelector('.discussion__notice')`,
    ),
    'server validation preserves rejected Unicode without false success',
  );
  dropCsrf = true;
  const csrf = marker + ' csrf';
  await fill(csrf);
  await c.click(submit);
  await until(() => writes.at(-1).status === 403);
  await c.wait(
    `document.querySelector('#comment-validation')?.textContent.includes('Обновите страницу') && !document.querySelector('#new-comment').disabled`,
  );
  check(
    await c.evaluate(
      `document.querySelector('#new-comment').value===${JSON.stringify(csrf)} && !!document.querySelector('.comment') && !!document.querySelector('.player video')`,
    ),
    'CSRF failure retains allowed discussion, player and draft',
  );
  dropCsrf = false;
  check(
    !(await allComments()).some((x) => x.text === csrf),
    'CSRF denial left the database unchanged',
  );
  await c.click(submit);
  await posted(csrf);
  await fill(marker + ' cancel');
  await c.click('.discussion__cancel');
  check(
    await c.evaluate(
      `document.querySelector('#new-comment').value==='' && !document.querySelector('.discussion__form-actions')`,
    ),
    'cancel clears the local draft',
  );

  const seeded = [];
  for (let i = 0; i < 43; i++) {
    const r = await actorRequest(
      'owner',
      '/videos/' + v.public + '/comments',
      'POST',
      { text: marker + ' page ' + i },
    );
    assert.equal(r.status, 201);
    seeded.push(r.data);
  }
  for (const comment of seeded.slice(0, 2))
    for (const actor of ['owner', 'invited'])
      assert.equal(
        (
          await actorRequest(
            actor,
            '/videos/' + v.public + '/comments/' + comment.id + '/like',
            'PUT',
            { liked: true },
          )
        ).status,
        200,
      );
  const expected = await allComments();
  await c.reload();
  await c.wait(`document.querySelectorAll('.comment').length===20`);
  check(
    JSON.stringify(await uiIds()) ===
      JSON.stringify(expected.slice(0, 20).map((x) => x.id)),
    'first page uses server newest order and a portion of twenty',
  );
  nextFailure = true;
  await c.click('.discussion__more');
  await c.wait(
    `document.querySelector('.discussion__more')?.textContent.trim()==='Повторить'`,
  );
  check(
    (await uiIds()).length === 20,
    'next-page failure preserves confirmed first page',
  );
  nextFailure = false;
  await c.click('.discussion__more');
  await c.wait(`document.querySelectorAll('.comment').length===40`);
  check(
    JSON.stringify(await uiIds()) ===
      JSON.stringify(expected.slice(0, 40).map((x) => x.id)),
    'retry continues with real cursor without duplicate rows',
  );
  while ((await uiIds()).length < expected.length) {
    const loaded = (await uiIds()).length;
    await c.evaluate(
      `document.querySelector('.discussion > [aria-hidden=true]').scrollIntoView({block:'center'})`,
    );
    await c.wait(`document.querySelectorAll('.comment').length>${loaded}`);
  }
  check(
    new Set(await uiIds()).size === expected.length &&
      !(await c.evaluate(`!!document.querySelector('.discussion__more')`)),
    'scroll loads the final portion and removes continuation at the end',
  );

  await c.evaluate('scrollTo(0,0)');
  await choose('popular');
  await c.wait(
    `document.querySelector('.comment')?.dataset.commentId==='${seeded[1].id}'`,
  );
  const popular = await allComments('popular');
  check(
    JSON.stringify((await uiIds()).slice(0, 2)) ===
      JSON.stringify([seeded[1].id, seeded[0].id]) &&
      popular[0].id === seeded[1].id,
    'popular reaches older comments beyond first page and breaks like ties by newest',
  );
  check(
    await c.evaluate(
      `!document.querySelector('.discussion__menu') && document.activeElement.getAttribute('aria-label')==='Упорядочить комментарии'`,
    ),
    'sort selection closes menu and returns keyboard focus',
  );

  await choose('newest');
  await c.wait(
    `document.querySelector('.comment')?.dataset.commentId==='${expected[0].id}'`,
  );
  holdReadSort = 'popular';
  await choose('popular');
  await until(() => held.size === 1);
  await choose('newest');
  await c.wait(
    `document.querySelector('.comment')?.dataset.commentId==='${expected[0].id}'`,
  );
  release();
  await pause(250);
  check(
    (await uiIds())[0] === expected[0].id,
    'late sort response cannot replace the selected server order',
  );

  readFailure = true;
  await refetch();
  await c.wait(
    `document.querySelector('.discussion__more')?.textContent.trim()==='Повторить' && !document.querySelector('.comment')`,
  );
  readFailure = false;
  await c.click('.discussion__more');
  await c.wait(
    `document.querySelector('.comment')?.dataset.commentId==='${expected[0].id}'`,
  );
  check(
    true,
    'failed background read hides stale rows and explicit retry recovers',
  );
  readFailure = true;
  await c.reload();
  await c.wait(
    `document.querySelector('.discussion__more')?.textContent.trim()==='Повторить' && !document.querySelector('.comment')`,
  );
  readFailure = false;
  await c.click('.discussion__more');
  await c.wait(
    `!!document.querySelector('#new-comment') && !!document.querySelector('.comment')`,
  );
  check(true, 'initial list failure has a working retry');

  const late = marker + ' late';
  holdPostAfter = true;
  await fill(late);
  await c.click(submit);
  await until(() => held.size === 1 && writes.at(-1).status === 201);
  await spa('/watch?v=' + v.other);
  await c.wait(
    `!!document.querySelector('#new-comment') && !document.querySelector('#new-comment').disabled`,
  );
  await fill(marker + ' other draft');
  holdPostAfter = false;
  release();
  await pause(300);
  check(
    await c.evaluate(
      `document.querySelector('#new-comment').value===${JSON.stringify(marker + ' other draft')} && !document.querySelector('.discussion__notice') && ![...document.querySelectorAll('.comment__text')].some(x=>x.textContent===${JSON.stringify(late)})`,
    ),
    'late publication cannot clear or publish into another video composer',
  );
  check(await noOldQuery(), 'leaving removes inactive discussion snapshots');
  await navigate();
  await c.wait(
    `document.querySelector('.comment__text')?.textContent===${JSON.stringify(late)}`,
  );
  check(
    (await allComments()).some((x) => x.text === late),
    'return reads the server-committed comment even when its response was cancelled',
  );

  holdReadSort = 'newest';
  await refetch();
  await until(() => held.size === 1);
  await spa('/watch?v=' + v.other);
  await c.wait(`!!document.querySelector('#new-comment')`);
  release();
  await pause(200);
  check(
    !(await c.evaluate(
      `[...document.querySelectorAll('.comment__text')].some(x=>x.textContent===${JSON.stringify(late)})`,
    )),
    'late list cannot transfer comments to another video',
  );
  await navigate();
  holdPostBefore = true;
  postFailure = true;
  await fill(marker + ' late error');
  await c.click(submit);
  await until(() => held.size === 1);
  await spa('/watch?v=' + v.other);
  await c.wait(`!!document.querySelector('#new-comment')`);
  holdPostBefore = false;
  release();
  await until(() => writes.at(-1).status === 503);
  postFailure = false;
  await pause(200);
  check(
    !(await c.evaluate(
      `!!document.querySelector('#comment-validation')?.textContent`,
    )),
    'late failed publication cannot add an error to another video',
  );

  await navigate();
  holdPostAfter = true;
  await fill(marker + ' old viewer');
  await c.click(submit);
  await until(() => held.size === 1 && writes.at(-1).status === 201);
  await logout();
  await login('invited');
  await fill(marker + ' new viewer');
  holdPostAfter = false;
  release();
  await pause(250);
  check(
    await c.evaluate(
      `document.querySelector('#new-comment').value===${JSON.stringify(marker + ' new viewer')} && !document.querySelector('.discussion__notice')`,
    ),
    'late success after logout and account switch preserves the new viewer draft',
  );
  await api('/auth/logout', 'POST');
  await c.click(submit);
  await c.wait(
    `!!document.querySelector('.discussion__guest') && !document.querySelector('#new-comment')`,
  );
  check(
    writes.at(-1).status === 401 &&
      !(await allComments()).some((x) => x.text === marker + ' new viewer'),
    'expired session removes composer and leaves no unauthorized comment',
  );

  await login('owner');
  holdPostBefore = true;
  const denied = marker + ' denied';
  await fill(denied);
  await c.click(submit);
  await until(() => held.size === 1);
  await visibility('private');
  holdPostBefore = false;
  release();
  await until(() => writes.at(-1).status === 404);
  await c.wait(
    `!!document.querySelector('.watch-page__private-discussion') && !document.querySelector('.discussion') && !!document.querySelector('.player video')`,
  );
  check(
    await noOldQuery(),
    'private transition removes all comment snapshots while keeping owner playback',
  );
  const deniedRead = await actorRequest(
    'owner',
    '/videos/' + v.public + '/comments',
  );
  check(
    deniedRead.status === 404,
    'owner cannot read private discussion through API',
  );
  await login('invited', v.private);
  check(
    !(await c.evaluate(`!!document.querySelector('.discussion')`)),
    'invited private viewer also has no discussion',
  );
  await visibility('public');
  holdReadSort = 'newest';
  await c.navigate(base + '/watch?v=' + v.public);
  await until(() => held.size === 1);
  check(
    !(await c.evaluate(
      `!!document.querySelector('.comment') || !!document.querySelector('#new-comment')`,
    )),
    'return to public shows no old rows or composer before fresh discussion confirmation',
  );
  release();
  await c.wait(
    `!!document.querySelector('#new-comment') && !!document.querySelector('.comment')`,
  );
  const restored = await allComments();
  check(
    restored.some((x) => x.text === boundary) &&
      !restored.some((x) => x.text === denied),
    'public restoration recovers old comments without accepting denied publication',
  );

  await login('owner');
  await visibility('private');
  await refetch();
  await c.wait(
    `!!document.querySelector('.watch-page__private-discussion') && !document.querySelector('.discussion')`,
  );
  check(
    await noOldQuery(),
    'GET denial closes discussion and clears every sort cache',
  );
  await visibility('public');
  readFailure = true;
  await c.reload();
  await c.wait(
    `document.querySelector('.discussion__more')?.textContent.trim()==='Повторить'`,
  );
  check(
    !(await c.evaluate(`!!document.querySelector('.comment')`)),
    'public metadata alone cannot restore comments during a failed list read',
  );
  readFailure = false;
  await c.click('.discussion__more');
  await c.wait(
    `!!document.querySelector('#new-comment') && !!document.querySelector('.comment')`,
  );

  await c.viewport(390, 844, true);
  await c.evaluate('scrollTo(0,0)');
  await c.click('.discussion__sort > button');
  await key('ArrowDown');
  check(
    await c.evaluate(
      `document.activeElement.textContent.includes('популярные')`,
    ),
    'phone sort menu supports keyboard movement',
  );
  await key('Escape');
  check(
    await c.evaluate(
      `!document.querySelector('.discussion__menu') && document.activeElement.getAttribute('aria-label')==='Упорядочить комментарии'`,
    ),
    'Escape closes phone menu and restores focus',
  );
  const phoneBefore = writes.length;
  await fill(marker + ' phone');
  await key('Enter');
  const phone = await c.evaluate(
    `document.querySelector('#new-comment').value`,
  );
  check(
    phone.endsWith('\n') && writes.length === phoneBefore,
    'Enter in phone textarea inserts a newline without publishing',
  );
  await c.evaluate(`document.querySelector('${submit}').focus()`);
  await key('Enter');
  await posted(phone);
  check(
    (await allComments())[0].text === phone,
    'keyboard publication on phone width preserves exact multiline text',
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
    'no unexpected browser runtime errors: ' + errors.join(' | '),
  );
  check(
    writes.every((x) => JSON.stringify(x.fields) === '["text"]'),
    'all publication commands contain only comment text',
  );
  console.log(
    JSON.stringify({
      profile: 'comments',
      browser: firefox ? 'Firefox' : 'Chromium',
      assertions,
      status: 'pass',
    }),
  );
} catch (error) {
  console.error(
    JSON.stringify({
      profile: 'comments',
      assertions,
      writes: writes.slice(-4),
      path: await c.evaluate('location.pathname+location.search'),
      discussion: await c.evaluate(
        "document.querySelector('.discussion [role=alert]')?.textContent",
      ),
      composer: JSON.parse(
        await c.evaluate(
          `JSON.stringify((()=>{const t=document.querySelector('#new-comment');return {active:document.activeElement?.id,disabled:t?.disabled,focused:document.hasFocus(),composing:t?.__vueParentComponent?.setupState?.composing,pending:t?.__vueParentComponent?.setupState?.pending};})())`,
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
