"""Typed records for provisional research claims and a lightweight evidence graph."""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
import uuid


class EvidenceCategory(str, Enum):
    DIRECT_EXPERIMENTAL = "direct_experimental_evidence"
    OBSERVATIONAL = "observational_evidence"
    THEORETICAL = "theoretical_evidence"
    STATISTICAL = "statistical_evidence"
    LITERATURE_SUPPORTED = "literature_supported_finding"
    INTERPRETATION = "researcher_interpretation"
    HYPOTHESIS = "hypothesis"
    PROPOSED_EXPERIMENT = "proposed_experiment"
    UNVERIFIED = "unverified_claim"


class VerificationStatus(str, Enum):
    PROVISIONAL = "provisional"
    REVIEWED = "reviewed"
    VERIFIED = "verified"
    CONTESTED = "contested"
    UNVERIFIED = "unverified"


class NodeType(str, Enum):
    PAPER = "paper"
    DOCUMENT = "document"
    AUTHOR = "author"
    CLAIM = "claim"
    TOPIC = "topic"
    DATASET = "dataset"
    EXPERIMENT = "experiment"
    METHOD = "method"
    FINDING = "finding"


class Relationship(str, Enum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    REFERENCES = "references"
    USES_METHODOLOGY = "uses_methodology"
    EVALUATES = "evaluates"
    EXTENDS = "extends"
    REPRODUCES = "reproduces"
    UNCERTAIN = "uncertain"
    CHALLENGES = "challenges"
    DERIVED_FROM = "derived_from"
    DOCUMENT_VERSION_OF = "document_version_of"
    AUTHORED_BY = "authored_by"


@dataclass(frozen=True)
class SourcePassage:
    document_id: str
    page_number: int
    text: str
    section_name: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ResearchClaim:
    original_text: str
    source: SourcePassage
    evidence_category: EvidenceCategory = EvidenceCategory.UNVERIFIED
    verification_status: VerificationStatus = VerificationStatus.PROVISIONAL
    extraction_method: str = "researcher_entered"
    extracted_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    claim_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def to_dict(self) -> dict:
        data = asdict(self)
        data["evidence_category"] = self.evidence_category.value
        data["verification_status"] = self.verification_status.value
        data["extracted_at"] = self.extracted_at.astimezone(timezone.utc).isoformat()
        return data


@dataclass(frozen=True)
class KnowledgeNode:
    node_id: str
    node_type: NodeType
    label: str
    properties: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["node_type"] = self.node_type.value
        return data


@dataclass(frozen=True)
class KnowledgeEdge:
    source_id: str
    target_id: str
    relationship: Relationship
    status: str = "proposed"
    notes: str = ""
    edge_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def to_dict(self) -> dict:
        data = asdict(self)
        data["relationship"] = self.relationship.value
        return data
