import json
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from researchos.research.models import Paper, PaperProvenance, SearchCompleteness
from researchos.research.providers.crossref import CrossrefProvider
from researchos.research.providers.openalex import OpenAlexProvider
from researchos.research.search import deduplicate_papers, search_literature
from researchos.storage.database import save_run


class FakeProvider:
    def __init__(self, name, papers=None, error=None):
        self.name, self.papers, self.error = name, papers or [], error

    def search(self, query, limit=10):
        if self.error:
            raise RuntimeError(self.error)
        return self.papers[:limit]


def paper(provider, identifier, doi=None, title="A title"):
    return Paper(identifier, title, 2024, ["A. Author"], "Journal", doi,
                 f"https://example.org/{identifier}", source=provider,
                 provenance=[PaperProvenance(provider, identifier, f"https://example.org/{identifier}", datetime.now(timezone.utc))])


class KnowledgeEngineTests(unittest.TestCase):
    def test_openalex_adapter_uses_shared_contract(self):
        payload = {"results": [{"id": "https://openalex.org/W1", "title": "A title",
                                "publication_year": 2024, "doi": "https://doi.org/10/x",
                                "authorships": [], "primary_location": {},
                                "abstract_inverted_index": None}]}
        with patch("researchos.research.literature_search.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value = json_response(payload)
            found = OpenAlexProvider().search("topic", 3)
        self.assertEqual(found[0].title, "A title")
        self.assertEqual(found[0].provenance[0].provider, "OpenAlex")

    def test_crossref_adapter_uses_shared_contract(self):
        payload = {"message": {"items": [{"DOI": "10/example", "URL": "https://api.crossref.org/works/10.example",
                                           "title": ["A title"], "author": [{"given": "Ada", "family": "Lovelace"}],
                                           "published-print": {"date-parts": [[2023]]},
                                           "container-title": ["Journal"], "abstract": "<p>Text</p>"}]}}
        with patch("researchos.research.providers.crossref.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value = json_response(payload)
            found = CrossrefProvider().search("topic", 3)
        self.assertIsInstance(found[0], Paper)
        self.assertEqual(found[0].authors, ["Ada Lovelace"])
        self.assertEqual(found[0].abstract, "Text")
        self.assertEqual(found[0].provenance[0].source_url, "https://api.crossref.org/works/10.example")

    def test_deduplicates_normalized_doi_and_retains_all_provenance(self):
        result = deduplicate_papers([
            paper("OpenAlex", "W1", "https://doi.org/10/ABC"),
            paper("Crossref", "10/abc", "doi:10/abc"),
        ])
        self.assertEqual(len(result), 1)
        self.assertEqual({p.provider for p in result[0].provenance}, {"OpenAlex", "Crossref"})
        self.assertEqual({p.provider_id for p in result[0].provenance}, {"W1", "10/abc"})
        self.assertEqual({p.source_url for p in result[0].provenance}, {"https://example.org/W1", "https://example.org/10/abc"})

    def test_missing_or_different_dois_do_not_merge_same_title(self):
        papers = deduplicate_papers([
            paper("A", "1", None, "Same title"), paper("B", "2", None, "Same title"),
            paper("C", "3", "10/one", "Same title"), paper("D", "4", "10/two", "Same title"),
        ])
        self.assertEqual(len(papers), 4)

    def test_same_provider_identifier_with_distinct_urls_keeps_each_provenance(self):
        first = paper("Provider", "same-id", "10/one")
        second = paper("Provider", "same-id", "10/one")
        second.provenance[0] = PaperProvenance("Provider", "same-id", "https://alternate.example/record", datetime.now(timezone.utc))
        merged = deduplicate_papers([first, second])
        self.assertEqual(len(merged), 1)
        self.assertEqual({p.source_url for p in merged[0].provenance}, {"https://example.org/same-id", "https://alternate.example/record"})

    def test_partial_and_all_provider_failures_are_recorded(self):
        partial = search_literature("topic", providers=[FakeProvider("ok", [paper("ok", "1")]), FakeProvider("down", error="offline")])
        self.assertEqual(partial.completeness, SearchCompleteness.PARTIAL)
        self.assertEqual(partial.provider_outcomes[1].error, "offline")
        failed = search_literature("topic", providers=[FakeProvider("down", error="offline")])
        self.assertEqual(failed.completeness, SearchCompleteness.FAILED)
        self.assertEqual(failed.papers, [])
        empty = search_literature("topic", providers=[FakeProvider("empty")])
        self.assertEqual(empty.completeness, SearchCompleteness.COMPLETE)
        self.assertEqual(empty.papers, [])

    def test_run_record_persists_provider_outcomes_and_search_state(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            _, payload = save_run({"topic": "T", "research_field": "CS", "output_format": "markdown"}, [], directory,
                                  provider_outcomes=[{"provider": "OpenAlex", "status": "failed", "error": "offline"}],
                                  search_completeness="failed")
            record = json.loads((__import__("pathlib").Path(directory) / payload["run_id"] / "run.json").read_text())
            self.assertEqual(record["provider_outcomes"][0]["provider"], "OpenAlex")
            self.assertEqual(record["workflow"][1]["status"], "failed")

    def test_orchestrator_persists_aggregated_provenance_and_keeps_brief(self):
        import tempfile
        from unittest.mock import patch
        from researchos.core.orchestrator import run_research
        from researchos.core.request import create_research_request
        from researchos.research.models import ProviderOutcome, ResearchSearchResult

        selected = paper("OpenAlex", "W1", "10/example")
        selected.provenance.append(PaperProvenance("Crossref", "10/example", "https://crossref.example/10", datetime.now(timezone.utc)))
        aggregate = ResearchSearchResult([selected], [ProviderOutcome("OpenAlex", "succeeded", 1),
                                                        ProviderOutcome("Crossref", "failed", error="offline")],
                                         1, datetime.now(timezone.utc), SearchCompleteness.PARTIAL)
        with tempfile.TemporaryDirectory() as directory, patch("researchos.core.orchestrator.search_literature", return_value=aggregate):
            run = run_research(create_research_request("topic", "field"), directory)
            record = json.loads((run.directory / "run.json").read_text())
            brief = run.report_path.read_text()
        self.assertEqual(run.source_count, 1)
        self.assertEqual(record["search_completeness"], "partial")
        self.assertEqual(len(record["works"][0]["provenance"]), 2)
        self.assertEqual(record["workflow"][1]["status"], "partial")
        self.assertIn("Candidate literature", brief)
        self.assertIn("partial", brief.lower())

    def test_cli_no_search_still_creates_markdown_run(self):
        import contextlib
        import io
        import tempfile
        from researchos.main import main

        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()) as output:
            code = main(["run", "--topic", "topic", "--field", "field", "--no-search", "--output-dir", directory])
        self.assertEqual(code, 0)
        self.assertIn("Candidate sources: 0", output.getvalue())


def json_response(payload):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(payload).encode()

    # json.load expects a file-like read method.
    import io
    return io.BytesIO(json.dumps(payload).encode())


if __name__ == "__main__":
    unittest.main()
