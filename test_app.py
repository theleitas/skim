import unittest
from datetime import datetime, timedelta, timezone
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

    def news_story(
        self,
        story_id: str,
        title: str,
        source: str,
        *,
        hours_ago: float = 1,
        group: str = "Major News",
    ) -> app.Story:
        return app.Story(
            id=story_id,
            source=source,
            group=group,
            title=title,
            link=f"https://example.com/{story_id}",
            summary_text="Officials confirmed the development and described its immediate consequences.",
            published=datetime.now(timezone.utc) - timedelta(hours=hours_ago),
            topics=("World",),
        )

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
            attempt = app.smart_summarize(self.story, evidence, detail=3, refresh_key="refresh")

        self.assertIsNotNone(attempt.card)
        self.assertEqual(attempt.card["__headline"], self.good_card()["headline"])
        repair.assert_called_once()

    def test_openai_cost_applies_cached_and_cache_write_rates(self) -> None:
        cost = app.openai_cost(
            "gpt-5.6-terra",
            input_tokens=1_000_000,
            output_tokens=100_000,
            cached_input_tokens=200_000,
            cache_write_tokens=100_000,
        )

        self.assertAlmostEqual(cost, 3.6125)

    def test_ai_cost_counter_does_not_count_same_batch_twice(self) -> None:
        first_total, first_changed = app.accumulate_ai_cost(0, "", "batch-1", 0.25)
        second_total, second_changed = app.accumulate_ai_cost(
            first_total,
            "batch-1",
            "batch-1",
            0.25,
        )

        self.assertTrue(first_changed)
        self.assertEqual(first_total, 250_000)
        self.assertFalse(second_changed)
        self.assertEqual(second_total, first_total)

    def test_story_clusters_do_not_drift_into_shared_generic_words(self) -> None:
        stories = [
            self.news_story(
                "kyiv-one",
                "Russia launches drones at Kyiv overnight",
                "BBC News",
            ),
            self.news_story(
                "kyiv-two",
                "Kyiv hit in overnight Russian drone attack",
                "Reuters",
            ),
            self.news_story(
                "startup",
                "Drone delivery startup launches service in London",
                "Technology Daily",
                group="Aggregator",
            ),
        ]

        clusters = app.cluster_stories(stories)

        self.assertEqual(sorted(len(cluster) for cluster in clusters), [1, 2])

    def test_reference_count_uses_distinct_outlets_without_feed_bonuses(self) -> None:
        stories = [
            self.news_story(
                "ap-one",
                "Earthquake triggers emergency response across coastal region",
                "AP News",
                group="Aggregator",
            ),
            self.news_story(
                "ap-two",
                "Coastal region begins emergency response after earthquake",
                "Associated Press",
                group="Aggregator",
            ),
        ]

        ranked = app.rank_stories(stories, require_high_signal=False)

        self.assertEqual(len(ranked), 1)
        self.assertEqual(ranked[0].references, 1)

    def test_high_signal_filter_keeps_fast_coverage_and_fresh_major_breaking_news(self) -> None:
        stories = [
            self.news_story(
                "old-single",
                "Officials continue talks over regional economic plan",
                "BBC News",
                hours_ago=18,
            ),
            self.news_story(
                "fresh-single",
                "Major earthquake triggers emergency response in capital",
                "Reuters",
                hours_ago=2,
            ),
            self.news_story(
                "covered-one",
                "Government coalition collapses after confidence vote",
                "BBC News",
                hours_ago=6,
            ),
            self.news_story(
                "covered-two",
                "Coalition government collapses following confidence vote",
                "Reuters",
                hours_ago=5,
            ),
        ]

        ranked = app.rank_stories(stories)
        ranked_ids = {item.story.id for item in ranked}

        self.assertNotIn("old-single", ranked_ids)
        self.assertIn("fresh-single", ranked_ids)
        self.assertTrue({"covered-one", "covered-two"}.intersection(ranked_ids))

    def test_prepared_stories_publish_as_each_one_is_appended(self) -> None:
        batch = []
        published = []
        first = object()
        second = object()

        app.append_prepared_story(batch, first, lambda story, count: published.append((story, count)))
        app.append_prepared_story(batch, second, lambda story, count: published.append((story, count)))

        self.assertEqual(batch, [first, second])
        self.assertEqual(published, [(first, 1), (second, 2)])

    def test_gdelt_articles_become_direct_publisher_stories(self) -> None:
        payload = {
            "articles": [
                {
                    "url": "https://www.reuters.com/world/example-story/",
                    "title": "Central bank announces emergency rate decision",
                    "seendate": "20260724T193000Z",
                    "socialimage": "https://www.reuters.com/image.jpg",
                    "domain": "reuters.com",
                    "language": "English",
                    "sourcecountry": "United States",
                },
                {
                    "url": "https://example.com/non-english",
                    "title": "Noticias internacionales",
                    "seendate": "20260724T193000Z",
                    "domain": "example.com",
                    "language": "Spanish",
                },
            ]
        }

        stories = app.parse_gdelt_articles(payload)

        self.assertEqual(len(stories), 1)
        self.assertEqual(stories[0].source, "Reuters")
        self.assertEqual(stories[0].group, "GDELT")
        self.assertEqual(stories[0].published, datetime(2026, 7, 24, 19, 30, tzinfo=timezone.utc))
        self.assertIn("Business", stories[0].topics)

    def test_story_deduplication_ignores_tracking_queries(self) -> None:
        first = self.news_story(
            "first",
            "Coalition government collapses after confidence vote",
            "BBC News",
        )
        duplicate = app.Story(
            id="duplicate",
            source="BBC News",
            group="GDELT",
            title=first.title,
            link=f"{first.link}?utm_source=gdelt",
            summary_text="",
            published=first.published,
            topics=first.topics,
        )

        deduplicated = app.deduplicate_stories([first, duplicate])

        self.assertEqual(deduplicated, [first])


if __name__ == "__main__":
    unittest.main()
