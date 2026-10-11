# P53 isolated feasibility fixture

This directory is a synthetic, non-installable proof of concept. It does not
contain an MV3 manifest, make network requests, access cookies/storage, or use
ChatGPT data.

`fixture.html` models two assistant turns with collapsed reasoning summaries.
One panel already exists in the DOM; the other is mounted by the local click
handler. `poc.js` scopes semantic toggles to assistant turns, expands them with
bounded/idempotent passes, waits for DOM quiescence, and exports only rendered
content to Markdown.

The fixture exposes `window.runP53()` and stores its result in `#result`. It is
intended to be served from a loopback-only static HTTP server so browser network
events can compare the settled baseline with the expansion interval.
