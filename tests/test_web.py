import unittest

from researchos.web import _page, _render_result


class WebRenderingTests(unittest.TestCase):
    def test_home_page_has_research_form(self):
        page = _page().decode()
        self.assertIn("Start with a question", page)
        self.assertIn('name="topic"', page)
        self.assertIn("Markdown (available)", page)

    def test_result_page_escapes_source_text_and_links_to_markdown(self):
        request = {"topic": "T <script>", "research_field": "Computer Science", "output_format": "markdown"}
        payload = {
            "run_id": "20261105T165202Z-topic-d20ba173",
            "workflow": [{"name": "Request"}, {"name": "Literature", "status": "completed_no_results"}],
            "works": [{"title": "A <b>paper</b>", "url": "https://example.org/paper", "authors": ["A. Author"], "year": 2024}],
        }
        page = _render_result(request, payload)
        self.assertIn("T &lt;script&gt;", page)
        self.assertIn("A &lt;b&gt;paper&lt;/b&gt;", page)
        self.assertNotIn("A <b>paper</b>", page)
        self.assertIn("/runs/20261105T165202Z-topic-d20ba173/brief", page)


if __name__ == "__main__":
    unittest.main()
