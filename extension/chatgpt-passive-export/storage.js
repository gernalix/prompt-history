"use strict";

importScripts("core.js");

const SESSION_PREFIX = "chatgpt-passive-export:v1:";
const EPOCH_KEY = "chatgpt-passive-export:epoch";
let pending = Promise.resolve();

async function epoch() {
  return (await chrome.storage.local.get(EPOCH_KEY))[EPOCH_KEY] || 0;
}

async function operate(message, key) {
  const current = await epoch();
  if (message.type === "storage-epoch") return current;
  if (message.type === "storage-clear") {
    await chrome.storage.local.set({[EPOCH_KEY]: current + 1});
    const all = await chrome.storage.local.get(null);
    await chrome.storage.local.remove(Object.keys(all).filter(id => id.startsWith(SESSION_PREFIX)));
    return true;
  }
  if (message.epoch !== current || message.metadata?.chatKey !== key) return false;
  const id = SESSION_PREFIX + key;
  const previous = (await chrome.storage.local.get(id))[id];
  const session = PassiveExportCore.mergeCapture(previous, message.turns, message.metadata);
  await chrome.storage.local.set({[id]: session});
  return true;
}

chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (sender.id !== chrome.runtime.id || sender.frameId !== 0 ||
      !/^https:\/\/(chatgpt\.com|chat\.openai\.com)\//.test(sender.url || "") ||
      !["storage-epoch", "storage-clear", "storage-capture"].includes(message?.type)) return false;
  const key = PassiveExportCore.chatKey(sender.url);
  pending = pending.then(() => operate(message, key)).then(
    value => respond({ok: true, value}),
    error => respond({ok: false, error: error.message || String(error)})
  ).catch(() => {});
  return true;
});
