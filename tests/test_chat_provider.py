import json
from io import BytesIO
from urllib.error import HTTPError
import unittest
from unittest.mock import patch

from researchos.agents.chat import OpenAICompatibleChatProvider
from researchos.knowledge.extraction.claim_extractor import ClaimExtractionConfig


class ChatProviderTests(unittest.TestCase):
    def setUp(self):
        self.config = ClaimExtractionConfig("openai_compatible", "gemini-test", "secret",
                                            "https://example.test/v1", timeout=1)
        self.provider = OpenAICompatibleChatProvider(self.config)

    def test_chat_retries_transient_service_unavailable(self):
        body = json.dumps({"choices": [{"message": {"content": "Ready"}}]}).encode()
        unavailable = HTTPError("https://example.test/v1/chat/completions", 503, "Service Unavailable", {}, BytesIO(b""))
        with patch("researchos.agents.chat.urlopen", side_effect=[unavailable, BytesIO(body)]) as open_url, \
             patch("researchos.agents.chat.time.sleep") as sleep:
            result = self.provider.respond([{"role": "user", "content": "Hello"}])
        self.assertEqual(result, "Ready")
        self.assertEqual(open_url.call_count, 2)
        sleep.assert_called_once_with(0.5)

    def test_chat_does_not_retry_non_transient_provider_errors(self):
        missing = HTTPError("https://example.test/v1/chat/completions", 404, "Not Found", {}, BytesIO(b""))
        with patch("researchos.agents.chat.urlopen", side_effect=missing) as open_url:
            with self.assertRaisesRegex(RuntimeError, "HTTP Error 404"):
                self.provider.respond([{"role": "user", "content": "Hello"}])
        open_url.assert_called_once()


if __name__ == "__main__":
    unittest.main()
