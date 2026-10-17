import {spawn} from "node:child_process";
import {mkdtempSync, readFileSync, rmSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";

const delay = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));
const url = process.argv[process.argv.indexOf("--url") + 1];
if (!url) throw new Error("--url is required");

const profile = mkdtempSync(join(tmpdir(), "p54-chrome-"));
const chrome = spawn("/usr/bin/google-chrome", [
  "--headless=new", "--no-first-run", "--disable-gpu", "--remote-debugging-port=0",
  `--user-data-dir=${profile}`, url
], {stdio: "ignore", detached: true});

async function waitForPort() {
  const path = join(profile, "DevToolsActivePort");
  for (let attempt = 0; attempt < 100; attempt += 1) {
    try {
      return readFileSync(path, "utf8").split("\n")[0];
    } catch (_) {
      await delay(20);
    }
  }
  throw new Error("Chrome DevTools port was not created");
}

async function waitForPage(port) {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
    const page = targets.find((target) => target.type === "page" && target.url.startsWith(url));
    if (page) return page;
    await delay(20);
  }
  throw new Error("Chrome did not open the fixture URL");
}

function evaluate(webSocketDebuggerUrl, expression) {
  return new Promise((resolve, reject) => {
    const socket = new WebSocket(webSocketDebuggerUrl);
    socket.addEventListener("open", () => {
      socket.send(JSON.stringify({id: 1, method: "Runtime.enable"}));
      socket.send(JSON.stringify({
        id: 2, method: "Runtime.evaluate",
        params: {expression, awaitPromise: true, returnByValue: true}
      }));
    });
    socket.addEventListener("message", (event) => {
      const message = JSON.parse(event.data);
      if (message.id !== 2) return;
      socket.close();
      const remote = message.result?.result;
      if (!remote || !("value" in remote)) reject(new Error(JSON.stringify(message)));
      else resolve(remote.value);
    });
    socket.addEventListener("error", reject);
  });
}

try {
  const port = await waitForPort();
  const page = await waitForPage(port);
  await delay(200);
  const expression = `new Promise((resolve, reject) => {
    let attempts = 0;
    const timer = setInterval(() => {
      if (typeof runFixture === "function") {
        clearInterval(timer);
        resolve(runFixture());
      } else if (++attempts > 100) {
        clearInterval(timer);
        reject(new Error("fixture did not load"));
      }
    }, 20);
  })`;
  const result = await evaluate(page.webSocketDebuggerUrl, expression);
  process.stdout.write(`${JSON.stringify(result)}\n`);
} finally {
  const exited = chrome.exitCode !== null ? Promise.resolve() :
    new Promise((resolve) => chrome.once("exit", resolve));
  try {
    process.kill(-chrome.pid, "SIGTERM");
  } catch (_) {
    chrome.kill("SIGTERM");
  }
  await Promise.race([
    exited,
    delay(2000)
  ]);
  if (chrome.exitCode === null) {
    try {
      process.kill(-chrome.pid, "SIGKILL");
    } catch (_) {
      chrome.kill("SIGKILL");
    }
  }
  await delay(100);
  rmSync(profile, {recursive: true, force: true, maxRetries: 10, retryDelay: 100});
}
