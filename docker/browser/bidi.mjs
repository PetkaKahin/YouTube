import { spawn } from "node:child_process";
import { mkdir, writeFile } from "node:fs/promises";
import { pause } from "./cdp.mjs";

export async function firefoxBrowser({ autoplayAllow = false } = {}) {
  await mkdir("/tmp/firefox-studio", { recursive: true });
  if (autoplayAllow) await writeFile('/tmp/firefox-studio/user.js','user_pref("media.autoplay.default", 0);\n');
  const process = spawn(
    "firefox-esr",
    [
      "--headless",
      "--remote-debugging-port=9227",
      "--profile",
      "/tmp/firefox-studio",
      "about:blank",
    ],
    { stdio: "ignore" },
  );
  let socket;
  for (let attempt = 0; attempt < 100; attempt++) {
    try {
      const candidate = new WebSocket("ws://127.0.0.1:9227/session");
      await new Promise((resolve, reject) => {
        candidate.addEventListener("open", resolve, { once: true });
        candidate.addEventListener("error", reject, { once: true });
      });
      socket = candidate;
      break;
    } catch {
      await pause(100);
    }
  }
  if (!socket) {
    process.kill();
    throw new Error("Firefox did not start");
  }
  let sequence = 0;
  const calls = new Map(),
    errors = [];
  socket.addEventListener("message", ({ data }) => {
    const message = JSON.parse(data);
    if (
      message.method === "log.entryAdded" &&
      message.params.type === "javascript" &&
      message.params.level === "error"
    )
      errors.push(message.params.text);
    const call = calls.get(message.id);
    if (call) {
      clearTimeout(call.timer);
      calls.delete(message.id);
      if (message.type === "error")
        call.reject(new Error(JSON.stringify(message)));
      else call.resolve(message.result);
    }
  });
  const send = (method, params = {}) =>
    new Promise((resolve, reject) => {
      const id = ++sequence;
      const timer = setTimeout(() => {
        calls.delete(id);
        reject(new Error(`BiDi timeout: ${method}`));
      }, 15000);
      calls.set(id, { resolve, reject, timer });
      socket.send(JSON.stringify({ id, method, params }));
    });
  await send("session.new", { capabilities: {} });
  const { context } = await send("browsingContext.create", { type: "tab" });
  await send("session.subscribe", {
    events: ["log.entryAdded"],
    contexts: [context],
  });
  const evaluate = async (expression) => {
    const result = await send("script.evaluate", {
      expression,
      target: { context },
      awaitPromise: true,
    });
    if (result.type === "exception")
      throw new Error(JSON.stringify(result.exceptionDetails));
    return result.result.value;
  };
  const wait = async (expression, timeoutMs = 10000) => {
    for (let attempt = 0; attempt < Math.ceil(timeoutMs / 100); attempt++) {
      if (await evaluate(expression)) return;
      await pause(100);
    }
    throw new Error(`DOM condition failed: ${expression}`);
  };
  const navigate = async (url) => {
    await send("browsingContext.navigate", { context, url, wait: "complete" });
    await wait(`!!document.querySelector('#app > *')`);
  };
  return {
    evaluate,
    wait,
    errors,
    navigate,
    reload: async () => {
      await send("browsingContext.reload", { context, wait: "complete" });
      await wait(`!!document.querySelector('#app > *')`);
    },
    click: async (selector) => {
      await wait(`!!document.querySelector(${JSON.stringify(selector)})`);
      await evaluate(
        `document.querySelector(${JSON.stringify(selector)}).click()`,
      );
    },
    fill: (selector, value) =>
      evaluate(
        `(()=>{const input=document.querySelector(${JSON.stringify(selector)});input.value=${JSON.stringify(value)};input.dispatchEvent(new Event('input',{bubbles:true}));})()`,
      ),
    viewport: (width, height) =>
      send("browsingContext.setViewport", {
        context,
        viewport: { width, height },
        devicePixelRatio: 1,
      }),
    key: (value) =>
      send("input.performActions", {
        context,
        actions: [
          {
            type: "key",
            id: "keyboard",
            actions: [
              { type: "keyDown", value },
              { type: "keyUp", value },
            ],
          },
        ],
      }),
    screenshot: async (name) => {
      const shot = await send("browsingContext.captureScreenshot", {
        context,
        origin: "viewport",
      });
      await writeFile(`/work/${name}.png`, Buffer.from(shot.data, "base64"));
    },
    close: async () => {
      try {
        await send("session.end");
      } finally {
        socket.close();
        process.kill();
      }
    },
  };
}
