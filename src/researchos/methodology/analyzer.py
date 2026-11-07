"""Deterministic domain-aware prompts and assumption-aware method guidance."""

from researchos.methodology.models import ResearchProblemAnalysis
import math

RESEARCH_TYPES = {
    "quantitative", "qualitative", "mixed_methods", "computational", "theoretical", "systematic_review",
}

DOMAIN_ALIASES = {
    "computer_science": ("computer science", "computer science and information technology", "cs"),
    "machine_learning": ("machine learning", "artificial intelligence", "ai", "deep learning"),
    "cybersecurity": ("cybersecurity", "cyber security", "information security"),
    "physics": ("physics", "astrophysics", "particle physics"),
    "medical": ("medicine", "medical", "clinical", "health sciences", "public health"),
    "social_sciences": ("social science", "social sciences", "psychology", "sociology", "education"),
}

METHODOLOGY_PACKS = {
    "general": [
        {"method": "Align the design to the primary research question", "rationale": "The question determines what observations and analyses can answer it.", "assumptions": ["A specific, answerable question has been approved." ]},
    ],
    "computer_science": [
        {"method": "Define baselines and evaluation conditions", "rationale": "Comparative claims require a fair, reproducible reference and consistent conditions.", "assumptions": ["The work makes an empirical algorithm or systems claim."]},
        {"method": "Report computational cost where relevant", "rationale": "Runtime and resource use may change the practical interpretation of performance.", "assumptions": ["Efficiency is relevant to the research question."]},
    ],
    "machine_learning": [
        {"method": "Predefine data splits and prevent leakage", "rationale": "Leakage can inflate measured generalization performance.", "assumptions": ["The study trains or evaluates a predictive model."]},
        {"method": "Compare against justified baselines", "rationale": "A baseline helps determine whether a proposed method adds measurable value.", "assumptions": ["Comparable prior or simple baseline methods can be defined."]},
        {"method": "Select metrics for the task and error costs", "rationale": "A single metric can hide important failure modes, especially with imbalanced outcomes.", "assumptions": ["The prediction task and consequences of errors are specified."]},
    ],
    "cybersecurity": [
        {"method": "Document the threat model and test environment", "rationale": "Security results are conditional on attacker capability, system boundaries, and configuration.", "assumptions": ["Testing is authorized and contained."]},
        {"method": "Report false positives and false negatives", "rationale": "Detection performance includes both missed threats and benign activity incorrectly flagged.", "assumptions": ["The work evaluates a detector or security control."]},
    ],
    "physics": [
        {"method": "Propagate measurement and model uncertainty", "rationale": "Uncertainty affects whether observed differences support the proposed physical interpretation.", "assumptions": ["Measurements or fitted parameters are part of the study."]},
        {"method": "State calibration and instrument conditions", "rationale": "Instrument settings can affect comparability and reproducibility.", "assumptions": ["The study uses measurements or instrument-derived data."]},
    ],
    "medical": [
        {"method": "Specify outcomes, population, and study setting", "rationale": "Clinical and health findings depend on outcome definitions and population characteristics.", "assumptions": ["A qualified domain expert reviews the design."]},
        {"method": "Obtain applicable ethics and regulatory oversight", "rationale": "Human-subject and clinical work may require prior ethics approval and regulatory review.", "assumptions": ["Researchers consult their institution and applicable authorities before recruitment or intervention."]},
    ],
    "social_sciences": [
        {"method": "Justify sampling and discuss nonresponse", "rationale": "Recruitment and nonresponse can limit how findings generalize.", "assumptions": ["The study samples people, groups, or social records."]},
        {"method": "Validate instruments and protect participant privacy", "rationale": "Measurement validity and privacy affect both inference and participant welfare.", "assumptions": ["Data collection involves people or identifiable records."]},
    ],
}

DOMAIN_RESEARCH_TYPES = {
    "computer_science": {"quantitative", "computational", "theoretical", "systematic_review"},
    "machine_learning": {"quantitative", "computational", "systematic_review"},
    "cybersecurity": {"quantitative", "computational", "qualitative", "systematic_review"},
    "physics": {"quantitative", "computational", "theoretical", "systematic_review"},
    "medical": {"quantitative", "qualitative", "mixed_methods", "systematic_review"},
    "social_sciences": {"quantitative", "qualitative", "mixed_methods", "systematic_review"},
    "general": set(RESEARCH_TYPES),
}

FEASIBILITY_CHECKS = {
    "quantitative": [
        {"area": "Sampling frame", "check": "Define the target population, eligibility rules, accessible sampling frame, and recruitment route.",
         "rationale": "The reachable sample may differ from the population named in the research question."},
        {"area": "Sample size basis", "check": "Justify the planned sample size from the primary outcome and a prespecified effect or precision target; account for clustering, repeated measures, and attrition where relevant.",
         "rationale": "A raw sample count alone does not show whether the design can answer its primary question."},
        {"area": "Resources and missingness", "check": "Check recruitment time, measurement burden, data completeness, and the plan for missing observations.",
         "rationale": "Recruitment and measurement constraints can change both achievable sample size and interpretation."},
    ],
    "qualitative": [
        {"area": "Purposeful sampling", "check": "Specify who or what can provide relevant, varied information and how cases will be selected.",
         "rationale": "Sampling should fit the phenomenon and context rather than imply statistical representativeness."},
        {"area": "Information adequacy", "check": "Explain how information richness and the evolving analysis will guide whether more cases are needed; do not use a universal fixed sample-size rule.",
         "rationale": "Adequacy depends on the question, sample specificity, data quality, and analytic approach."},
        {"area": "Access and ethics", "check": "Assess gatekeeper access, participant burden, confidentiality, and safe handling of sensitive accounts.",
         "rationale": "Access and participant protections can limit feasible recruitment and data collection."},
    ],
    "mixed_methods": [
        {"area": "Component sampling", "check": "Define sampling and adequacy criteria separately for each quantitative and qualitative component.",
         "rationale": "The components answer different parts of the question and need distinct sampling logic."},
        {"area": "Integration", "check": "Specify when and how samples, measures, or findings will be connected or compared.",
         "rationale": "Integration requires compatible timing, identifiers, and analytic plans."},
        {"area": "Combined workload", "check": "Check whether recruitment, data collection, analysis, and integration fit the available team and schedule.",
         "rationale": "Mixed designs add coordination and analysis work across methods."},
    ],
    "computational": [
        {"area": "Data and permissions", "check": "Confirm access rights, provenance, licenses, privacy constraints, and whether the data can be retained or shared.",
         "rationale": "A technically usable dataset may still be unavailable for the intended research use."},
        {"area": "Compute and runtime", "check": "Estimate required compute, storage, runtime, energy or service cost, and access to needed hardware before fixing the evaluation plan.",
         "rationale": "Resource limits can make a proposed computational evaluation impractical or irreproducible."},
        {"area": "Coverage and reproducibility", "check": "Check whether datasets, benchmarks, test conditions, and baselines cover the intended claim and can be rerun.",
         "rationale": "Narrow benchmark coverage or unavailable dependencies can limit what results support."},
    ],
    "theoretical": [
        {"area": "Scope and definitions", "check": "Bound the concepts, assumptions, and cases covered by the argument.",
         "rationale": "Explicit scope makes the reasoning assessable and guards against broader claims than the argument supports."},
        {"area": "Materials and method", "check": "Identify required texts, formal systems, proofs, or analytic tools and confirm access and expertise.",
         "rationale": "Theoretical work still depends on available materials and a tractable analytic approach."},
        {"area": "Evaluation of claims", "check": "State what would count as a counterexample, consistency check, or comparison with alternatives.",
         "rationale": "A planned way to examine the argument makes its limits and contribution clearer."},
    ],
    "systematic_review": [
        {"area": "Search coverage", "check": "Confirm database access, search-language coverage, date bounds, and time for updating searches.",
         "rationale": "Search access and scope determine which records can be identified."},
        {"area": "Screening capacity", "check": "Define eligibility rules and the people/time available for screening, conflict resolution, and full-text retrieval.",
         "rationale": "Screening workload and unresolved decisions can delay or bias selection."},
        {"area": "Extraction and synthesis", "check": "Pilot extraction fields and confirm that study designs and outcomes permit the planned synthesis.",
         "rationale": "Heterogeneous or incompletely reported studies may not support the intended comparison or pooling."},
    ],
}

DOMAIN_FEASIBILITY_CHECKS = {
    "medical": {"area": "Clinical and ethics oversight", "check": "Confirm qualified clinical review, applicable ethics/regulatory approval, site permission, and participant-safety procedures before human-subject work.",
                "rationale": "Clinical access and oversight are prerequisites for many health studies."},
    "social_sciences": {"area": "Participant access and privacy", "check": "Check recruitment permissions, language/accessibility needs, privacy protections, and any institutional ethics review.",
                         "rationale": "Participation and data protections shape feasible sampling and collection."},
    "machine_learning": {"area": "Data leakage and split feasibility", "check": "Confirm that the available data support leakage-resistant splits at the correct unit, such as person, site, device, or time.",
                         "rationale": "Some datasets cannot support the intended independent generalization test."},
    "cybersecurity": {"area": "Authorized test environment", "check": "Confirm written authorization, isolation, safe test data, and a contained environment for any security evaluation.",
                      "rationale": "Operational and legal boundaries constrain which evaluations are feasible."},
    "physics": {"area": "Instrument and calibration access", "check": "Confirm instrument availability, calibration schedule, measurement time, and access to required facilities.",
                "rationale": "Facility and instrument constraints can determine achievable precision and throughput."},
}


def identify_domain(research_field: str) -> str:
    value = research_field.strip().casefold()
    for domain, aliases in DOMAIN_ALIASES.items():
        if any(alias in value for alias in aliases):
            return domain
    return "general"


def analyze_research_problem(
    topic: str,
    research_field: str,
    research_type: str | None = None,
    problem_statement: str | None = None,
    objectives: list[str] | None = None,
    research_questions: list[str] | None = None,
) -> ResearchProblemAnalysis:
    if not isinstance(topic, str) or not topic.strip() or len(topic) > 1000:
        raise ValueError("Enter a research topic of 1 to 1000 characters.")
    if not isinstance(research_field, str) or not research_field.strip() or len(research_field) > 200:
        raise ValueError("Enter a research field of 1 to 200 characters.")
    if research_type is not None and research_type not in RESEARCH_TYPES:
        raise ValueError("Choose a supported research type or leave it unspecified.")
    objectives = _clean_list(objectives, "Objectives")
    research_questions = _clean_list(research_questions, "Research questions")
    problem_statement = _optional_text(problem_statement, "Problem statement", 4000)
    missing = []
    if not problem_statement:
        missing.append("Describe the specific research problem; the topic alone is not enough to infer it.")
    if not objectives:
        missing.append("Add at least one research objective.")
    if not research_questions:
        missing.append("Add a primary research question.")
    if not research_type:
        missing.append("Select a research type before receiving a methodology recommendation.")
    domain = identify_domain(research_field)
    recommendations = []
    feasibility_checks = []
    if research_type:
        recommendations = list(METHODOLOGY_PACKS[domain]) if research_type in DOMAIN_RESEARCH_TYPES[domain] else list(METHODOLOGY_PACKS["general"])
        feasibility_checks = list(FEASIBILITY_CHECKS[research_type])
        domain_check = DOMAIN_FEASIBILITY_CHECKS.get(domain)
        if domain_check:
            feasibility_checks.append(domain_check)
    if research_type == "systematic_review":
        recommendations += [{"method": "Predefine databases, eligibility criteria, and screening steps",
                             "rationale": "A review protocol makes coverage and selection decisions auditable.",
                             "assumptions": ["The review question and scope can be stated explicitly."]}]
    cautions = []
    if domain == "medical":
        cautions.append("Medical and human-subject research requires qualified expert oversight and any applicable ethics approval.")
    if not objectives or not research_questions or not problem_statement:
        cautions.append("No research gap, objective, or question has been invented; supply and review these inputs.")
    return ResearchProblemAnalysis(topic.strip(), research_field.strip(), domain, research_type,
        problem_statement, objectives, research_questions, missing, recommendations, feasibility_checks, cautions)


def recommend_statistical_methods(
    outcome_type: str,
    design: str,
    sample_size: int | None = None,
    independent_observations: bool | None = None,
    distribution_assessed: bool = False,
    variance_assessed: bool = False,
    dataset_summary: dict | None = None,
) -> dict:
    outcomes = {"continuous", "binary", "categorical", "count", "ordinal", "time_to_event", "unknown"}
    designs = {"two_independent_groups", "paired_two_conditions", "multiple_groups", "association", "one_sample", "unknown"}
    if outcome_type not in outcomes or design not in designs:
        raise ValueError("Unsupported outcome type or comparison design.")
    missing = []
    assumptions = []
    methods = []
    if outcome_type == "unknown": missing.append("Define the outcome type.")
    if design == "unknown": missing.append("Define the comparison or sampling design.")
    if sample_size is None: missing.append("Provide group or sample sizes.")
    elif not isinstance(sample_size, int) or isinstance(sample_size, bool) or sample_size < 1: raise ValueError("Sample size must be a positive integer.")
    independent = independent_observations is True
    if design in {"two_independent_groups", "multiple_groups"} and not independent:
        missing.append("Confirm whether observations are independent; clustered or repeated data need another model.")
    if outcome_type == "continuous" and design == "two_independent_groups" and independent:
        methods.append("Welch two-sample t-test")
        assumptions.extend(["Independent observations", "Continuous outcome", "No severe influential outliers; inspect group distributions"])
        if not distribution_assessed: missing.append("Assess group distributions and influential outliers before selecting a parametric test.")
        if variance_assessed:
            assumptions.append("Welch's test does not require equal group variances.")
    elif outcome_type == "continuous" and design == "paired_two_conditions":
        methods.extend(["Paired t-test if paired differences are approximately normal", "Wilcoxon signed-rank alternative if its assumptions fit"])
        assumptions.extend(["Measurements are correctly paired", "Assess the distribution of within-pair differences"])
        if not distribution_assessed: missing.append("Assess the within-pair difference distribution.")
    elif outcome_type in {"binary", "categorical"} and design == "two_independent_groups" and independent:
        methods.extend(["Chi-square test when expected cell counts are adequate", "Fisher's exact test for sparse 2x2 tables"])
        assumptions.extend(["Independent observations", "Mutually exclusive categories", "Expected cell counts checked"])
        missing.append("Provide the contingency table and inspect expected cell counts.")
    elif outcome_type == "continuous" and design == "multiple_groups" and independent:
        methods.extend(["One-way ANOVA if model assumptions are reasonable", "Welch ANOVA for unequal variances", "Kruskal-Wallis for suitable ordinal/non-normal outcomes"])
        assumptions.extend(["Independent observations", "Assess residuals/outliers and variance structure"])
        if not distribution_assessed or not variance_assessed:
            missing.append("Assess residual distributions and variance structure before choosing among these methods.")
    elif design == "association" and outcome_type in {"continuous", "ordinal"}:
        methods.extend(["Pearson correlation for a linear association under its assumptions", "Spearman correlation for a monotonic rank association"])
        assumptions.extend(["Inspect scatterplots, influential observations, and the intended association form"])
        if not distribution_assessed: missing.append("Inspect the relationship shape and influential observations.")
    elif design == "association" and outcome_type == "binary":
        methods.append("Logistic regression for a binary outcome when the model form and link are appropriate")
        assumptions.extend(["Check independent or appropriately modeled observations", "Inspect event counts, separation, influential records, and model calibration"])
        missing.append("Define the outcome coding, candidate predictors, event count, and confounding strategy.")
    elif design == "association" and outcome_type == "count":
        methods.extend(["Poisson regression when conditional variance is compatible with the model", "Negative binomial regression when overdispersion is supported"])
        assumptions.extend(["Count outcome and exposure time/offset are defined", "Check overdispersion, excess zeros, dependence, and model fit"])
        missing.append("Define the observation unit, exposure/offset, predictors, and dependence structure.")
    elif design == "association" and outcome_type == "time_to_event":
        methods.append("Cox proportional-hazards model if proportional-hazards and censoring assumptions are reasonable")
        assumptions.extend(["Define event and time origin", "Assess proportional hazards and censoring assumptions", "Account for competing events where relevant"])
        missing.append("Specify event definition, censoring, follow-up, predictors, and competing-event handling.")
    elif outcome_type == "time_to_event" and design == "two_independent_groups" and independent:
        methods.extend(["Kaplan-Meier curves for descriptive survival estimates", "Log-rank test for an unadjusted group comparison when its assumptions fit"])
        assumptions.extend(["Define time origin, event, and censoring", "Censoring is non-informative for the intended comparison", "Groups and competing events are handled appropriately"])
        missing.append("Report event counts, censoring, follow-up, and competing-event handling; consider adjusted analysis if needed.")
    elif outcome_type == "ordinal" and design == "two_independent_groups" and independent:
        methods.append("Mann-Whitney U test for an ordinal outcome when its estimand and distribution assumptions fit")
        assumptions.extend(["Independent observations", "Ordered categories", "Interpretation as a location shift requires similarly shaped distributions"])
        missing.append("Show category counts and define the target comparison; consider ordinal regression for adjusted analyses.")
    elif outcome_type == "ordinal" and design == "paired_two_conditions":
        methods.append("Wilcoxon signed-rank test for paired ordinal outcomes when its assumptions fit")
        assumptions.extend(["Observations are correctly paired", "Paired differences have a meaningful ordering and suitable symmetry"])
        missing.append("Show paired category transitions and confirm the intended estimand.")
    elif outcome_type == "ordinal" and design == "multiple_groups" and independent:
        methods.append("Kruskal-Wallis test for an unadjusted comparison of independent ordinal groups when appropriate")
        assumptions.extend(["Independent observations", "Ordered categories", "Similar distribution shapes if interpreted as location differences"])
        missing.append("Show category counts by group and define planned follow-up comparisons.")
    elif outcome_type == "ordinal" and design == "association":
        methods.extend(["Ordinal logistic regression if the proportional-odds assumption fits", "Spearman correlation for a monotonic rank association"])
        assumptions.extend(["Categories have a justified order", "Assess proportional odds for ordinal logistic regression", "Account for clustering or repeated observations"])
        missing.append("Define category order, predictors, and the proportional-odds assessment plan.")
    elif outcome_type == "count" and design in {"two_independent_groups", "multiple_groups"} and independent:
        methods.extend(["Poisson regression for group comparisons when its mean-variance assumption fits", "Negative binomial regression when overdispersion is supported"])
        assumptions.extend(["Independent observations", "Define exposure/offset where observation time differs", "Check overdispersion and excess zeros"])
        missing.append("Provide group-specific count summaries, exposure time, and a plan for overdispersion checks.")
    elif outcome_type == "continuous" and design == "one_sample":
        methods.extend(["One-sample t-test when the sampling and distribution assumptions are reasonable", "Wilcoxon signed-rank test for a suitable paired-difference/one-sample estimand"])
        assumptions.extend(["Define the reference value and target estimand", "Inspect observations for influential outliers and distribution concerns"])
        if not distribution_assessed: missing.append("Assess the outcome distribution and influential observations.")
    elif outcome_type == "binary" and design == "one_sample":
        methods.append("Exact binomial interval/test or a Wilson confidence interval for a single proportion, according to the prespecified question")
        assumptions.extend(["Define the binary event and denominator", "Observations are independent or dependence is modeled"])
        missing.append("Provide event and denominator counts and state whether the goal is estimation or a hypothesis test.")
    elif outcome_type == "categorical" and design == "association":
        methods.append("Multinomial logistic regression for a nominal outcome when categories and model assumptions fit")
        assumptions.extend(["Define the reference category", "Check sparse cells, separation, dependence, and calibration"])
        missing.append("Provide category counts and define predictors and confounder strategy.")
    elif design in {"two_independent_groups", "multiple_groups"} and not independent:
        pass
    else:
        missing.append("The supplied outcome/design combination needs a domain statistician or a more specific analysis plan.")
    diagnostics = diagnose_dataset_summary(dataset_summary, sample_size, outcome_type)
    return {"candidate_methods": methods, "assumptions": assumptions, "missing_information": missing,
            "dataset_diagnostics": diagnostics,
            "status": "guidance_only", "warning": "These are conditional candidates, not a selected analysis. Validate the estimand, data structure, assumptions, multiplicity, and model with a qualified reviewer; no result or adequacy conclusion is computed."}


def diagnose_dataset_summary(summary: dict | None, declared_sample_size: int | None = None,
                             outcome_type: str = "unknown") -> list[str]:
    """Return descriptive prompts from researcher-supplied summaries, never inferential conclusions."""
    if summary is None:
        return []
    if not isinstance(summary, dict) or set(summary) - {"sample_size", "group_sizes", "missing_fraction", "event_count", "mean", "variance", "zero_fraction"}:
        raise ValueError("Dataset summary must be an object containing only supported summary fields.")
    diagnostics = []
    n = summary.get("sample_size")
    if n is not None and (not isinstance(n, int) or isinstance(n, bool) or n < 1):
        raise ValueError("Dataset summary sample_size must be a positive integer.")
    if n is not None:
        diagnostics.append(f"Summary reports {n} observations; this count alone does not establish adequate information or power.")
        if declared_sample_size is not None and n != declared_sample_size:
            diagnostics.append("The supplied summary count differs from the sample size field; reconcile the analysis unit and exclusions.")
    sizes = summary.get("group_sizes")
    if sizes is not None:
        if not isinstance(sizes, list) or not sizes or len(sizes) > 20 or any(not isinstance(item, int) or isinstance(item, bool) or item < 1 for item in sizes):
            raise ValueError("Dataset summary group_sizes must be a list of 1 to 20 positive integers.")
        diagnostics.append(f"Group counts range from {min(sizes)} to {max(sizes)} across {len(sizes)} groups; review imbalance and whether the groups match the planned unit of analysis.")
    fraction_fields = ("missing_fraction", "zero_fraction")
    for field in fraction_fields:
        value = summary.get(field)
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1):
            raise ValueError(f"Dataset summary {field} must be a finite fraction from 0 to 1.")
    if "missing_fraction" in summary:
        diagnostics.append(f"Summary reports {summary['missing_fraction']:.1%} missing values; describe where they occur and justify the missing-data handling plan.")
    events = summary.get("event_count")
    if events is not None and (not isinstance(events, int) or isinstance(events, bool) or events < 0):
        raise ValueError("Dataset summary event_count must be a nonnegative integer.")
    if events is not None:
        diagnostics.append(f"Summary reports {events} events; assess information relative to model complexity and follow-up without relying on a universal events-per-variable cutoff.")
    mean, variance = summary.get("mean"), summary.get("variance")
    for field, value in (("mean", mean), ("variance", variance)):
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0):
            raise ValueError(f"Dataset summary {field} must be a finite nonnegative number.")
    if outcome_type == "count" and mean is not None and variance is not None:
        diagnostics.append(f"Count summary mean={mean:g}, variance={variance:g}; compare conditional mean and variance after accounting for predictors and exposure before choosing a count model.")
    zeros = summary.get("zero_fraction")
    if outcome_type == "count" and zeros is not None:
        diagnostics.append(f"Summary reports {zeros:.1%} zero counts; examine whether zeros are expected under the study process before considering a zero-inflated model.")
    return diagnostics


def _clean_list(value: list[str] | None, label: str) -> list[str]:
    if value is None: return []
    if not isinstance(value, list) or len(value) > 30 or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{label} must be a list of at most 30 text items.")
    cleaned = [item.strip() for item in value if item.strip()]
    if any(len(item) > 1000 for item in cleaned): raise ValueError(f"{label} items must be 1000 characters or fewer.")
    return cleaned


def _optional_text(value: str | None, label: str, maximum: int) -> str | None:
    if value is None or value == "": return None
    if not isinstance(value, str) or len(value) > maximum:
        raise ValueError(f"{label} must be {maximum} characters or fewer.")
    return value.strip() or None
