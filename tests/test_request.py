import unittest

from researchos.core.request import create_research_request
from researchos import __version__


class ResearchRequestTests(unittest.TestCase):
    def test_release_version_is_v1(self):
        self.assertEqual(__version__, "1.0.0")

    def test_creates_normalized_request(self):
        request = create_research_request("  Federated Learning  ", " Computer Science ", "PDF")
        self.assertEqual(request.to_dict(), {
            "topic": "Federated Learning",
            "research_field": "Computer Science",
            "output_format": "pdf",
        })

    def test_rejects_empty_values(self):
        for values in [("", "CS", "pdf"), ("topic", "  ", "pdf")]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                create_research_request(*values)

    def test_rejects_unsupported_format(self):
        with self.assertRaises(ValueError):
            create_research_request("topic", "field", "html")


if __name__ == "__main__":
    unittest.main()
