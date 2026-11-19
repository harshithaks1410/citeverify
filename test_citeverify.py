"""Automated tests for CiteVerify core verification rules and consensus logic.

Validates the three critical hackathon requirements:
1. Anti-fabrication rule: Quote verification rejects hallucinated / made-up sentences.
2. Retraction exclusion: Retracted papers are completely purged from consensus calculation.
3. Honest verdicts: The verdict is strictly 'unverifiable' when no papers are found.
"""

import unittest
from retraction import RetractionStatus
from search import Paper
from verify import (
    calculate_paper_quality_weight,
    compute_consensus,
    extract_study_metadata_regex,
    judge_paper_stance,
    verify_quote_in_abstract,
)


class TestCiteVerifyCoreRules(unittest.TestCase):

    def test_quote_verification_rejects_hallucination(self):
        """Rule 1: Verify that made-up or hallucinated sentences are rejected."""
        abstract = (
            "In this randomized clinical trial involving 120 overweight adults, "
            "intermittent fasting significantly improved insulin sensitivity and lowered "
            "fasting plasma glucose over 12 weeks. No major adverse events were observed."
        )

        # 1. Hallucinated sentence not present in abstract
        fake_quote = "Fasting completely eliminates diabetes permanently in 100% of human patients."
        is_verified, verified_text = verify_quote_in_abstract(fake_quote, abstract)
        self.assertFalse(is_verified, "Anti-fabrication rule MUST reject fabricated quote.")
        self.assertIsNone(verified_text)

        # 2. Verbatim authentic sentence present in abstract (case/whitespace variations)
        real_quote = "intermittent fasting significantly improved insulin sensitivity and lowered fasting plasma glucose"
        is_verified_real, verified_real = verify_quote_in_abstract(real_quote, abstract)
        self.assertTrue(is_verified_real, "Anti-fabrication rule MUST accept verbatim sentence.")
        self.assertIsNotNone(verified_real)

        # 3. Test through judge_paper_stance enforcement
        paper = Paper(
            paper_id="test_01",
            title="Trial of Intermittent Fasting",
            abstract=abstract,
            year=2024,
            doi="10.1001/test.2024.1",
        )
        # Even if an LLM returns a made-up sentence, verify_quote_in_abstract prevents display
        fake_stance_result = judge_paper_stance("Intermittent fasting cures all diabetes", paper)
        if fake_stance_result.get("key_sentence") is not None:
            # If any key_sentence is returned, it must exist in the abstract
            self.assertTrue(
                verify_quote_in_abstract(fake_stance_result["key_sentence"], abstract)[0]
            )

    def test_retracted_papers_excluded_from_consensus(self):
        """Rule 2: Retracted papers must be excluded from verdict and consensus meter."""
        papers = [
            {
                "paper_id": "p1",
                "title": "Legitimate Study Supporting Fasting",
                "stance": "supports",
                "quality_weight": 2.5,
                "retraction": {"is_retracted": False},
                "study_type": "randomized controlled trial",
            },
            {
                "paper_id": "p2_wakefield",
                "title": "Retracted Fraudulent Study",
                "stance": "supports",
                "quality_weight": 4.0,  # High weight that WOULD distort verdict if included
                "retraction": {
                    "is_retracted": True,
                    "reason": "Retracted due to falsified clinical data.",
                    "notice_doi": "10.1016/S0140-6736(10)60175-4",
                },
                "study_type": "meta-analysis",
            },
            {
                "paper_id": "p3",
                "title": "Trial Showing No Difference",
                "stance": "contradicts",
                "quality_weight": 2.5,
                "retraction": {"is_retracted": False},
                "study_type": "randomized controlled trial",
            },
        ]

        consensus = compute_consensus("Sample Claim", papers)

        # Retracted count
        self.assertEqual(consensus["retracted_papers_count"], 1)
        self.assertEqual(consensus["usable_papers_count"], 2)

        # Supports count should ONLY count the non-retracted paper (1, not 2)
        self.assertEqual(consensus["counts"]["supports"], 1)
        self.assertEqual(consensus["counts"]["contradicts"], 1)

        # Weighted scores must EXCLUDE paper 2's 4.0 weight
        # Only p1 (2.5 supports) and p3 (2.5 contradicts)
        self.assertEqual(consensus["quality_weighted_scores"]["supports"], 2.5)
        self.assertEqual(consensus["quality_weighted_scores"]["contradicts"], 2.5)

        # Distribution should be 50% supports, 50% contradicts (not 72% supports!)
        self.assertEqual(consensus["percentages"]["supports"], 50.0)
        self.assertEqual(consensus["percentages"]["contradicts"], 50.0)
        self.assertEqual(consensus["verdict"], "mixed")

    def test_verdict_unverifiable_when_no_papers(self):
        """Rule 3: Verdict must be 'unverifiable' with 0 confidence when no papers are found."""
        empty_papers = []
        consensus = compute_consensus("Obscure non-existent scientific assertion", empty_papers)

        self.assertEqual(consensus["verdict"], "unverifiable")
        self.assertEqual(consensus["confidence_score"], 0.0)
        self.assertEqual(consensus["confidence_label"], "none")
        self.assertTrue(consensus["weak_evidence"])
        self.assertEqual(consensus["usable_papers_count"], 0)

    def test_quality_weighting_hierarchy(self):
        """Rule 4: Higher-tier studies (meta-analyses) must out-weight lower tiers."""
        w_meta = calculate_paper_quality_weight("meta-analysis", citation_count=50, year=2024)
        w_review = calculate_paper_quality_weight("review", citation_count=50, year=2024)
        w_animal = calculate_paper_quality_weight("in vitro / animal study", citation_count=50, year=2024)

        self.assertGreater(w_meta, w_review)
        self.assertGreater(w_review, w_animal)

    def test_study_metadata_extraction(self):
        """Rule 5: Extract study design and sample size patterns."""
        title = "Intermittent fasting in type 2 diabetes: a randomized controlled trial"
        abstract = "A total of 1,240 patients were randomized into two groups. Intermittent fasting improved HbA1c."
        study_type, sample_size = extract_study_metadata_regex(title, abstract, ["Journal Article"])

        self.assertEqual(study_type, "randomized controlled trial")
        self.assertEqual(sample_size, "n = 1,240")


if __name__ == "__main__":
    unittest.main()
