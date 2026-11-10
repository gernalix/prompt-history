import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import {dirname, join} from "node:path";

const contentPath = process.argv[2];
const storage = new Map();
const listeners = [];
const popup = {id: "fixture-extension", url: "chrome-extension://fixture-extension/popup.html"};
const prefix = "chatgpt-passive-export:v1:";
const epochKey = "chatgpt-passive-export:epoch";
let worker;
let release;
let delayEpoch = true;
const local = {
  get: async key => {
    if (delayEpoch && key === epochKey) {
      delayEpoch = false;
      await new Promise(resolve => { release = resolve; });
    }
    return Object.fromEntries(structuredClone([...storage]));
  },
  set: async items => {
    const changes = {};
    for (const [key, value] of Object.entries(items)) {
      changes[key] = {oldValue: storage.get(key), newValue: value};
      storage.set(key, structuredClone(value));
    }
    for (const fn of listeners) fn(changes, "local");
  },
  remove: async keys => { for (const key of keys) storage.delete(key); },
};
const core = {
  chatKey: url => new URL(url).origin + new URL(url).pathname,
  captureDocument: document => [{key: document.title, versions: [], text: document.title}],
  mergeCapture: (previous, turns, metadata) => ({
    ...metadata, turns: [...(previous?.turns || []), ...turns],
  }),
};
vm.runInNewContext(fs.readFileSync(join(dirname(contentPath), "storage.js"), "utf8"), {
  importScripts() {},
  PassiveExportCore: core,
  chrome: {storage: {local}, runtime: {id: popup.id, onMessage: {addListener: fn => { worker = fn; }}}},
});

function tab(path, title) {
  const navigation = new Map();
  let listener;
  let mutate;
  const location = new URL(path, "https://chatgpt.com");
  const context = vm.createContext({
    URL, Date, queueMicrotask, location,
    document: {title, documentElement: {}},
    performance: {getEntriesByType: () => [{name: location.href}]},
    window: {
      navigation: {addEventListener: (type, fn) => navigation.set(type, fn)},
      addEventListener: (type, fn) => navigation.set(type, fn),
    },
    MutationObserver: class {
      constructor(fn) { mutate = fn; }
      observe() {}
      disconnect() {}
    },
    PassiveExportCore: core,
    chrome: {
      runtime: {
        id: popup.id, getURL: name => new URL(name, popup.url).href,
        onMessage: {addListener: fn => { listener = fn; }},
        sendMessage: message => new Promise(resolve => {
          const sender = {id: popup.id, frameId: 0, url: context.location.href};
          assert.equal(worker(message, sender, resolve), true);
        }),
      },
      storage: {local, onChanged: {addListener: fn => listeners.push(fn)}},
    },
  });
  vm.runInContext(fs.readFileSync(contentPath, "utf8"), context);
  return {
    context,
    listener,
    command: type => new Promise(resolve => {
      assert.equal(listener({type, key: context.location.href}, popup, resolve), true);
    }),
    navigate: path => {
      context.location = new URL(path, context.location);
      navigation.get("currententrychange")();
    },
    mutate: () => mutate(),
  };
}

const first = tab("/c/first", "Synthetic first");
assert.equal(first.listener({type: "start", key: first.context.location.href},
  {id: popup.id, url: first.context.location.href}, () => {}), false);
const starting = first.command("start");
await new Promise(setImmediate);
assert.equal(typeof release, "function");
first.navigate("/c/second");
release();
assert.equal((await starting).ok, true);
assert.equal(storage.size, 0);
assert.equal((await first.command("start")).ok, false);
assert.equal(storage.size, 0);

const second = tab("/c/second", "Synthetic second");
const concurrent = tab("/c/second", "Synthetic other tab");
await Promise.all([second.command("start"), concurrent.command("start")]);
const key = prefix + second.context.location.href;
assert.deepEqual(storage.get(key).turns.map(turn => turn.text), [
  "Synthetic second", "Synthetic other tab",
]);
assert.equal((await second.command("stop")).value.currentTurns, 2);
const stale = {
  type: "storage-capture", epoch: 0,
  metadata: {chatKey: concurrent.context.location.href}, turns: [{text: "Synthetic pending"}],
};
assert.equal((await second.command("clear")).ok, true);
assert.equal((await concurrent.command("status")).value.active, false);
concurrent.mutate();
const late = await new Promise(resolve => worker(stale,
  {id: popup.id, frameId: 0, url: concurrent.context.location.href}, resolve));
assert.equal(late.value, false);
assert.equal(storage.has(key), false);
assert.equal((await concurrent.command("start")).ok, true);
assert.equal(storage.get(key).turns.length, 1);
