(function () {
  "use strict";

  if (globalThis.__chatgptPassiveExport) return;
  const Core = globalThis.PassiveExportCore;
  const STORAGE_PREFIX = "chatgpt-passive-export:v1:";
  let observer = null;
  let active = false;
  let captureQueued = false;
  let captureDirty = false;
  let captureChain = Promise.resolve();
  let currentKey = null;
  let generation = 0;
  let epoch;
  let lastKey = selectedKey();
  let changedConversation = false;

  function isSupportedPage() {
    return location.protocol === "https:" && !location.port &&
      ["chatgpt.com", "chat.openai.com"].includes(location.hostname) &&
      /\/(?:c|share)\/[^/?#]+/.test(location.pathname);
  }

  function selectedKey() {
    return isSupportedPage() ? Core.chatKey(location.href) : null;
  }

  function stillSelected(ticket) {
    return active && ticket.generation === generation && ticket.key === selectedKey();
  }

  async function storageCommand(type, fields = {}) {
    const response = await chrome.runtime.sendMessage({type, ...fields});
    if (!response?.ok) throw new Error(response?.error || "Archivio locale non disponibile.");
    return response.value;
  }

  async function performCapture(ticket) {
    if (!stillSelected(ticket)) return status();
    const saved = await storageCommand("storage-capture", {
      epoch: ticket.epoch, turns: ticket.turns, metadata: ticket.metadata
    });
    if (!saved && stillSelected(ticket)) invalidateCapture();
    return status();
  }

  function capture() {
    const key = selectedKey();
    if (!active || !key || key !== currentKey) {
      if (active) invalidateCapture();
      return status();
    }
    const capturedAt = new Date().toISOString();
    const url = new URL(location.href);
    url.search = "";
    url.hash = "";
    const ticket = {
      generation, key, epoch,
      turns: Core.captureDocument(document, url.href, capturedAt),
      metadata: {chatKey: key, url: url.href, title: document.title, capturedAt}
    };
    captureChain = captureChain.then(() => performCapture(ticket), () => performCapture(ticket));
    return captureChain;
  }

  function scheduleCapture() {
    if (!active) return;
    captureDirty = true;
    if (captureQueued) return;
    captureQueued = true;
    queueMicrotask(async () => {
      try {
        while (captureDirty && active) {
          captureDirty = false;
          await capture();
        }
      } catch (_) {
        invalidateCapture();
      } finally {
        captureQueued = false;
      }
    });
  }

  async function start() {
    if (!isSupportedPage()) throw new Error("Apri una conversazione ChatGPT con URL stabile e riprova.");
    const key = selectedKey();
    const loadedUrl = performance.getEntriesByType("navigation")[0]?.name;
    if (!loadedUrl || changedConversation || Core.chatKey(loadedUrl) !== key) {
      throw new Error("Ricarica la conversazione selezionata prima di avviare la cattura.");
    }
    const requestedGeneration = ++generation;
    const requestedEpoch = await storageCommand("storage-epoch");
    if (generation !== requestedGeneration || changedConversation || key !== selectedKey()) return status();
    epoch = requestedEpoch;
    currentKey = key;
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

  function invalidateCapture() {
    active = false;
    captureDirty = false;
    generation += 1;
    observer?.disconnect();
    observer = null;
  }

  function stop() {
    invalidateCapture();
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

  async function status() {
    const key = selectedKey();
    const items = await sessions();
    const selected = items.find(item => item.chatKey === key);
    return {active: active && key === currentKey, currentKey: key,
      currentTurns: selected?.turns.length || 0, ...counts(items)};
  }

  async function clearAll() {
    invalidateCapture();
    await storageCommand("storage-clear");
    return status();
  }

  function checkNavigation() {
    const key = selectedKey();
    if (lastKey !== key) {
      changedConversation = true;
      lastKey = key;
      invalidateCapture();
    }
  }

  window.navigation?.addEventListener("currententrychange", checkNavigation);
  window.addEventListener("popstate", checkNavigation);
  window.addEventListener("pagehide", invalidateCapture);
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area === "local" && changes["chatgpt-passive-export:epoch"] && active &&
        changes["chatgpt-passive-export:epoch"].newValue !== epoch) invalidateCapture();
  });

  chrome.runtime.onMessage.addListener((message, sender, respond) => {
    if (sender.id !== chrome.runtime.id || sender.url !== chrome.runtime.getURL("popup.html")) return false;
    const operations = new Map([["start", start], ["stop", stop], ["status", status],
      ["export", capture], ["clear", clearAll]]);
    const operation = operations.get(message?.type);
    if (!operation) return false;
    Promise.resolve().then(() => {
      if (message.type !== "status" && message.key !== selectedKey()) {
        throw new Error("La conversazione è cambiata.");
      }
      return operation();
    }).then((value) => respond({ok: true, value}), (error) =>
      respond({ok: false, error: error.message || String(error)}));
    return true;
  });

  globalThis.__chatgptPassiveExport = {version: 1};
}());
