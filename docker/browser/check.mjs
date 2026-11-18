import assert from "node:assert/strict";
import { createServer } from "node:http";
import { randomUUID } from "node:crypto";
import { browser, pause } from "./cdp.mjs";

const server = createServer(async (request, response) => {
  try {
    const api = request.url.startsWith("/api/");
    const target = api
      ? `http://python:8181${request.url.slice(4)}`
      : `http://node:5173${request.url}`;
    const headers = new Headers();
    for (const key of ["content-type", "cookie", "x-csrftoken", "accept"])
      if (request.headers[key]) headers.set(key, request.headers[key]);
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    const upstream = await fetch(target, {
      method: request.method,
      headers,
      body: chunks.length ? Buffer.concat(chunks) : undefined,
      redirect: "manual",
    });
    response.statusCode = upstream.status;
    for (const key of ["content-type", "location", "cache-control"])
      if (upstream.headers.has(key))
        response.setHeader(key, upstream.headers.get(key));
    if (upstream.headers.getSetCookie().length)
      response.setHeader("set-cookie", upstream.headers.getSetCookie());
    response.end(Buffer.from(await upstream.arrayBuffer()));
  } catch {
    response
      .writeHead(502, { "content-type": "application/json" })
      .end(JSON.stringify({ code: "test_upstream_unavailable" }));
  }
});
await new Promise((resolve) => server.listen(4180, "127.0.0.1", resolve));
let apiReady = false;
for (let attempt = 0; attempt < 200; attempt++) {
  try {
    const response = await fetch("http://python:8181/v1/auth/me");
    if (response.status === 401) {
      apiReady = true;
      break;
    }
  } catch {}
  await pause(100);
}
if (!apiReady) throw new Error("Isolated browser API did not start");
const cdp = await browser();
const base = "http://127.0.0.1:4180";
const token = randomUUID().replaceAll("-", "").slice(0, 20);
const email = `${token}@example.com`;
const password = " Пароль 123 ";
let assertions = 0;
const check = (value, message) => {
  assert.ok(value, message);
  assertions++;
};

try {
  await cdp.viewport(1920, 935);
  await cdp.navigate(base);
  await cdp.wait(
    `document.querySelector('.page-state h1')?.textContent === 'Пока нет видео' && !!document.querySelector('.masthead .sign-in')`,
  );
  check(
    await cdp.evaluate(
      `document.querySelector('.masthead').getBoundingClientRect().height === 56`,
    ),
    "Desktop header is 56 px",
  );
  check(
    await cdp.evaluate(
      `Math.abs(document.querySelector('.search-form').getBoundingClientRect().x - 634) <= 2`,
    ),
    "Search matches reference x coordinate",
  );
  check(
    await cdp.evaluate(`document.documentElement.scrollWidth <= innerWidth`),
    "Desktop has no horizontal overflow",
  );
  await cdp.screenshot("home-desktop");
  await cdp.click(".masthead__menu");
  check(
    await cdp.evaluate(
      `document.querySelector('.navigation').getBoundingClientRect().width === 72`,
    ),
    "Collapsed navigation is 72 px",
  );
  await cdp.click(".masthead__menu");

  await cdp.viewport(1000, 900);
  await cdp.click(".masthead__menu");
  await cdp.wait(`!!document.querySelector('.navigation--open')`);
  await cdp.send("Input.dispatchKeyEvent", {
    type: "keyDown",
    key: "Escape",
    code: "Escape",
  });
  await cdp.wait(
    `document.activeElement.getAttribute('aria-label') === 'Меню навигации'`,
  );
  check(
    await cdp.evaluate(
      `!document.querySelector('.app-shell__content').inert && document.activeElement.getAttribute('aria-expanded') === 'false'`,
    ),
    "Drawer closes with restored focus and accessibility state",
  );
  await cdp.viewport(1920, 935);

  await cdp.send("Network.clearBrowserCookies");
  await cdp.navigate(
    `${base}/register?returnTo=%2Fresults%3Fsearch_query%3D${token}`,
  );
  for (const [field, value] of Object.entries({
    name: "Иван Петров",
    email,
    tag: `b${token}`,
    password,
    password_confirmation: "different",
  }))
    await cdp.fill(`#register-${field}`, value);
  await cdp.click('button[type="submit"]');
  await cdp.wait(
    `document.querySelector('.field__error')?.textContent === 'Пароли не совпадают.'`,
  );
  check(
    await cdp.evaluate(
      `document.activeElement.id === 'register-password_confirmation'`,
    ),
    "Mismatch returns focus to confirmation",
  );
  await cdp.fill("#register-password_confirmation", password);
  await cdp.click('button[type="submit"]');
  await cdp.wait(
    `location.pathname === '/login' && !!document.querySelector('.notice')`,
  );
  check(
    await cdp.evaluate(`!document.cookie.includes('session=')`),
    "Registration does not log in",
  );

  await cdp.fill("#login-email", email);
  await cdp.fill("#login-password", "wrong");
  await cdp.click('button[type="submit"]');
  await cdp.wait(
    `document.querySelector('.form-error')?.textContent === 'Неверная почта или пароль.'`,
  );
  await cdp.fill("#login-password", password);
  await cdp.click('button[type="submit"]');
  await cdp.wait(
    `location.pathname === '/results' && !!document.querySelector('.avatar-button')`,
  );
  check(
    await cdp.evaluate(
      `new URLSearchParams(location.search).get('search_query') === ${JSON.stringify(token)}`,
    ),
    "Login restores the internal destination",
  );
  check(
    await cdp.evaluate(
      `!document.cookie.split('; ').some(x=>x.startsWith('session=')) && !JSON.stringify({...localStorage,...sessionStorage}).includes(${JSON.stringify(email)}) && !JSON.stringify({...localStorage,...sessionStorage}).includes(${JSON.stringify(password)})`,
    ),
    "No credentials in JS storage or readable session cookie",
  );
  check(
    await cdp.evaluate(
      `!location.href.includes('Пароль') && !location.href.includes(${JSON.stringify(email)})`,
    ),
    "No credentials in the URL",
  );
  const firstId = await cdp.evaluate(
    `fetch('/api/v1/auth/me').then(r=>r.text()).then(text=>text.match(/"id"\\s*:\\s*(\\d+)/)?.[1])`,
  );
  check(
    typeof firstId === "string" && BigInt(firstId) >= 9007199254740992n,
    "Real account uses an int64 ID above the safe number range",
  );
  const playlistTitle = `<img src=x onerror=window.frontendInjected=1> ${token}`;
  const createdPlaylist = await cdp.evaluate(
    `(()=>{const csrf=document.cookie.split('; ').find(x=>x.startsWith('csrftoken=')).slice(10);return fetch('/api/v1/playlists',{method:'POST',headers:{'content-type':'application/json','X-CSRFToken':decodeURIComponent(csrf)},body:JSON.stringify({title:${JSON.stringify(playlistTitle)},description:'Проверка пользовательского текста',visibility:'public'})}).then(async r=>({status:r.status,id:(await r.text()).match(/"id"\\s*:\\s*(\\d+)/)?.[1]}))})()`,
  );
  check(createdPlaylist.status === 201, "Real public playlist fixture created");
  await cdp.navigate(`${base}/results?search_query=${token}`);
  await cdp.wait(
    `!!document.querySelector('.channel-result') && !!document.querySelector('.playlist-result') && !!document.querySelector('button[aria-label="Меню аккаунта"]')`,
  );
  check(
    await cdp.evaluate(
      `document.querySelector('.playlist-result h2').textContent.trim() === ${JSON.stringify(playlistTitle)} && !window.frontendInjected && !document.querySelector('img[src="x"]')`,
    ),
    "Playlist title remains text in real search results",
  );
  check(
    BigInt(createdPlaylist.id) > 9007199254740992n &&
      (await cdp.evaluate(
        `(()=>{const url=new URL(document.querySelector('.playlist-result__preview').getAttribute('href'),location.href);return url.pathname.startsWith('/@') && url.pathname.endsWith('/playlists') && url.searchParams.get('list') === ${JSON.stringify(createdPlaylist.id)}})()`,
      )),
    "Playlist link preserves the exact odd int64 ID",
  );
  await cdp.screenshot("search-desktop");
  await cdp.click('button[aria-label="Меню аккаунта"]');
  await cdp.wait(
    `document.querySelector('.account-menu')?.textContent.includes(${JSON.stringify(email)})`,
  );
  await cdp.send("Input.dispatchKeyEvent", {
    type: "keyDown",
    key: "Escape",
    code: "Escape",
  });
  check(
    await cdp.evaluate(
      `!document.querySelector('.account-menu') && document.activeElement.getAttribute('aria-label') === 'Меню аккаунта'`,
    ),
    "Escape restores account focus",
  );
  await cdp.click(".filters-toggle");
  const selectFilter = async (label) =>
    cdp.evaluate(
      `(()=>{const button=[...document.querySelectorAll('.filters button')].find(x=>x.textContent.trim()===${JSON.stringify(label)});button.click();})()`,
    );
  await selectFilter("4K");
  await cdp.wait(
    `new URLSearchParams(location.search).getAll('feature').includes('4k')`,
  );
  await selectFilter("HDR");
  await cdp.wait(
    `new URLSearchParams(location.search).getAll('feature').length === 2`,
  );
  check(
    await cdp.evaluate(
      `[...document.querySelectorAll('.filters button[aria-pressed="true"]')].some(x=>x.textContent.trim()==='4K') && [...document.querySelectorAll('.filters button[aria-pressed="true"]')].some(x=>x.textContent.trim()==='HDR')`,
    ),
    "Multiple features stay selected",
  );

  await cdp.viewport(390, 844, true);
  await cdp.navigate(base);
  await cdp.wait(`!!document.querySelector('.avatar-button')`);
  check(
    await cdp.evaluate(
      `document.querySelector('.masthead').getBoundingClientRect().height === 48 && document.documentElement.scrollWidth <= innerWidth`,
    ),
    "Phone header and overflow",
  );
  await cdp.click('button[aria-label="Открыть поиск"]');
  await cdp.wait(
    `location.hash === '#searching' && document.activeElement.name === 'search_query'`,
  );
  check(
    await cdp.evaluate(`document.activeElement.name === 'search_query'`),
    "Mobile search focuses input",
  );
  await cdp.send("Input.dispatchKeyEvent", {
    type: "keyDown",
    key: "Escape",
    code: "Escape",
  });
  await cdp.wait(
    `document.activeElement.getAttribute('aria-label') === 'Открыть поиск'`,
  );
  check(
    await cdp.evaluate(
      `location.hash === '' && !document.querySelector('.app-shell__content').inert`,
    ),
    "Mobile search closes and restores focus",
  );
  await cdp.click('button[aria-label="Открыть поиск"]');
  await cdp.wait(`location.hash === '#searching'`);
  await cdp.evaluate("history.back()");
  await cdp.wait(
    `location.hash === '' && document.activeElement.getAttribute('aria-label') === 'Открыть поиск'`,
  );
  check(
    await cdp.evaluate(`!document.querySelector('.mobile-search-backdrop')`),
    "Browser Back closes mobile search",
  );
  await cdp.evaluate("history.forward()");
  await cdp.wait(
    `location.hash === '#searching' && document.activeElement.name === 'search_query'`,
  );
  check(
    await cdp.evaluate(`!!document.querySelector('.mobile-search-backdrop')`),
    "Browser Forward restores mobile search",
  );
  await cdp.reload();
  await cdp.wait(
    `location.hash === '#searching' && document.activeElement.name === 'search_query'`,
  );
  check(
    await cdp.evaluate(`document.querySelector('.app-shell__content').inert`),
    "Mobile search state survives refresh",
  );
  const hostile = "<img src=x onerror=window.frontendInjected=1>";
  await cdp.fill('input[name="search_query"]', hostile);
  await cdp.evaluate(`document.querySelector('.search-form').requestSubmit()`);
  await cdp.wait(
    `new URLSearchParams(location.search).get('search_query') === ${JSON.stringify(hostile)} && !document.querySelector('.search-page__loading') && (!!document.querySelector('.page-state') || !!document.querySelector('.playlist-result'))`,
  );
  check(
    await cdp.evaluate(
      `!window.frontendInjected && !document.querySelector('img[src="x"]') && document.querySelector('input[name="search_query"]').value === ${JSON.stringify(hostile)}`,
    ),
    "Search text stays data",
  );
  await cdp.screenshot("search-mobile");
  await cdp.click('button[aria-label="Меню аккаунта"]');
  await cdp.send("Network.emulateNetworkConditions", {
    offline: true,
    latency: 0,
    downloadThroughput: -1,
    uploadThroughput: -1,
  });
  await cdp.click("#logout-button");
  await cdp.wait(`!!document.querySelector('.account-menu .form-error')`);
  check(
    await cdp.evaluate(
      `!!document.querySelector('.account-menu') && !document.querySelector('.masthead .sign-in')`,
    ),
    "Failed logout preserves the account",
  );
  await cdp.send("Network.emulateNetworkConditions", {
    offline: false,
    latency: 0,
    downloadThroughput: -1,
    uploadThroughput: -1,
  });
  await cdp.click("#logout-button");
  await cdp.wait(`!!document.querySelector('.masthead .sign-in')`);
  check(
    await cdp.evaluate(
      `!document.body.textContent.includes(${JSON.stringify(email)}) && !document.querySelector('.account-menu')`,
    ),
    "Logout removes personal data",
  );
  check(
    await cdp.evaluate(`document.activeElement.matches('a[href^="/login"]')`),
    "Logout restores focus to visible sign-in",
  );
  await cdp.evaluate(`history.back()`);
  await pause(500);
  check(
    await cdp.evaluate(`!document.querySelector('.account-menu')`),
    "History cannot restore the account menu",
  );

  await cdp.navigate(`${base}/login?returnTo=https%3A%2F%2Fevil.example%2F`);
  await cdp.fill("#login-email", email);
  await cdp.fill("#login-password", password);
  await cdp.click('button[type="submit"]');
  await cdp.wait(`location.pathname === '/'`);
  check(
    await cdp.evaluate(`location.origin === ${JSON.stringify(base)}`),
    "External return URL is rejected",
  );

  const csrfStatus = await cdp.evaluate(
    `fetch('/api/v1/auth/logout',{method:'POST'}).then(r=>r.status)`,
  );
  check(csrfStatus === 403, "Missing CSRF is rejected by the real API");
  const personal = await cdp.evaluate(
    `fetch('/api/v1/auth/me').then(r=>r.json()).then(x=>x.email)`,
  );
  check(personal === email, "CSRF rejection did not end the session");
  await cdp.click('button[aria-label="Меню аккаунта"]');
  await cdp.click("#logout-button");
  await cdp.wait(`!!document.querySelector('.masthead .sign-in')`);

  await cdp.navigate(`${base}/register`);
  for (const [field, value] of Object.entries({
    name: "Иван Петров",
    email,
    tag: `duplicate${token}`,
    password,
    password_confirmation: password,
  }))
    await cdp.fill(`#register-${field}`, value);
  await cdp.click('button[type="submit"]');
  await cdp.wait(
    `document.querySelector('#hint-email')?.textContent === 'Это значение уже используется.'`,
  );
  check(
    await cdp.evaluate(`document.activeElement.id === 'register-email'`),
    "Duplicate email is a focused server field error",
  );
  const secondEmail = `${token}-second@example.com`;
  await cdp.fill("#register-email", secondEmail);
  await cdp.fill("#register-name", "И");
  await cdp.click('button[type="submit"]');
  check(
    await cdp.evaluate(
      `document.querySelector('#hint-name').textContent === 'Введите от 2 до 50 символов.'`,
    ),
    "Name rejects 1 character",
  );
  await cdp.fill("#register-name", "Ан");
  await cdp.fill("#register-tag", "ab");
  await cdp.click('button[type="submit"]');
  check(
    await cdp.evaluate(
      `document.querySelector('#hint-tag').textContent.includes('3–30')`,
    ),
    "Tag rejects 2 characters",
  );
  await cdp.fill("#register-tag", `longprefix${token}`);
  await cdp.fill("#register-password", "12345");
  await cdp.fill("#register-password_confirmation", "12345");
  await cdp.click('button[type="submit"]');
  check(
    await cdp.evaluate(
      `document.querySelector('#hint-password').textContent === 'Введите от 6 до 128 символов.'`,
    ),
    "Password rejects 5 characters",
  );
  await cdp.fill("#register-password", password);
  await cdp.fill("#register-password_confirmation", password);
  await cdp.fill("#register-name", "A".repeat(51));
  await cdp.click('button[type="submit"]');
  check(
    await cdp.evaluate(
      `document.querySelector('#hint-name').textContent === 'Введите от 2 до 50 символов.' && document.activeElement.id === 'register-name'`,
    ),
    "Name rejects 51 characters",
  );
  await cdp.fill("#register-name", "Ан");
  await cdp.fill("#register-tag", "a".repeat(31));
  await cdp.click('button[type="submit"]');
  check(
    await cdp.evaluate(
      `document.querySelector('#hint-tag').textContent.includes('3–30') && document.activeElement.id === 'register-tag'`,
    ),
    "Tag rejects 31 characters",
  );
  await cdp.fill("#register-tag", `longprefix${token}`);
  await cdp.fill("#register-password", "x".repeat(129));
  await cdp.fill("#register-password_confirmation", "x".repeat(129));
  await cdp.click('button[type="submit"]');
  await cdp.wait(
    `document.querySelector('#hint-password')?.textContent === 'Введите от 6 до 128 символов.'`,
  );
  check(
    await cdp.evaluate(`document.activeElement.id === 'register-password'`),
    "Password upper boundary is checked without trimming",
  );
  await cdp.fill("#register-name", "A".repeat(50));
  await cdp.fill("#register-email", `${token}-max@example.com`);
  await cdp.fill("#register-password", "я".repeat(128));
  await cdp.fill("#register-password_confirmation", "я".repeat(128));
  await cdp.click('button[type="submit"]');
  await cdp.wait(
    `location.pathname === '/login' && !!document.querySelector('.notice')`,
  );
  check(
    await cdp.evaluate(
      `!document.cookie.split('; ').some(x=>x.startsWith('session='))`,
    ),
    "Upper name/tag/password limits register without automatic login",
  );
  await cdp.fill("#login-email", `${token}-max@example.com`);
  await cdp.fill("#login-password", "я".repeat(128));
  await cdp.click('button[type="submit"]');
  await cdp.wait(
    `location.pathname === '/' && !!document.querySelector('.avatar-button')`,
  );
  await cdp.click('button[aria-label="Меню аккаунта"]');
  const adjacentId = await cdp.evaluate(
    `fetch('/api/v1/auth/me').then(r=>r.text()).then(text=>text.match(/"id"\\s*:\\s*(\\d+)/)?.[1])`,
  );
  check(
    BigInt(adjacentId) > BigInt(firstId) &&
      (await cdp.evaluate(
        `document.querySelector('.account-menu').textContent.includes(${JSON.stringify(`${token}-max@example.com`)})`,
      )),
    "Large int64 accounts remain distinct after login",
  );
  await cdp.click("#logout-button");
  await cdp.wait(`!!document.querySelector('.masthead .sign-in')`);
  await cdp.navigate(`${base}/register`);
  let minimumTag;
  for (let attempt = 0; attempt < 10; attempt++) {
    const candidate = randomUUID().slice(0, 3);
    const status = await cdp.evaluate(
      `fetch('/api/v1/channels/${candidate}').then(r=>r.status)`,
    );
    if (status === 404) {
      minimumTag = candidate;
      break;
    }
  }
  assert.ok(
    minimumTag,
    "A free 3-character tag is available in the isolated database",
  );
  await cdp.fill("#register-name", "Ан");
  await cdp.fill("#register-email", secondEmail);
  await cdp.fill("#register-tag", minimumTag);
  await cdp.fill("#register-password", "      ");
  await cdp.fill("#register-password_confirmation", "      ");
  await cdp.evaluate(
    `document.querySelector('form').requestSubmit();document.querySelector('form').requestSubmit();`,
  );
  await cdp.wait(
    `location.pathname === '/login' && !!document.querySelector('.notice')`,
  );
  await cdp.fill("#login-email", secondEmail);
  await cdp.fill("#login-password", "      ");
  await cdp.click('button[type="submit"]');
  await cdp.wait(
    `location.pathname === '/' && !!document.querySelector('.avatar-button')`,
  );
  await cdp.click('button[aria-label="Меню аккаунта"]');
  check(
    await cdp.evaluate(
      `document.querySelector('.account-menu').textContent.includes(${JSON.stringify(secondEmail)}) && !document.body.textContent.includes(${JSON.stringify(email)})`,
    ),
    "Second account cannot see the previous identity",
  );
  await cdp.click("#logout-button");
  await cdp.wait(`!!document.querySelector('.masthead .sign-in')`);

  await cdp.viewport(390, 844, true);
  await cdp.navigate(`${base}/register`);
  check(
    await cdp.evaluate(
      `document.documentElement.scrollWidth <= innerWidth && document.querySelector('.auth-card').getBoundingClientRect().width <= 358`,
    ),
    "Registration fits the phone",
  );
  await cdp.screenshot("register-mobile");
  await cdp.viewport(1920, 935);
  await cdp.navigate(`${base}/login`);
  await cdp.screenshot("login-desktop");
  check(
    cdp.errors.length === 0,
    `No uncaught browser exceptions: ${cdp.errors.join(", ")}`,
  );
  console.log(
    JSON.stringify(
      {
        status: "pass",
        assertions,
        engine: "Chromium",
        screens: ["1920x935", "390x844"],
        backend: "isolated real PostgreSQL/API/Valkey",
      },
      null,
      2,
    ),
  );
} catch (error) {
  await cdp.screenshot("failure");
  console.error(
    await cdp.evaluate(
      `({path:location.pathname,main:document.querySelector('main')?.innerText?.slice(0,300)})`,
    ),
  );
  console.error({ runtimeErrors: cdp.errors });
  throw error;
} finally {
  await cdp.close();
  server.closeAllConnections();
  await new Promise((resolve) => server.close(resolve));
}
