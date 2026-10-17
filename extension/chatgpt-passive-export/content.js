(function () {
  "use strict";

  if (globalThis.__chatgptPassiveExport) return;
  const Core = globalThis.PassiveExportCore;
  const STORAGE_PREFIX = "chatgpt-passive-export:v1:";
  let observer = null;
  let active = false;
  let captureQueued = false;
  let captureChain = Promise.resolve();
  let currentKey = null;

  function storageKey(key) {
    return `${STORAGE_PREFIX}${key}`;
  }

  function isSupportedPage() {
    return ["chatgpt.com", "chat.openai.com"].includes(location.hostname) &&
      /\/(?:c|share)\/[^/?#]+/.test(location.pathname);
  }

  async function performCapture() {
    if (!active || !isSupportedPage()) return status();
    const capturedAt = new Date().toISOString();
    const key = Core.chatKey(location.href);
    currentKey = key;
    const id = storageKey(key);
    const previous = (await chrome.storage.local.get(id))[id];
    const turns = Core.captureDocument(document, location.href, capturedAt);
    const session = Core.mergeCapture(previous, turns, {
      chatKey: key, url: location.href, title: document.title, capturedAt
    });
    await chrome.storage.local.set({[id]: session});
    return status(session);
  }

  function capture() {
    captureChain = captureChain.then(performCapture, performCapture);
    return captureChain;
  }

  function scheduleCapture() {
    if (captureQueued || !active) return;
    captureQueued = true;
    queueMicrotask(() => {
      captureQueued = false;
      capture().catch(() => {});
    });
  }

  async function start() {
    if (!isSupportedPage()) throw new Error("Apri una conversazione ChatGPT con URL stabile e riprova.");
    active = true;
    if (!observer) {
      observer = new MutationObserver(scheduleCapture);
      observer.observe(document.documentElement, {
        subtree: true, childList: true, characterData: true,
        attributes: true, attributeFilter: ["hidden", "aria-hidden", "aria-expanded"]
      });
    }
    return capture();
  }

  function stop() {
    active = false;
    observer?.disconnect();
    observer = null;
    return status();
  }

  async function sessions() {
    const all = await chrome.storage.local.get(null);
    return Object.entries(all).filter(([key]) => key.startsWith(STORAGE_PREFIX)).map(([, value]) => value);
  }

  function counts(items) {
    return {
      chats: items.length,
      turns: items.reduce((sum, item) => sum + item.turns.length, 0),
      versions: items.reduce((sum, item) => sum + item.turns.reduce((inner, turn) => inner + turn.versions.length, 0), 0)
    };
  }

  async function status(knownSession) {
    const items = knownSession ? await sessions() : await sessions();
    return {active, currentKey, ...counts(items)};
  }

  function download(name, type, body) {
    const blob = new Blob([body], {type});
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = name;
    anchor.hidden = true;
    document.documentElement.append(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  async function exportCurrent() {
    await capture();
    const key = currentKey || Core.chatKey(location.href);
    const id = storageKey(key);
    const session = (await chrome.storage.local.get(id))[id];
    if (!session) throw new Error("Nessuna cattura locale per questa chat.");
    const slug = (session.title || "chatgpt-chat").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 64) || "chatgpt-chat";
    download(`${slug}.md`, "text/markdown;charset=utf-8", Core.sessionMarkdown(session));
    download(`${slug}.json`, "application/json;charset=utf-8", JSON.stringify(session, null, 2) + "\n");
    return status(session);
  }

  async function clearAll() {
    const all = await chrome.storage.local.get(null);
    const keys = Object.keys(all).filter((key) => key.startsWith(STORAGE_PREFIX));
    if (keys.length) await chrome.storage.local.remove(keys);
    currentKey = null;
    return status();
  }

  chrome.runtime.onMessage.addListener((message, _sender, respond) => {
    const operations = {start, stop, status, export: exportCurrent, clear: clearAll};
    const operation = operations[message?.type];
    if (!operation) return false;
    Promise.resolve(operation()).then((value) => respond({ok: true, value}), (error) =>
      respond({ok: false, error: error.message || String(error)}));
    return true;
  });

  globalThis.__chatgptPassiveExport = {version: 1};
}());
