import unittest

from researchos.research.literature_search import _abstract_from_inverted_index, _parse_work, search_openalex


class LiteratureTests(unittest.TestCase):
    def test_restores_inverted_abstract_order(self):
        self.assertEqual(_abstract_from_inverted_index({"world": [1], "Hello": [0]}), "Hello world")

    def test_parses_work_and_preserves_missing_abstract(self):
        work = _parse_work({"id": "https://openalex.org/W1", "title": "A paper", "publication_year": 2024,
                            "authorships": [{"author": {"display_name": "A. Researcher"}}],
                            "primary_location": {"source": {"display_name": "Journal"}}, "doi": None})
        self.assertEqual(work.title, "A paper")
        self.assertIsNone(work.abstract)
        self.assertEqual(work.authors, ["A. Researcher"])

    def test_validates_search_arguments_without_network(self):
        with self.assertRaises(ValueError):
            search_openalex(" ")
        with self.assertRaises(ValueError):
            search_openalex("valid", 101)


if __name__ == "__main__":
    unittest.main()
