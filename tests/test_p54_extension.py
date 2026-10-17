from __future__ import annotations

import http.server
import json
import subprocess
import threading
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
EXTENSION = ROOT / "extension" / "chatgpt-passive-export"
FIXTURE = ROOT / "tests" / "fixtures" / "p54"


class RecordingHandler(http.server.SimpleHTTPRequestHandler):
    requests: list[str] = []

    def do_GET(self) -> None:
        self.requests.append(self.path)
        files = {
            "/fixture.html": (FIXTURE / "fixture.html", "text/html; charset=utf-8"),
            "/fixture.js": (FIXTURE / "fixture.js", "text/javascript; charset=utf-8"),
            "/extension/core.js": (EXTENSION / "core.js", "text/javascript; charset=utf-8"),
        }
        selected = files.get(self.path)
        if not selected:
            self.send_error(404)
            return
        body = selected[0].read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", selected[1])
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        pass


class P54ExtensionTests(unittest.TestCase):
    def test_manifest_has_only_explicit_tab_local_permissions(self) -> None:
        manifest = json.loads((EXTENSION / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["manifest_version"], 3)
        self.assertEqual(manifest["permissions"], ["activeTab", "scripting", "storage"])
        self.assertNotIn("host_permissions", manifest)
        self.assertNotIn("background", manifest)
        self.assertNotIn("content_scripts", manifest)

    def test_runtime_has_no_network_or_automatic_gesture_apis(self) -> None:
        runtime = "\n".join(path.read_text(encoding="utf-8") for path in EXTENSION.glob("*.js"))
        for forbidden in (
            "fetch(", "XMLHttpRequest", "WebSocket", "sendBeacon", "chrome.cookies",
            ".scrollTo(", ".scrollIntoView(", "location.reload(", "window.open("
        ):
            self.assertNotIn(forbidden, runtime)
        content = (EXTENSION / "content.js").read_text(encoding="utf-8")
        self.assertNotIn("button.click()", content)
        self.assertNotIn("element.click()", content)
        self.assertIn("anchor.click()", content)  # local Blob download only
        self.assertIn("MutationObserver", runtime)
        self.assertIn("chrome.storage.local", runtime)

    def test_isolated_chrome_capture_formats_versions_and_adds_no_http_request(self) -> None:
        RecordingHandler.requests = []
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), RecordingHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/fixture.html"
            completed = subprocess.run(
                ["node", str(ROOT / "tests" / "p54_browser_probe.mjs"), "--url", url],
                check=False, capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(completed.stdout)
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()

        requested = list(RecordingHandler.requests)
        self.assertEqual(requested.count("/fixture.html"), 1, requested)
        self.assertEqual(requested.count("/fixture.js"), 1, requested)
        self.assertEqual(requested.count("/extension/core.js"), 1, requested)
        self.assertNotIn("/never-requested.png", requested)
        self.assertEqual(len(result["before"]), 2)
        self.assertEqual(len(result["after"]), 2)
        assistant = result["session"]["turns"][1]
        self.assertEqual(len(assistant["versions"]), 2)
        self.assertNotIn("Passaggio visualizzato", assistant["versions"][0]["markdown"])
        self.assertIn("Passaggio visualizzato", assistant["versions"][1]["markdown"])
        self.assertIn("```python", result["markdown"])
        self.assertIn("| Campo | Valore |", result["markdown"])
        self.assertIn("> Citazione sintetica", result["markdown"])
        self.assertTrue(result["secondKey"].endswith("/c/second-synthetic-chat"))
        self.assertTrue(result["nestedKey"].endswith("/c/nested-chat"))
        self.assertFalse(any(path.endswith("never-requested.png") for path in result["resources"]))


if __name__ == "__main__":
    unittest.main()
