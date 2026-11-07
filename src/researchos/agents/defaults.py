"""Built-in no-credential agents for the local ResearchOS workflow runtime."""

from researchos.agents.runtime import AgentDefinition, AgentRegistry
from researchos.agents.providers import OpenAICompatibleAgentProvider
from researchos.knowledge.extraction.claim_extractor import ClaimExtractionConfig
from researchos.methodology.analyzer import RESEARCH_TYPES, analyze_research_problem


QUESTION_INPUT = {"type": "object", "properties": {"question": {"type": "string", "maxLength": 1000}},
                  "required": ["question"], "additionalProperties": False}
PLAN_OUTPUT = {"type": "object", "properties": {
    "objective": {"type": "string", "maxLength": 1000},
    "steps": {"type": "array", "maxItems": 8, "items": {"type": "string", "maxLength": 500}}},
    "required": ["objective", "steps"], "additionalProperties": False}
STUDY_INPUT = {"type": "object", "properties": {
    "topic": {"type": "string", "maxLength": 1000}, "research_field": {"type": "string", "maxLength": 200},
    "research_type": {"type": "string", "enum": sorted(RESEARCH_TYPES)},
    "problem_statement": {"type": "string", "maxLength": 4000},
    "objectives": {"type": "array", "maxItems": 30, "items": {"type": "string", "maxLength": 2000}},
    "research_questions": {"type": "array", "maxItems": 30, "items": {"type": "string", "maxLength": 2000}}},
    "required": ["topic", "research_field", "research_type", "problem_statement", "objectives", "research_questions"],
    "additionalProperties": False}
STUDY_OUTPUT = {"type": "object", "properties": {
    "domain_pack": {"type": "string", "maxLength": 100},
    "missing_information": {"type": "array", "maxItems": 30, "items": {"type": "string", "maxLength": 1000}},
    "method_prompts": {"type": "array", "maxItems": 30, "items": {"type": "string", "maxLength": 2000}},
    "feasibility_checks": {"type": "array", "maxItems": 30, "items": {"type": "string", "maxLength": 2000}},
    "cautions": {"type": "array", "maxItems": 30, "items": {"type": "string", "maxLength": 1000}},
    "review_required": {"type": "boolean"}}, "required": ["domain_pack", "missing_information", "method_prompts", "feasibility_checks", "cautions", "review_required"],
    "additionalProperties": False}


def _research_plan(data: dict, context) -> dict:
    question = data["question"].strip()
    return {"objective": question, "steps": [
        "Clarify the population, setting, and outcome before searching.",
        "Search scholarly sources and record provider outcomes and retrieval dates.",
        "Review source passages and keep claims linked to their original pages.",
        "Compare methods and findings while recording disagreements and missing evidence.",
        "Select a study design only after checking assumptions and feasibility.",
        "Run experiments only with approved inputs and the isolated execution engine.",
        "Review results and citations before drafting any conclusions."]}


def _study_design_guidance(data: dict, context) -> dict:
    analysis = analyze_research_problem(data["topic"], data["research_field"], data["research_type"],
        data["problem_statement"], data["objectives"], data["research_questions"]).to_dict()
    method_prompts = [f"{item['method']}: {item['rationale']} Assumptions to check: " + "; ".join(item["assumptions"])
                      for item in analysis["recommendations"]]
    feasibility = [f"{item['area']}: {item['check']} Rationale: {item['rationale']}"
                   for item in analysis["feasibility_checks"]]
    return {"domain_pack": analysis["domain_pack"], "missing_information": analysis["missing_information"],
            "method_prompts": method_prompts, "feasibility_checks": feasibility,
            "cautions": analysis["cautions"], "review_required": True}


def default_agent_registry() -> AgentRegistry:
    registry = AgentRegistry()
    registry.register(AgentDefinition("research_plan", QUESTION_INPUT, PLAN_OUTPUT, _research_plan,
                                      provider="local", model="research-plan-v1"))
    registry.register(AgentDefinition("study_design_guidance", STUDY_INPUT, STUDY_OUTPUT,
                                      _study_design_guidance, provider="local", model="methodology-rules-v1"))
    config = ClaimExtractionConfig.from_environment()
    if config is not None:
        provider = OpenAICompatibleAgentProvider(config)
        from researchos.agents.runtime import AgentResult
        def invoke_ai_plan(data: dict, context):
            result = provider.invoke(data)
            return AgentResult(result["output"], config.provider, config.model, result["usage"])
        registry.register(AgentDefinition("research_plan_ai", QUESTION_INPUT, PLAN_OUTPUT, invoke_ai_plan,
                                          provider=config.provider, model=config.model))
    return registry
