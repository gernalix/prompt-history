import unittest

from prompt_history import adapters
from prompt_history import chatgpt_adapters


class ChatGPTAdapterBoundaryTests(unittest.TestCase):
    def test_public_imports_still_reexport_canonical_implementations(self):
        for name in (
            "_iso_from_epoch", "_content_text", "ingest_chatgpt_export",
            "ingest_chatgpt_exporter_archive", "link_explicit_prompt_ids",
            "_record_chatgpt_exporter_file",
        ):
            self.assertIs(getattr(adapters,name),getattr(chatgpt_adapters,name))
    def test_epoch_and_normalized_text_helpers(self):
        self.assertEqual(chatgpt_adapters._iso_from_epoch(1790000000000),"2026-09-21T14:13:20Z")
        self.assertEqual(chatgpt_adapters._normalized_parts_text([{"text":"one"},{"text":"two"}]),"one\ntwo")


if __name__=="__main__":
    unittest.main()
