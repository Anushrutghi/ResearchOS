import json
import unittest
from unittest.mock import patch

from researchos.knowledge.extraction.claim_extractor import (
    ClaimExtractionConfig, OpenAICompatibleClaimExtractor,
)


class FakeResponse:
    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size=-1):
        return self.data[:size]


class ClaimExtractorTests(unittest.TestCase):
    def test_provider_is_disabled_without_complete_configuration(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertIsNone(ClaimExtractionConfig.from_environment())

    def test_provider_requires_openai_compatible_name_and_valid_base_url(self):
        with patch.dict("os.environ", {
            "RESEARCHOS_AI_PROVIDER": "openai", "RESEARCHOS_AI_MODEL": "m", "RESEARCHOS_API_KEY": "secret",
        }, clear=True):
            self.assertIsNone(ClaimExtractionConfig.from_environment())
        with patch.dict("os.environ", {
            "RESEARCHOS_AI_PROVIDER": "openai_compatible", "RESEARCHOS_AI_MODEL": "m",
            "RESEARCHOS_API_KEY": "secret", "RESEARCHOS_AI_BASE_URL": "http://api.example/v1",
        }, clear=True):
            self.assertIsNone(ClaimExtractionConfig.from_environment())
        with patch.dict("os.environ", {
            "RESEARCHOS_AI_PROVIDER": "openai_compatible", "RESEARCHOS_AI_MODEL": "m",
            "RESEARCHOS_API_KEY": "secret", "RESEARCHOS_AI_BASE_URL": "http://localhost:1234/v1",
        }, clear=True):
            self.assertEqual(ClaimExtractionConfig.from_environment().base_url, "http://localhost:1234/v1")
        with patch.dict("os.environ", {
            "RESEARCHOS_AI_PROVIDER": "openai_compatible", "RESEARCHOS_AI_MODEL": "m",
            "RESEARCHOS_API_KEY": "secret", "RESEARCHOS_AI_BASE_URL": "file:///tmp",
        }, clear=True):
            self.assertIsNone(ClaimExtractionConfig.from_environment())

    def test_extract_returns_provisional_source_grounded_candidates(self):
        body = {"choices": [{"message": {"content": json.dumps({"claims": [
            {"claim_text": "The intervention reduced error.", "evidence_category": "direct_experimental_evidence"},
            {"claim_text": "It may generalize.", "evidence_category": "invalid"},
        ]})}}]}
        config = ClaimExtractionConfig("openai_compatible", "test-model", "secret")
        with patch("researchos.knowledge.extraction.claim_extractor.urlopen", return_value=FakeResponse(json.dumps(body).encode())) as call:
            candidates = OpenAICompatibleClaimExtractor(config).extract("Source page text")
        self.assertEqual(candidates[0]["verification_status"], "provisional")
        self.assertEqual(candidates[0]["extraction_method"], "llm:openai_compatible/test-model")
        self.assertEqual(candidates[1]["evidence_category"], "unverified_claim")
        request = call.call_args.args[0]
        payload = json.loads(request.data)
        self.assertIn("untrusted data", payload["messages"][0]["content"])
        self.assertEqual(payload["messages"][1]["content"], "Source page text")
        self.assertNotIn("secret", json.dumps(candidates))

    def test_invalid_input_is_rejected_before_provider_request(self):
        extractor = OpenAICompatibleClaimExtractor(ClaimExtractionConfig("openai_compatible", "m", "k"))
        with patch("researchos.knowledge.extraction.claim_extractor.urlopen") as call:
            with self.assertRaises(ValueError):
                extractor.extract(" ")
            call.assert_not_called()

    def test_malformed_provider_output_is_reported(self):
        with patch("researchos.knowledge.extraction.claim_extractor.urlopen", return_value=FakeResponse(b"not-json")):
            with self.assertRaisesRegex(RuntimeError, "provider failed"):
                OpenAICompatibleClaimExtractor(ClaimExtractionConfig("openai_compatible", "m", "k")).extract("text")


if __name__ == "__main__":
    unittest.main()
