(function () {
  "use strict";

  const Core = globalThis.PassiveExportCore;
  const statusNode = document.getElementById("status");
  const startButton = document.getElementById("start");
  const stopButton = document.getElementById("stop");
  const exportButton = document.getElementById("export");
  const clearButton = document.getElementById("clear");

  async function activeTab() {
    const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
    if (!tab?.id || !/^https:\/\/(chatgpt\.com|chat\.openai\.com)\/.*\/(c|share)\/[^/?#]+/.test(tab.url || "") &&
        !/^https:\/\/(chatgpt\.com|chat\.openai\.com)\/(c|share)\/[^/?#]+/.test(tab.url || "")) {
      throw new Error("Apri e seleziona una conversazione ChatGPT con URL stabile.");
    }
    return tab;
  }

  async function ensureInjected(tabId) {
    try {
      await chrome.tabs.sendMessage(tabId, {type: "status"});
    } catch (_) {
      await chrome.scripting.executeScript({target: {tabId}, files: ["core.js", "content.js"]});
    }
  }

  function download(name, type, body) {
    const url = URL.createObjectURL(new Blob([body], {type}));
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = name;
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  async function exportSession(tab, key) {
    const id = "chatgpt-passive-export:v1:" + key;
    const session = (await chrome.storage.local.get(id))[id];
    const current = await activeTab();
    if (current.id !== tab.id || Core.chatKey(current.url) !== key || session?.chatKey !== key) {
      throw new Error("La scheda o la conversazione è cambiata.");
    }
    const slug = (session.title || "chatgpt-chat").toLowerCase()
      .replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 64) || "chatgpt-chat";
    const url = new URL(session.url);
    url.search = "";
    url.hash = "";
    const turns = session.turns.map((turn) => ({
      ...turn,
      versions: turn.versions.filter((version) => version.captureFormat === 2)
    })).filter((turn) => turn.versions.length);
    if (!turns.length) {
      throw new Error("Questa conversazione contiene solo catture precedenti. Avvia una nuova cattura per esportarla.");
    }
    const exported = {...session, url: url.href, turns};
    download(`${slug}.md`, "text/markdown;charset=utf-8", Core.sessionMarkdown(exported));
    download(`${slug}.json`, "application/json;charset=utf-8", JSON.stringify(exported, null, 2) + "\n");
  }

  async function command(type) {
    const tab = await activeTab();
    const key = Core.chatKey(tab.url);
    await ensureInjected(tab.id);
    const response = await chrome.tabs.sendMessage(tab.id, {type, key});
    if (!response?.ok) throw new Error(response?.error || "Operazione non riuscita.");
    if (type === "export") {
      if (response.value.currentKey !== key) throw new Error("La conversazione è cambiata.");
      await exportSession(tab, key);
    }
    render(response.value);
  }

  function render(value) {
    const data = value || {active: false, chats: 0, turns: 0, versions: 0};
    document.getElementById("chat-count").textContent = data.chats || 0;
    document.getElementById("turn-count").textContent = data.turns || 0;
    document.getElementById("version-count").textContent = data.versions || 0;
    startButton.disabled = Boolean(data.active);
    stopButton.disabled = !data.active;
    exportButton.disabled = !(data.currentTurns > 0);
    statusNode.textContent = data.active ? "Cattura attiva nella scheda selezionata." : "Cattura ferma.";
  }

  async function run(type) {
    statusNode.textContent = "Operazione in corso…";
    try {
      await command(type);
    } catch (error) {
      statusNode.textContent = error.message || String(error);
    }
  }

  startButton.addEventListener("click", () => run("start"));
  stopButton.addEventListener("click", () => run("stop"));
  exportButton.addEventListener("click", () => run("export"));
  clearButton.addEventListener("click", () => {
    if (confirm("Eliminare tutte le catture locali create dall’estensione?")) run("clear");
  });
  run("status");
}());
