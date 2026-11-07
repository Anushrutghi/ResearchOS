"""Validation and serialization for user research requests."""

from dataclasses import asdict, dataclass

SUPPORTED_FORMATS = ("pdf", "docx", "latex", "markdown")


@dataclass(frozen=True)
class ResearchRequest:
    topic: str
    research_field: str
    output_format: str = "markdown"

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def create_research_request(topic: str, research_field: str, output_format: str = "markdown") -> ResearchRequest:
    """Validate user input and return a normalized, immutable request."""
    if not isinstance(topic, str) or not topic.strip():
        raise ValueError("Research topic must not be empty.")
    if not isinstance(research_field, str) or not research_field.strip():
        raise ValueError("Research field must not be empty.")
    if not isinstance(output_format, str):
        raise ValueError("Output format must be a string.")
    normalized_format = output_format.strip().lower()
    if normalized_format not in SUPPORTED_FORMATS:
        choices = ", ".join(SUPPORTED_FORMATS)
        raise ValueError(f"Unsupported output format '{output_format}'. Choose one of: {choices}.")
    return ResearchRequest(topic=topic.strip(), research_field=research_field.strip(), output_format=normalized_format)
