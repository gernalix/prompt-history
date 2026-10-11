(function () {
  "use strict";

  const REASONING_NAME = /(^|\s)(ragionamento|reasoning)(\s|$)/i;

  function rendered(element) {
    return !element.hidden && element.getAttribute("aria-hidden") !== "true" &&
      Boolean(element.getClientRects().length);
  }

  function turnFor(element) {
    return element.closest("[data-message-author-role='assistant']");
  }

  function toggleName(element) {
    return [element.getAttribute("aria-label"), element.getAttribute("title"), element.innerText]
      .filter(Boolean).join(" ").trim();
  }

  function reasoningToggles(root) {
    return [...root.querySelectorAll("button[aria-expanded], [role='button'][aria-expanded]")]
      .filter((element) => turnFor(element) && REASONING_NAME.test(toggleName(element)))
      .filter((element) => {
        const controlled = element.getAttribute("aria-controls");
        if (!controlled) return true;
        const panel = document.getElementById(controlled);
        return !panel || turnFor(panel) === turnFor(element);
      });
  }

  function waitForQuiet(root, quietMs, deadlineMs) {
    return new Promise((resolve) => {
      let quietTimer;
      const finish = () => {
        observer.disconnect();
        clearTimeout(quietTimer);
        clearTimeout(deadlineTimer);
        resolve();
      };
      const armQuiet = () => {
        clearTimeout(quietTimer);
        quietTimer = setTimeout(finish, quietMs);
      };
      const observer = new MutationObserver(armQuiet);
      observer.observe(root, {subtree: true, childList: true, attributes: true, characterData: true});
      const deadlineTimer = setTimeout(finish, deadlineMs);
      armQuiet();
    });
  }

  async function expandReasoning(root, options = {}) {
    const maxToggles = options.maxToggles || 128;
    const maxPasses = options.maxPasses || 8;
    const attempted = new WeakSet();
    let clicked = 0;

    for (let pass = 0; pass < maxPasses; pass += 1) {
      const collapsed = reasoningToggles(root).filter((element) =>
        element.getAttribute("aria-expanded") === "false" && !attempted.has(element));
      if (!collapsed.length) break;
      if (clicked + collapsed.length > maxToggles) throw new Error("reasoning toggle bound exceeded");
      for (const element of collapsed) {
        attempted.add(element);
        element.click();
        clicked += 1;
      }
      await waitForQuiet(root, 80, 1000);
    }

    const unresolved = reasoningToggles(root).filter((element) =>
      element.getAttribute("aria-expanded") === "false").length;
    return {clicked, discovered: reasoningToggles(root).length, unresolved};
  }

  function inline(node) {
    if (node.nodeType === Node.TEXT_NODE) return node.textContent;
    if (node.nodeType !== Node.ELEMENT_NODE || !rendered(node)) return "";
    const text = [...node.childNodes].map(inline).join("");
    if (node.matches("strong,b")) return `**${text}**`;
    if (node.matches("em,i")) return `*${text}*`;
    if (node.matches("code") && !node.closest("pre")) return `\`${text}\``;
    if (node.matches("a[href]")) return `[${text || node.href}](${node.getAttribute("href")})`;
    if (node.matches("img")) return `![${node.alt || "media"}](${node.getAttribute("src")})`;
    if (node.matches("br")) return "\n";
    return text;
  }

  function blocks(container) {
    const lines = [];
    for (const node of container.children) {
      if (!rendered(node) || node.matches("button[aria-expanded],[role='button'][aria-expanded]")) continue;
      if (node.matches("pre")) {
        const code = node.querySelector("code");
        const language = (code?.className.match(/language-([\w+-]+)/) || [])[1] || "";
        lines.push(`\`\`\`${language}\n${(code || node).innerText}\n\`\`\``);
      } else if (node.matches("ul,ol")) {
        [...node.children].filter(rendered).forEach((item, index) =>
          lines.push(`${node.matches("ol") ? `${index + 1}.` : "-"} ${inline(item).trim()}`));
      } else if (node.matches("table")) {
        const rows = [...node.rows].filter(rendered).map((row) =>
          [...row.cells].map((cell) => inline(cell).trim()));
        if (rows.length) {
          lines.push(`| ${rows[0].join(" | ")} |`);
          lines.push(`| ${rows[0].map(() => "---").join(" | ")} |`);
          rows.slice(1).forEach((row) => lines.push(`| ${row.join(" | ")} |`));
        }
      } else if (node.matches("[data-reasoning-panel]")) {
        lines.push(`### Ragionamento\n\n${blocks(node)}`);
      } else {
        const text = inline(node).trim();
        if (text) lines.push(text);
      }
    }
    return lines.join("\n\n");
  }

  function hash(text) {
    let value = 2166136261;
    for (const char of text) value = Math.imul(value ^ char.charCodeAt(0), 16777619);
    return (value >>> 0).toString(16);
  }

  function exportMarkdown(root) {
    const seen = new Set();
    const output = [];
    for (const turn of root.querySelectorAll("[data-message-author-role]")) {
      if (!rendered(turn)) continue;
      const role = turn.getAttribute("data-message-author-role");
      const body = blocks(turn).trim();
      const key = turn.getAttribute("data-message-id") || `${role}:${hash(body)}`;
      if (!body || seen.has(key)) continue;
      seen.add(key);
      output.push(`## ${role === "assistant" ? "Assistant" : "User"}\n\n${body}`);
    }
    return output.join("\n\n");
  }

  window.P53 = {reasoningToggles, expandReasoning, exportMarkdown};
}());
