from __future__ import annotations

import unittest
from pathlib import Path


class P53ResearchFixtureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).parents[1] / "research" / "p53"
        self.script = (self.root / "poc.js").read_text(encoding="utf-8")
        self.fixture = (self.root / "fixture.html").read_text(encoding="utf-8")

    def test_poc_is_non_installable_and_network_free(self) -> None:
        self.assertFalse((self.root / "manifest.json").exists())
        for forbidden in ("fetch(", "XMLHttpRequest", "WebSocket", "sendBeacon", "chrome.cookies"):
            self.assertNotIn(forbidden, self.script)
            self.assertNotIn(forbidden, self.fixture)

    def test_fixture_contains_only_synthetic_content_and_required_shapes(self) -> None:
        self.assertIn("Domanda sintetica", self.fixture)
        self.assertIn('aria-expanded="false"', self.fixture)
        self.assertIn("data-reasoning-panel", self.fixture)
        self.assertIn("<pre><code", self.fixture)
        self.assertIn("<ul>", self.fixture)
        self.assertIn("<table>", self.fixture)
        self.assertIn("<img", self.fixture)

    def test_poc_has_bounds_quiescence_visibility_and_deduplication(self) -> None:
        for required in (
            "maxToggles", "maxPasses", "MutationObserver", "getClientRects",
            "data-message-id", "seen.has", "aria-expanded", "aria-controls",
        ):
            self.assertIn(required, self.script)


if __name__ == "__main__":
    unittest.main()
