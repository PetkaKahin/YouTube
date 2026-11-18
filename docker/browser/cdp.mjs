import { spawn } from "node:child_process";
import { writeFile } from "node:fs/promises";

export const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

export async function browser({ autoplayAllow = false } = {}) {
  const chrome = spawn(
    "chromium",
    [
      "--headless",
      ...(autoplayAllow ? ['--autoplay-policy=no-user-gesture-required'] : []),
      "--no-sandbox",
      "--disable-dev-shm-usage",
      "--no-first-run",
      "--remote-debugging-port=9226",
      "--user-data-dir=/tmp/chrome-frontend",
      "about:blank",
    ],
    { stdio: "ignore" },
  );
  let target;
  for (let attempt = 0; attempt < 100; attempt++) {
    try {
      target = (
        await (await fetch("http://127.0.0.1:9226/json/list")).json()
      ).find((item) => item.type === "page");
      if (target) break;
    } catch {
      /* Chromium is starting. */
    }
    await pause(100);
  }
  if (!target) {
    chrome.kill();
    throw new Error("Chromium did not start");
  }
  const socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve, { once: true });
    socket.addEventListener("error", reject, { once: true });
  });
  let sequence = 0;
  const calls = new Map();
  const pageLoads = new Set();
  const errors = [];
  socket.addEventListener("message", ({ data }) => {
    const message = JSON.parse(data);
    if (message.method === "Page.loadEventFired") {
      for (const complete of [...pageLoads]) complete();
    }
    if (message.method === "Runtime.exceptionThrown")
      errors.push(message.params.exceptionDetails.text);
    const call = calls.get(message.id);
    if (call) {
      calls.delete(message.id);
      clearTimeout(call.timer);
      if (message.error) call.reject(new Error(JSON.stringify(message.error)));
      else call.resolve(message.result);
    }
  });
  const send = (method, params = {}) =>
    new Promise((resolve, reject) => {
      const id = ++sequence;
      const timer = setTimeout(() => {
        calls.delete(id);
        reject(new Error(`CDP timeout: ${method}`));
      }, 15000);
      calls.set(id, { resolve, reject, timer });
      socket.send(JSON.stringify({ id, method, params }));
    });
  const evaluate = async (expression) => {
    const result = await send("Runtime.evaluate", {
      expression,
      returnByValue: true,
      awaitPromise: true,
    });
    if (result.exceptionDetails)
      throw new Error(
        result.exceptionDetails.exception?.description ||
          result.exceptionDetails.text,
      );
    return result.result.value;
  };
  const wait = async (expression, timeoutMs = 10000) => {
    for (let attempt = 0; attempt < Math.ceil(timeoutMs / 100); attempt++) {
      if (await evaluate(expression)) return;
      await pause(100);
    }
    throw new Error(`DOM condition failed: ${expression}`);
  };
  const click = async (selector) => {
    await wait(`!!document.querySelector(${JSON.stringify(selector)})`);
    await evaluate(
      `document.querySelector(${JSON.stringify(selector)}).click()`,
    );
  };
  const fill = (selector, value) =>
    evaluate(
      `(()=>{const input=document.querySelector(${JSON.stringify(selector)});input.value=${JSON.stringify(value)};input.dispatchEvent(new Event('input',{bubbles:true}));})()`,
    );
  const screenshot = async (name) => {
    const { data } = await send("Page.captureScreenshot", {
      format: "png",
      captureBeyondViewport: false,
    });
    await writeFile(`/work/${name}.png`, Buffer.from(data, "base64"));
  };
  const navigate = async (url) => {
    await loadPage(() => send("Page.navigate", { url }));
  };
  const loadPage = async (action) => {
    let loadTimer;
    let complete;
    const loaded = new Promise((resolve, reject) => {
      complete = () => {
        pageLoads.delete(complete);
        clearTimeout(loadTimer);
        resolve();
      };
      pageLoads.add(complete);
      loadTimer = setTimeout(
        () => reject(new Error("Page load did not finish")),
        15000,
      );
    });
    try {
      await action();
      await loaded;
      await wait(
        `document.readyState === 'complete' && !!document.querySelector('#app > *')`,
      );
    } finally {
      clearTimeout(loadTimer);
      pageLoads.delete(complete);
    }
  };
  const reload = () => loadPage(() => send("Page.reload"));
  const viewport = (width, height, mobile = false) =>
    send("Emulation.setDeviceMetricsOverride", {
      width,
      height,
      deviceScaleFactor: 1,
      mobile,
    });
  await send("Runtime.enable");
  await send("Page.enable");
  await send("Network.enable");
  return {
    send,
    evaluate,
    wait,
    click,
    fill,
    screenshot,
    navigate,
    reload,
    viewport,
    errors,
    close: async () => {
      try {
        await send("Browser.close");
      } finally {
        socket.close();
        chrome.kill();
      }
    },
  };
}
