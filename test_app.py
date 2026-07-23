import unittest
from unittest.mock import patch

import app


class ArticlePipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.story = app.Story(
            id="ladera-ranch",
            source="The Guardian",
            group="Major News",
            title="Southern California suburb alarmed as rare cancer sickens children",
            link="https://example.com/ladera-ranch",
            summary_text="",
            published=None,
            topics=("Health", "US"),
        )

    def good_card(self) -> dict[str, str]:
        return {
            "headline": "Six childhood cancer cases prompt Orange County review",
            "summary": (
                "Six children in Ladera Ranch, a planned Orange County community, have been "
                "diagnosed with Ewing sarcoma, a rare cancer that forms in bone or soft tissue. "
                "County supervisors have asked California health officials to examine the cases "
                "and determine whether their concentration is statistically unusual or points to "
                "a shared exposure. Families want a transparent investigation while officials "
                "caution that a cluster can occur by chance and does not itself establish a cause."
            ),
            "background": (
                "Cancer-cluster investigations compare observed diagnoses with the number expected "
                "for a population of similar size and age, then examine timing, geography, and "
                "possible common exposures. The finding could shape local testing and public-health "
                "action, but the decisive signal will be whether epidemiologists identify a rate "
                "above the statistical baseline or a credible environmental link."
            ),
        }

    def test_sanitize_article_text_removes_page_furniture_and_duplicates(self) -> None:
        raw = "\n".join(
            (
                "Families in Ladera Ranch are asking health officials to investigate six cancer diagnoses.",
                "Sign up for the Breaking News newsletter email every morning.",
                "County officials said state epidemiologists will review the reported cases.",
                "County officials said state epidemiologists will review the reported cases.",
            )
        )

        cleaned = app.sanitize_article_text(raw)

        self.assertNotIn("newsletter", cleaned.lower())
        self.assertEqual(cleaned.count("state epidemiologists"), 1)

    def test_evidence_gate_requires_relevant_full_text(self) -> None:
        relevant_sentence = (
            "Ladera Ranch families asked California cancer investigators to review six childhood diagnoses."
        )
        relevant_text = " ".join(relevant_sentence for _ in range(24))
        relevant = app.ArticleEvidence(
            url=self.story.link,
            title=self.story.title,
            text=relevant_text,
            word_count=len(relevant_text.split()),
        )
        unrelated_text = " ".join(
            "Financial markets moved after a central bank changed interest rates." for _ in range(24)
        )
        unrelated = app.ArticleEvidence(
            url=self.story.link,
            title=self.story.title,
            text=unrelated_text,
            word_count=len(unrelated_text.split()),
        )

        self.assertTrue(app.article_evidence_is_sufficient(relevant))
        self.assertFalse(app.article_evidence_is_sufficient(unrelated))

    def test_card_validator_rejects_screenshot_failure_mode(self) -> None:
        bad_card = {
            "headline": "Southern California suburb alarmed as rare cancer sickens children",
            "summary": (
                "Residents voiced concerns after six children were diagnosed with Ewing sarcoma. "
                "Sign up for the Breaking News newsletter email. "
                "The full story will matter for the."
            ),
            "background": (
                "This story sits inside a wider struggle over power, legitimacy, and public trust. "
                "The event may be brief, but the response can set precedents."
            ),
        }

        errors = app.card_quality_errors(bad_card, self.story)

        self.assertIn("card contains meta or promotional language", errors)
        self.assertIn("background contains generic stock analysis", errors)

    def test_card_validator_accepts_grounded_publishable_prose(self) -> None:
        good_card = self.good_card()

        self.assertEqual(app.card_quality_errors(good_card, self.story), ())

    def test_smart_summary_repairs_instead_of_using_canned_fallback(self) -> None:
        evidence_text = " ".join(
            "Ladera Ranch families asked California officials to investigate six cancer diagnoses."
            for _ in range(24)
        )
        evidence = app.ArticleEvidence(
            url=self.story.link,
            title=self.story.title,
            text=evidence_text,
            word_count=len(evidence_text.split()),
        )
        broken = {
            "headline": "Cancer cases in",
            "summary": "The full story will matter for the.",
            "background": "This story is about public trust.",
        }

        with (
            patch.object(app, "configured_ai_provider", return_value="openai"),
            patch.object(app, "ai_model", return_value="gpt-test"),
            patch.object(app, "ai_summary_cached", return_value=broken),
            patch.object(app, "ai_summary_repair_cached", return_value=self.good_card()) as repair,
        ):
            result = app.smart_summarize(self.story, evidence, detail=3, refresh_key="refresh")

        self.assertIsNotNone(result)
        self.assertEqual(result["__headline"], self.good_card()["headline"])
        repair.assert_called_once()


if __name__ == "__main__":
    unittest.main()
