"use strict";
const root = document.getElementById("conversation");
const toggle = document.getElementById("reason-toggle");
toggle.addEventListener("click", () => {
  toggle.setAttribute("aria-expanded", "true");
  document.getElementById("reason").hidden = false;
});

window.runFixture = () => {
  const capturedAt = "2026-10-17T00:00:00.000Z";
  const before = PassiveExportCore.captureDocument(document, location.href, capturedAt);
  toggle.click(); // test-driver gesture; extension runtime contains no click automation
  const after = PassiveExportCore.captureDocument(document, location.href, capturedAt);
  const metadata = {chatKey: "fixture", url: location.href, title: document.title, capturedAt};
  let session = PassiveExportCore.mergeCapture(null, before, metadata);
  session = PassiveExportCore.mergeCapture(session, after, {...metadata, capturedAt: "2026-10-17T00:00:01.000Z"});
  history.pushState({}, "", "/c/second-synthetic-chat");
  const secondKey = PassiveExportCore.chatKey(location.href);
  return {
    before, after, session, secondKey,
    nestedKey: PassiveExportCore.chatKey(`${location.origin}/g/synthetic/c/nested-chat`),
    markdown: PassiveExportCore.sessionMarkdown(session),
    resources: performance.getEntriesByType("resource").map((entry) => entry.name)
  };
};
