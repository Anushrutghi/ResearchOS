"""Evidence records keep provenance and epistemic status explicit."""

from dataclasses import asdict, dataclass
from typing import Literal

EvidenceStatus = Literal["discovered", "reviewed", "supports", "contradicts", "uncertain"]


@dataclass(frozen=True)
class EvidenceRecord:
    claim: str
    source_id: str
    status: EvidenceStatus = "discovered"
    notes: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)
