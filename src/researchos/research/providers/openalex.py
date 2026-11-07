"""OpenAlex adapter implementing the shared literature provider contract."""

from researchos.research.literature_search import search_openalex
from researchos.research.models import Paper


class OpenAlexProvider:
    name = "OpenAlex"

    def __init__(self, timeout: float = 20.0):
        self.timeout = timeout

    def search(self, query: str, limit: int = 10) -> list[Paper]:
        return search_openalex(query, limit, self.timeout)
