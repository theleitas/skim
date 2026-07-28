import unittest
from dataclasses import replace
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

    def test_article_extraction_falls_back_to_article_paragraphs(self) -> None:
        paragraphs = "".join(
            f"<p>Ladera Ranch cancer investigators reviewed diagnosis {index} and reported "
            "new evidence to county health officials.</p>"
            for index in range(16)
        )
        page_html = f"<html><body><nav>Subscribe now</nav><article>{paragraphs}</article></body></html>"

        with patch("trafilatura.extract", return_value=""):
            extracted = app.extract_main_article_text(
                page_html,
                self.story.link,
                self.story.title,
            )

        self.assertGreaterEqual(len(extracted.split()), app.MIN_ARTICLE_WORDS)
        self.assertIn("county health officials", extracted)
        self.assertNotIn("Subscribe now", extracted)

    def test_ranked_cluster_keeps_direct_articles_as_briefing_candidates(self) -> None:
        google_story = replace(
            self.news_story(
                "google",
                "Coalition government collapses after confidence vote",
                "Google News",
                group="Aggregator",
            ),
            link="https://news.google.com/rss/articles/example",
        )
        direct_story = self.news_story(
            "direct",
            "Government coalition collapses following confidence vote",
            "BBC News",
        )

        ranked = app.rank_stories([google_story, direct_story], require_high_signal=False)

        self.assertEqual(len(ranked), 1)
        self.assertEqual(ranked[0].article_candidates[0].id, "direct")
        self.assertEqual(
            {candidate.id for candidate in ranked[0].article_candidates},
            {"google", "direct"},
        )

    def test_briefing_falls_through_to_another_article_in_the_cluster(self) -> None:
        blocked_story = self.news_story(
            "blocked",
            "Coalition government collapses after confidence vote",
            "Blocked Publisher",
        )
        readable_story = self.news_story(
            "readable",
            "Government coalition collapses following confidence vote",
            "BBC News",
        )
        ranked = app.RankedStory(
            story=blocked_story,
            cluster_key="coalition-collapse",
            references=2,
            topic_story_count=2,
            score=1.0,
            article_candidates=(blocked_story, readable_story),
        )
        evidence_text = " ".join(
            "The coalition government lost a confidence vote and ministers confirmed the transition."
            for _ in range(18)
        )
        evidence = app.ArticleEvidence(
            url=readable_story.link,
            title=readable_story.title,
            text=evidence_text,
            word_count=len(evidence_text.split()),
        )

        with (
            patch.object(app, "fetch_article_evidence", side_effect=[None, evidence]) as fetch,
            patch.object(
                app,
                "smart_summarize",
                return_value=app.SummaryAttempt(card=self.good_card(), ai_cost=0.02),
            ),
        ):
            prepared, cost = app.prepare_ranked_story(ranked, detail=3, refresh_key="test")

        self.assertIsNotNone(prepared)
        self.assertEqual(prepared.evidence.url, readable_story.link)
        self.assertEqual(prepared.article_story, readable_story)
        self.assertEqual(cost, 0.02)
        self.assertEqual(fetch.call_count, 2)

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

    def test_ai_failure_message_explains_auth_and_redacts_keys(self) -> None:
        message = app.ai_failure_message(
            "openai",
            "gpt-5.6-terra",
            RuntimeError("Incorrect API key provided: sk-secret-value"),
        )

        self.assertIn("rejected the configured API key", message)
        self.assertNotIn("sk-secret-value", message)

    def test_ai_failure_message_explains_api_credit_problem(self) -> None:
        message = app.ai_failure_message(
            "openai",
            "gpt-5.6-terra",
            RuntimeError("insufficient_quota"),
        )

        self.assertIn("no available credit", message)

    def test_headline_categories_cover_the_major_sections(self) -> None:
        stories = (
            self.news_story("conflict", "Missile attack hits military base", "BBC News"),
            self.news_story("politics", "Senate votes on White House budget plan", "NPR"),
            self.news_story("sports", "NBA finals series reaches decisive game", "CBS Sports"),
            self.news_story("tech", "Artificial intelligence company launches new model", "Tech Daily"),
            self.news_story("economy", "Central bank signals change in interest rates", "Reuters"),
        )

        categories = [app.story_category(story) for story in stories]

        self.assertEqual(
            categories,
            ["Conflict", "US Politics", "Sports", "Technology", "Economy"],
        )

    def test_coverage_outlet_text_lists_three_then_remaining_outlets(self) -> None:
        ranked = app.RankedStory(
            story=self.news_story("coverage", "A widely covered story", "BBC News"),
            cluster_key="coverage",
            references=5,
            topic_story_count=5,
            score=1.0,
            outlets=("BBC News", "Reuters", "The Guardian", "NPR", "CNN"),
        )

        self.assertEqual(
            app.coverage_outlet_text(ranked),
            "BBC News, Reuters, The Guardian · 2 more outlets",
        )

    def test_expanded_headline_font_scales_down_for_longer_text(self) -> None:
        short_desktop, short_mobile = app.expanded_headline_font_sizes(
            "Central bank cuts interest rates",
            has_image=True,
        )
        long_desktop, long_mobile = app.expanded_headline_font_sizes(
            "Central bank unexpectedly cuts interest rates as global markets confront renewed volatility",
            has_image=True,
        )
        full_width_desktop, _ = app.expanded_headline_font_sizes(
            "Central bank unexpectedly cuts interest rates as global markets confront renewed volatility",
            has_image=False,
        )

        self.assertLess(long_desktop, short_desktop)
        self.assertLess(long_mobile, short_mobile)
        self.assertGreaterEqual(full_width_desktop, long_desktop)
        self.assertGreaterEqual(long_desktop, 1.0)
        self.assertGreaterEqual(long_mobile, 0.9)

    def test_category_prefers_headline_signals_over_incidental_summary_words(self) -> None:
        economy_story = self.news_story(
            "economy-headline",
            "Oil price slides as AstraZeneca beats profit forecasts",
            "The Guardian World",
        )
        economy_story = replace(
            economy_story,
            summary_text="The live page also links to film, music, and television coverage.",
        )
        conflict_story = self.news_story(
            "conflict-headline",
            "Israeli forces enter West Bank towns as settler violence worsens",
            "Al Jazeera",
        )

        self.assertEqual(app.story_category(economy_story), "Economy")
        self.assertEqual(app.story_category(conflict_story), "Conflict")


if __name__ == "__main__":
    unittest.main()
