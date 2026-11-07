"""A transparent, domain-neutral research workflow plan."""

from dataclasses import asdict, dataclass

@dataclass(frozen=True)
class WorkflowStage:
    name: str
    purpose: str
    status: str


def create_workflow_plan() -> list[WorkflowStage]:
    """Return workflow stage availability without implying automatic completion."""
    return [
        WorkflowStage("Request understanding", "Capture topic, field, and requested output.", "implemented"),
        WorkflowStage("Literature discovery", "Find candidate publications and preserve their source metadata.", "implemented"),
        WorkflowStage("Evidence synthesis", "Extract and assess claims against full source material.", "researcher_review_required"),
        WorkflowStage("Hypothesis and methodology", "Develop testable questions and field-appropriate methods.", "conditional_guidance_available"),
        WorkflowStage("Experiments and analysis", "Run reproducible experiments and analyze observed data.", "digest_pinned_docker_required"),
        WorkflowStage("Critical review and verification", "Challenge methods, calculations, sources, and conclusions.", "review_tools_available_no_truth_automation"),
        WorkflowStage("Scientific writing", "Produce a cited document from verified work products.", "researcher_approval_required"),
    ]


def workflow_as_dicts() -> list[dict[str, str]]:
    return [asdict(stage) for stage in create_workflow_plan()]
