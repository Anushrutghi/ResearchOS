"""Typed v0.4 research problem and study design records."""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import uuid


@dataclass(frozen=True)
class ResearchProblemAnalysis:
    topic: str
    research_field: str
    domain_pack: str
    research_type: str | None
    problem_statement: str | None
    objectives: list[str] = field(default_factory=list)
    research_questions: list[str] = field(default_factory=list)
    missing_information: list[str] = field(default_factory=list)
    recommendations: list[dict] = field(default_factory=list)
    feasibility_checks: list[dict] = field(default_factory=list)
    cautions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class StudyDesign:
    title: str
    research_field: str
    research_type: str
    problem_statement: str | None = None
    objectives: list[str] = field(default_factory=list)
    research_questions: list[str] = field(default_factory=list)
    hypotheses: list[str] = field(default_factory=list)
    methodology: str | None = None
    methodology_rationale: str | None = None
    population_or_material: str | None = None
    sample_size: str | None = None
    sampling_method: str | None = None
    variables: dict = field(default_factory=dict)
    conditions: list[str] = field(default_factory=list)
    data_collection: str | None = None
    analysis_plan: str | None = None
    evaluation_metrics: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    ethics_notes: str | None = None
    evidence_claim_ids: list[str] = field(default_factory=list)
    design_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    status: str = "draft"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    reviewer_note: str | None = None
    approved_at: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)
