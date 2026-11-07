import base64
from io import BytesIO
import tempfile
import unittest

from pypdf import PdfWriter

from researchos.methodology.analyzer import analyze_research_problem, recommend_statistical_methods
from researchos.methodology.store import approve_study_design, get_study_design, save_study_design


def add_reviewed_claim(directory):
    from researchos.knowledge.store import add_claim, update_claim_review
    from researchos.workspace import store_pdf
    writer = PdfWriter(); writer.add_blank_page(width=200, height=200)
    pdf = BytesIO(); writer.write(pdf)
    document = store_pdf(directory, "evidence.pdf", base64.b64encode(pdf.getvalue()).decode())
    claim = add_claim(directory, document["document_id"], 1, "Source passage", "Reviewed literature finding")
    return update_claim_review(directory, claim["claim_id"], "reviewed", "Checked against source page")


class MethodologyTests(unittest.TestCase):
    def test_problem_analysis_does_not_invent_problem_objectives_or_questions(self):
        result = analyze_research_problem("Machine learning in medicine", "Medical", "quantitative")
        self.assertIsNone(result.problem_statement)
        self.assertEqual(result.objectives, [])
        self.assertEqual(result.research_questions, [])
        self.assertGreaterEqual(len(result.missing_information), 3)
        self.assertTrue(any("ethics" in note.lower() for note in result.cautions))

    def test_domain_pack_is_selected_by_explicit_field_and_research_type(self):
        result = analyze_research_problem("A topic", "Machine Learning", "computational",
            "Compare model performance", ["Evaluate generalization"], ["Which model performs better?"])
        self.assertEqual(result.domain_pack, "machine_learning")
        self.assertTrue(any("leakage" in item["rationale"].lower() for item in result.recommendations))
        qualitative = analyze_research_problem("A topic", "Machine Learning", "qualitative")
        self.assertTrue(all(item["method"] == "Align the design to the primary research question" for item in qualitative.recommendations))

    def test_problem_analysis_requires_valid_user_inputs(self):
        with self.assertRaisesRegex(ValueError, "research topic"):
            analyze_research_problem(" ", "Physics")
        with self.assertRaisesRegex(ValueError, "supported research type"):
            analyze_research_problem("Topic", "Physics", "clinical")

    def test_feasibility_guidance_is_research_type_and_domain_specific_without_inventing_sample_size(self):
        qualitative = analyze_research_problem("A topic", "Social Sciences", "qualitative")
        qualitative_checks = " ".join(item["check"] for item in qualitative.feasibility_checks).lower()
        qualitative_areas = {item["area"] for item in qualitative.feasibility_checks}
        self.assertIn("Purposeful sampling", qualitative_areas)
        self.assertIn("universal fixed sample-size rule", qualitative_checks)
        self.assertIn("privacy", qualitative_checks)
        self.assertNotIn("sample size of", qualitative_checks)

        computational = analyze_research_problem("A topic", "Machine Learning", "computational")
        computational_areas = {item["area"] for item in computational.feasibility_checks}
        self.assertIn("Compute and runtime", computational_areas)
        self.assertIn("Data leakage and split feasibility", computational_areas)

    def test_medical_feasibility_flags_ethics_and_clinical_oversight(self):
        result = analyze_research_problem("A topic", "Medical", "quantitative")
        checks = " ".join(item["check"] for item in result.feasibility_checks).lower()
        self.assertIn("ethics/regulatory approval", checks)
        self.assertIn("participant-safety", checks)

    def test_statistical_guidance_withholds_independent_group_method_until_independence_checked(self):
        result = recommend_statistical_methods("continuous", "two_independent_groups", 60)
        self.assertEqual(result["candidate_methods"], [])
        self.assertTrue(any("independent" in item.lower() for item in result["missing_information"]))
        checked = recommend_statistical_methods("continuous", "two_independent_groups", 60, True, True)
        self.assertIn("Welch two-sample t-test", checked["candidate_methods"])
        self.assertIn("not a selected analysis", checked["warning"])

    def test_statistical_guidance_covers_count_ordinal_binary_and_survival_cases_with_gates(self):
        count = recommend_statistical_methods("count", "association", 120, True)
        self.assertTrue(any("Poisson regression" in method for method in count["candidate_methods"]))
        self.assertTrue(any("overdispersion" in item.lower() for item in count["assumptions"]))
        self.assertTrue(count["missing_information"])

        ordinal = recommend_statistical_methods("ordinal", "two_independent_groups", 80, True)
        self.assertTrue(any("Mann-Whitney" in method for method in ordinal["candidate_methods"]))
        self.assertTrue(any("distribution" in item.lower() for item in ordinal["assumptions"]))

        binary = recommend_statistical_methods("binary", "association", 200)
        self.assertTrue(any("Logistic regression" in method for method in binary["candidate_methods"]))
        self.assertTrue(any("event count" in item.lower() for item in binary["missing_information"]))

        survival = recommend_statistical_methods("time_to_event", "two_independent_groups", 150, True)
        self.assertTrue(any("Kaplan-Meier" in method for method in survival["candidate_methods"]))
        self.assertTrue(any("censoring" in item.lower() for item in survival["assumptions"]))

    def test_dataset_summary_checks_are_descriptive_validated_and_never_claim_adequacy(self):
        result = recommend_statistical_methods("count", "association", 40, True,
            dataset_summary={"sample_size": 42, "group_sizes": [12, 30], "missing_fraction": 0.1,
                             "event_count": 8, "mean": 1.2, "variance": 3.4, "zero_fraction": 0.65})
        joined = " ".join(result["dataset_diagnostics"])
        self.assertIn("differs from the sample size", joined)
        self.assertIn("does not establish adequate information or power", joined)
        self.assertIn("conditional mean and variance", joined)
        self.assertIn("zero counts", joined)
        self.assertIn("no result or adequacy conclusion", result["warning"])
        with self.assertRaisesRegex(ValueError, "missing_fraction"):
            recommend_statistical_methods("continuous", "one_sample", 20,
                dataset_summary={"missing_fraction": 1.5})

    def test_study_design_draft_is_editable_and_requires_review_before_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            claim = add_reviewed_claim(directory)
            draft = save_study_design(directory, {"title": "Study plan", "research_field": "Computer Science",
                "research_type": "computational", "objectives": ["Compare methods"],
                "variables": {"independent": ["model"], "dependent": ["F1"], "control": ["dataset"]},
                "evidence_claim_ids": [claim["claim_id"]]})
            self.assertEqual(draft["status"], "draft")
            with self.assertRaisesRegex(ValueError, "Complete these fields"):
                approve_study_design(directory, draft["design_id"], "Reviewed")
            edited = save_study_design(directory, {"title": "Study plan", "research_field": "Computer Science",
                "research_type": "computational", "problem_statement": "Fairly compare methods",
                "objectives": ["Compare methods"], "research_questions": ["Which method performs better?"],
                "methodology": "Benchmark", "methodology_rationale": "It answers the comparison question.",
                "population_or_material": "Open benchmark datasets", "sample_size": "Not applicable to fixed benchmark",
                "sampling_method": "Predefined benchmark inclusion criteria", "analysis_plan": "Report metrics with intervals.",
                "evaluation_metrics": ["F1"], "limitations": ["Benchmark coverage is limited"],
                "ethics_notes": "No human subjects; dataset license review required.",
                "variables": {"independent": ["model"], "dependent": ["F1"], "control": ["dataset"]},
                "evidence_claim_ids": [claim["claim_id"]]},
                draft["design_id"])
            self.assertEqual(edited["design_id"], draft["design_id"])
            from researchos.knowledge.store import update_claim_review
            update_claim_review(directory, claim["claim_id"], "provisional", "Needs another review")
            with self.assertRaisesRegex(ValueError, "reviewed_literature_evidence_claims"):
                approve_study_design(directory, draft["design_id"], "Attempted approval")
            update_claim_review(directory, claim["claim_id"], "reviewed", "Source checked")
            approved = approve_study_design(directory, draft["design_id"], "Reviewed by researcher")
            self.assertEqual(approved["status"], "approved")
            self.assertEqual(get_study_design(directory, draft["design_id"])["reviewer_note"], "Reviewed by researcher")
            with self.assertRaisesRegex(ValueError, "Approved designs are immutable"):
                save_study_design(directory, {"title": "Changed", "research_field": "CS", "research_type": "computational"}, draft["design_id"])


if __name__ == "__main__":
    unittest.main()
