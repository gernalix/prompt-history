(function (global) {
  "use strict";

  const TURN_SELECTOR = [
    "[data-message-author-role]",
    "[data-testid^='conversation-turn-']"
  ].join(",");
  const SKIP_SELECTOR = "button,[role='button'],script,style,noscript,template,svg";

  function hash(text) {
    let value = 2166136261;
    for (const char of text) value = Math.imul(value ^ char.charCodeAt(0), 16777619);
    return (value >>> 0).toString(16).padStart(8, "0");
  }

  function chatKey(rawUrl) {
    const url = new URL(rawUrl);
    const match = url.pathname.match(/\/(c|share)\/([^/?#]+)/);
    return `${url.origin}${match ? `/${match[1]}/${match[2]}` : url.pathname}`;
  }

  function safeHref(raw, base) {
    if (!raw) return "";
    try {
      const url = new URL(raw, base);
      return ["http:", "https:", "data:", "blob:"].includes(url.protocol) ? url.href : "";
    } catch (_) {
      return "";
    }
  }

  function rendered(element) {
    if (!(element instanceof Element)) return true;
    if (element.hidden || element.getAttribute("aria-hidden") === "true") return false;
    const style = global.getComputedStyle ? global.getComputedStyle(element) : null;
    return !style || (style.display !== "none" && style.visibility !== "hidden");
  }

  function inline(node, baseUrl) {
    if (node.nodeType === Node.TEXT_NODE) return node.textContent || "";
    if (node.nodeType !== Node.ELEMENT_NODE || !rendered(node) || node.matches(SKIP_SELECTOR)) return "";
    if (node.matches("img")) {
      const source = safeHref(node.getAttribute("src") || node.getAttribute("data-src"), baseUrl);
      return source ? `![${node.getAttribute("alt") || "media"}](${source})` : "";
    }
    const text = [...node.childNodes].map((child) => inline(child, baseUrl)).join("");
    if (node.matches("strong,b")) return `**${text}**`;
    if (node.matches("em,i")) return `*${text}*`;
    if (node.matches("code") && !node.closest("pre")) return `\`${text}\``;
    if (node.matches("a[href]")) {
      const href = safeHref(node.getAttribute("href"), baseUrl);
      return href ? `[${text.trim() || href}](${href})` : text;
    }
    if (node.matches("br")) return "\n";
    return text;
  }

  function tableMarkdown(table, baseUrl) {
    const rows = [...table.rows].filter(rendered).map((row) =>
      [...row.cells].map((cell) => inline(cell, baseUrl).trim().replace(/\|/g, "\\|")));
    if (!rows.length) return "";
    const width = Math.max(...rows.map((row) => row.length));
    const normalized = rows.map((row) => [...row, ...Array(width - row.length).fill("")]);
    return [
      `| ${normalized[0].join(" | ")} |`,
      `| ${Array(width).fill("---").join(" | ")} |`,
      ...normalized.slice(1).map((row) => `| ${row.join(" | ")} |`)
    ].join("\n");
  }

  function listMarkdown(list, baseUrl, depth = 0) {
    return [...list.children].filter((item) => item.matches("li") && rendered(item)).map((item, index) => {
      const direct = [...item.childNodes].filter((node) => !(node instanceof Element && node.matches("ul,ol")));
      const prefix = list.matches("ol") ? `${index + 1}.` : "-";
      const line = `${"  ".repeat(depth)}${prefix} ${direct.map((node) => inline(node, baseUrl)).join("").trim()}`;
      const nested = [...item.children].filter((child) => child.matches("ul,ol") && rendered(child))
        .map((child) => listMarkdown(child, baseUrl, depth + 1)).filter(Boolean);
      return [line, ...nested].join("\n");
    }).join("\n");
  }

  function blocks(container, baseUrl) {
    const lines = [];
    for (const node of container.children) {
      if (!rendered(node) || node.matches(SKIP_SELECTOR)) continue;
      if (node.matches("pre")) {
        const code = node.querySelector("code");
        const language = (code?.className.match(/language-([\w+-]+)/) || [])[1] || "";
        lines.push(`\`\`\`${language}\n${(code || node).textContent || ""}\n\`\`\``);
      } else if (node.matches("ul,ol")) {
        lines.push(listMarkdown(node, baseUrl));
      } else if (node.matches("table")) {
        lines.push(tableMarkdown(node, baseUrl));
      } else if (node.matches("blockquote")) {
        lines.push(inline(node, baseUrl).trim().split("\n").map((line) => `> ${line}`).join("\n"));
      } else if (node.matches("p,h1,h2,h3,h4,h5,h6")) {
        const text = inline(node, baseUrl).trim();
        if (text) lines.push(text);
      } else {
        const nested = blocks(node, baseUrl).trim();
        const text = nested || inline(node, baseUrl).trim();
        if (text) lines.push(text);
      }
    }
    return lines.filter(Boolean).join("\n\n");
  }

  function roleFor(turn) {
    const explicit = turn.getAttribute("data-message-author-role");
    if (explicit) return explicit;
    const testId = turn.getAttribute("data-testid") || "";
    if (/user/i.test(testId)) return "user";
    return "assistant";
  }

  function turnIdentity(turn, index, role, markdown) {
    const owner = turn.closest("[data-message-id]");
    const stable = turn.getAttribute("data-message-id") || owner?.getAttribute("data-message-id") ||
      turn.getAttribute("data-testid");
    return stable ? `${role}:${stable}` : `${role}:position-${index}:${hash(markdown.slice(0, 256))}`;
  }

  function mediaReferences(turn, baseUrl) {
    return [...turn.querySelectorAll("img[src],img[data-src],video[src],audio[src],source[src],a[href]")].map((element) => {
      const attribute = element.matches("a") ? "href" : (element.hasAttribute("src") ? "src" : "data-src");
      return {
        type: element.tagName.toLowerCase(),
        url: safeHref(element.getAttribute(attribute), baseUrl),
        label: element.getAttribute("alt") || element.getAttribute("title") || element.textContent?.trim() || ""
      };
    }).filter((item) => item.url);
  }

  function captureDocument(document, rawUrl, capturedAt = new Date().toISOString()) {
    const turns = [...document.querySelectorAll(TURN_SELECTOR)].filter((turn) =>
      rendered(turn) && !turn.parentElement?.closest(TURN_SELECTOR));
    return turns.map((turn, index) => {
      const role = roleFor(turn);
      const markdown = blocks(turn, rawUrl).trim();
      if (!markdown) return null;
      return {
        key: turnIdentity(turn, index, role, markdown),
        role,
        capturedAt,
        markdown,
        text: turn.innerText?.trim() || turn.textContent?.trim() || "",
        media: mediaReferences(turn, rawUrl),
        contentHash: hash(`${role}\n${markdown}`)
      };
    }).filter(Boolean);
  }

  function mergeCapture(session, capturedTurns, metadata) {
    const next = session || {
      schemaVersion: 1,
      chatKey: metadata.chatKey,
      url: metadata.url,
      title: metadata.title,
      startedAt: metadata.capturedAt,
      updatedAt: metadata.capturedAt,
      gaps: ["Only content rendered in the DOM while capture was active is included."],
      turns: []
    };
    next.url = metadata.url;
    next.title = metadata.title;
    next.updatedAt = metadata.capturedAt;
    for (const captured of capturedTurns) {
      let turn = next.turns.find((candidate) => candidate.key === captured.key);
      if (!turn) {
        turn = {key: captured.key, role: captured.role, versions: []};
        next.turns.push(turn);
      }
      const latest = turn.versions.at(-1);
      if (!latest || latest.contentHash !== captured.contentHash) turn.versions.push(captured);
    }
    return next;
  }

  function sessionMarkdown(session) {
    const lines = [
      `# ${session.title || "ChatGPT conversation"}`,
      "",
      `- URL: ${session.url}`,
      `- Capture started: ${session.startedAt}`,
      `- Capture updated: ${session.updatedAt}`,
      "- Coverage: passive DOM capture; missing or never-rendered content is not included.",
      ""
    ];
    for (const turn of session.turns) {
      turn.versions.forEach((version, index) => {
        const revision = turn.versions.length > 1 ? ` — version ${index + 1}` : "";
        lines.push(`## ${turn.role === "user" ? "User" : "Assistant"}${revision}`, "", version.markdown, "");
      });
    }
    return lines.join("\n").trim() + "\n";
  }

  global.PassiveExportCore = Object.freeze({
    TURN_SELECTOR, blocks, captureDocument, chatKey, hash, mergeCapture, sessionMarkdown
  });
}(globalThis));
