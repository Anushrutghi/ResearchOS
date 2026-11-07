"""Provider contract for academic metadata search."""

from typing import Protocol

from researchos.research.models import Paper


class LiteratureProvider(Protocol):
    name: str

    def search(self, query: str, limit: int = 10) -> list[Paper]: ...
