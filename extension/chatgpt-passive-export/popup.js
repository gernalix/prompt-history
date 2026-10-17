(function () {
  "use strict";

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

  async function command(type) {
    const tab = await activeTab();
    await ensureInjected(tab.id);
    const response = await chrome.tabs.sendMessage(tab.id, {type});
    if (!response?.ok) throw new Error(response?.error || "Operazione non riuscita.");
    render(response.value);
  }

  function render(value) {
    const data = value || {active: false, chats: 0, turns: 0, versions: 0};
    document.getElementById("chat-count").textContent = data.chats || 0;
    document.getElementById("turn-count").textContent = data.turns || 0;
    document.getElementById("version-count").textContent = data.versions || 0;
    startButton.disabled = Boolean(data.active);
    stopButton.disabled = !data.active;
    exportButton.disabled = !(data.turns > 0);
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
