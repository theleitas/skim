import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
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

    def test_feed_combines_article_and_media_descriptions_for_single_source_evidence(self) -> None:
        xml = b"""
        <rss xmlns:media="http://search.yahoo.com/mrss/">
          <channel>
            <item>
              <title>Investigation finds historical abuse at Michigan arts school</title>
              <link>https://example.com/interlochen-investigation</link>
              <description>
                A law firm hired by Interlochen to examine alumni complaints fielded dozens
                of disturbing accounts, primarily from decades ago.
              </description>
              <media:description>
                After an alumnus complained, the school opened an investigation into allegations
                that teachers and other staff had acted inappropriately with students.
              </media:description>
            </item>
          </channel>
        </rss>
        """
        source = app.NewsSource(
            "NYT Top Stories",
            "https://example.com/feed",
            "Major News",
            ("World",),
        )

        stories, error = app.parse_source_feed(source, xml)

        self.assertIsNone(error)
        self.assertEqual(len(stories), 1)
        self.assertIn("dozens of disturbing accounts", stories[0].summary_text)
        self.assertIn("teachers and other staff", stories[0].summary_text)
        self.assertIsNotNone(app.feed_story_evidence(stories[0]))

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

    def test_google_news_cluster_exposes_each_underlying_publisher(self) -> None:
        cluster_html = """
        <ol>
          <li>
            <a href="https://news.google.com/rss/articles/bbc">Board of Peace deal explained</a>
            <font color="#6f6f6f">BBC</font>
          </li>
          <li>
            <a href="https://news.google.com/rss/articles/cnn">What the Board of Peace does</a>
            <font color="#6f6f6f">CNN</font>
          </li>
        </ol>
        """

        links = app.google_news_cluster_links(cluster_html)

        self.assertEqual(
            links,
            (
                (
                    "Board of Peace deal explained",
                    "https://news.google.com/rss/articles/bbc",
                    "BBC",
                ),
                (
                    "What the Board of Peace does",
                    "https://news.google.com/rss/articles/cnn",
                    "CNN",
                ),
            ),
        )

    def test_briefing_search_adds_the_subject_of_an_explainer_question(self) -> None:
        phrases = app.briefing_search_phrases(
            "What Is the Board of Peace? The Trump-Backed Group, Explained"
        )

        self.assertIn('"Board of Peace"', phrases)
        self.assertIn(
            '"What Is the Board of Peace? The Trump-Backed Group, Explained"',
            phrases,
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

    def test_briefing_searches_other_publishers_when_stored_articles_are_blocked(self) -> None:
        blocked_story = self.news_story(
            "blocked-explainer",
            "What Is the Board of Peace? The Trump-Backed Group, Explained",
            "NYT Top Stories",
        )
        alternate_story = replace(
            self.news_story(
                "readable-explainer",
                "Board of Peace deal and international role explained",
                "BBC",
                group="Aggregator",
            ),
            link="https://news.google.com/rss/articles/readable-explainer",
        )
        ranked = app.RankedStory(
            story=blocked_story,
            cluster_key="board-of-peace",
            references=12,
            topic_story_count=17,
            score=1.0,
            article_candidates=(blocked_story,),
        )
        evidence_text = " ".join(
            "The Board of Peace is an international group created to coordinate a Gaza agreement."
            for _ in range(18)
        )
        evidence = app.ArticleEvidence(
            url="https://www.bbc.com/news/articles/board-of-peace",
            title=alternate_story.title,
            text=evidence_text,
            word_count=len(evidence_text.split()),
        )

        with (
            patch.object(
                app,
                "fetch_google_news_briefing_candidates",
                return_value=(alternate_story,),
            ) as discover,
            patch.object(
                app,
                "fetch_article_evidence",
                side_effect=[None, evidence],
            ) as fetch,
            patch.object(
                app,
                "smart_summarize",
                return_value=app.SummaryAttempt(card=self.good_card(), ai_cost=0.02),
            ),
        ):
            prepared, cost = app.prepare_ranked_story(ranked, detail=3, refresh_key="test")

        self.assertIsNotNone(prepared)
        self.assertEqual(prepared.article_story, alternate_story)
        self.assertEqual(prepared.evidence.url, evidence.url)
        self.assertEqual(cost, 0.02)
        discover.assert_called_once_with(blocked_story.title, blocked_story.topics)
        self.assertEqual(fetch.call_count, 2)

    def test_briefing_uses_one_publishers_feed_text_when_page_is_blocked(self) -> None:
        feed_story = replace(
            self.news_story(
                "single-feed-source",
                "Investigation finds historical abuse at Michigan arts school",
                "NYT Top Stories",
            ),
            summary_text=(
                "A law firm hired by the school to examine alumni complaints fielded dozens "
                "of disturbing accounts, primarily from decades ago. After an alumnus complained, "
                "the school opened an investigation into allegations that teachers and other staff "
                "had acted inappropriately with students."
            ),
        )
        ranked = app.RankedStory(
            story=feed_story,
            cluster_key="single-feed-source",
            references=1,
            topic_story_count=1,
            score=1.0,
            article_candidates=(feed_story,),
        )

        with (
            patch.object(app, "fetch_article_evidence", return_value=None),
            patch.object(
                app,
                "smart_summarize",
                return_value=app.SummaryAttempt(card=self.good_card(), ai_cost=0.02),
            ) as summarize,
        ):
            prepared, cost = app.prepare_ranked_story(ranked, detail=3, refresh_key="test")

        self.assertIsNotNone(prepared)
        self.assertEqual(prepared.article_story, feed_story)
        self.assertGreaterEqual(prepared.evidence.word_count, app.MIN_FEED_EVIDENCE_WORDS)
        self.assertEqual(cost, 0.02)
        self.assertEqual(summarize.call_args.args[-1], feed_story)

    def test_briefing_continues_after_first_ai_draft_fails_quality(self) -> None:
        first_story = self.news_story(
            "first-draft-fails",
            "Coalition government collapses after confidence vote",
            "First Publisher",
        )
        second_story = self.news_story(
            "second-draft-works",
            "Government coalition collapses following confidence vote",
            "Second Publisher",
        )
        ranked = app.RankedStory(
            story=first_story,
            cluster_key="coalition-collapse",
            references=2,
            topic_story_count=2,
            score=1.0,
            article_candidates=(first_story, second_story),
        )
        evidence_text = " ".join(
            "The coalition lost a confidence vote and ministers confirmed the transition."
            for _ in range(18)
        )
        first_evidence = app.ArticleEvidence(
            url=first_story.link,
            title=first_story.title,
            text=evidence_text,
            word_count=len(evidence_text.split()),
        )
        second_evidence = replace(first_evidence, url=second_story.link, title=second_story.title)

        with (
            patch.object(
                app,
                "fetch_article_evidence",
                side_effect=[first_evidence, second_evidence],
            ),
            patch.object(
                app,
                "smart_summarize",
                side_effect=[
                    app.SummaryAttempt(card=None, ai_cost=0.01),
                    app.SummaryAttempt(card=self.good_card(), ai_cost=0.02),
                ],
            ) as summarize,
        ):
            prepared, cost = app.prepare_ranked_story(ranked, detail=3, refresh_key="test")

        self.assertIsNotNone(prepared)
        self.assertEqual(prepared.article_story, second_story)
        self.assertAlmostEqual(cost, 0.03)
        self.assertEqual(summarize.call_count, 2)

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

    def test_plain_language_guidance_simplifies_without_losing_accuracy(self) -> None:
        guidance = app.summary_readability_guidance(True)

        self.assertIn("20% more simply", guidance)
        self.assertIn("Explain unavoidable jargon", guidance)
        self.assertIn("Preserve important names, numbers, dates, uncertainty, and nuance", guidance)

    def test_standard_readability_guidance_restores_news_prose(self) -> None:
        guidance = app.summary_readability_guidance(False)

        self.assertIn("general-audience news prose", guidance)
        self.assertNotIn("20% more simply", guidance)

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
        self.assertNotIn("Learn More", attempt.card)
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

    def test_deep_analysis_exposes_openai_usage_cost(self) -> None:
        evidence_text = " ".join(
            "Officials confirmed the policy change and described its consequences."
            for _ in range(30)
        )
        evidence = app.ArticleEvidence(
            url=self.story.link,
            title=self.story.title,
            text=evidence_text,
            word_count=len(evidence_text.split()),
        )
        ai_result = {
            "analysis": "The decision changes how local officials will evaluate the reported cases.",
            "watch_next": "Watch for the state epidemiology review.",
            "research": "Study cancer-cluster methodology.",
            "__usage_input_tokens": "2000",
            "__usage_output_tokens": "500",
            "__usage_cached_input_tokens": "0",
            "__usage_cache_write_tokens": "0",
        }

        with (
            patch.object(app, "configured_ai_provider", return_value="openai"),
            patch.object(app, "ai_deep_analysis_cached", return_value=ai_result),
        ):
            result = app.deeper_analysis(self.story, evidence)

        self.assertGreater(app.card_ai_cost(result), 0)
        self.assertEqual(result["__research_topic"], "Study cancer-cluster methodology.")
        self.assertEqual(result["Research trail"], "Study cancer-cluster methodology.")
        self.assertNotIn("Learn More", result)
        self.assertNotIn("Learn more:", result["Research trail"])

    def test_legacy_learn_more_content_is_discarded(self) -> None:
        analysis = {
            "Research trail": (
                "Understand cancer-cluster methodology. "
                "Learn more: [CDC health topics](https://www.cdc.gov/health-topics.html)"
            ),
            "Learn More": "Learn more: [Public health](https://example.com)",
        }

        normalized = app.normalize_research_analysis(analysis)

        self.assertEqual(
            normalized["Research trail"],
            "Understand cancer-cluster methodology.",
        )
        self.assertNotIn("Learn More", normalized)

    def test_ai_cost_ledger_survives_a_file_round_trip(self) -> None:
        ledger = {
            "total_micros": 12345,
            "latest_micros": 345,
            "total_articles": 8,
            "latest_articles": 1,
            "updated_at": 1722366000000,
            "events": ["0123456789abcdef"],
            "history": [
                {
                    "token": "0123456789abcdef",
                    "at": 1722366000000,
                    "cost_micros": 345,
                    "articles": 1,
                    "label": "Story brief",
                    "model": "gpt-5.6-terra",
                }
            ],
        }

        with TemporaryDirectory() as temporary_directory:
            ledger_path = Path(temporary_directory) / "ai-cost.json"
            with patch.object(app, "AI_COST_LEDGER_PATH", ledger_path):
                app.write_ai_cost_ledger(ledger)
                restored = app.read_ai_cost_ledger()

        self.assertEqual(restored, ledger)

    def test_legacy_ai_cost_ledger_adds_an_empty_history(self) -> None:
        normalized = app.normalize_ai_cost_ledger(
            {
                "total_micros": 12345,
                "latest_micros": 345,
                "events": ["0123456789abcdef"],
            }
        )

        self.assertEqual(normalized["history"], [])

    def test_ai_cost_history_keeps_the_latest_ten_unique_calls(self) -> None:
        history = [
            {
                "token": f"{index:016x}",
                "at": index,
                "cost_micros": index + 1,
                "articles": 1,
                "label": "Story brief",
                "model": "gpt-5.6-terra",
            }
            for index in range(12)
        ]

        merged = app.merge_ai_cost_history(history)

        self.assertEqual(len(merged), 10)
        self.assertEqual(merged[0]["token"], f"{2:016x}")
        self.assertEqual(merged[-1]["token"], f"{11:016x}")

    def test_ai_cost_event_details_identifies_interactive_calls(self) -> None:
        self.assertEqual(
            app.ai_cost_event_details("deep-batch-story", 1),
            ("Deep analysis", app.OPENAI_DEEP_MODEL),
        )
        self.assertEqual(
            app.ai_cost_event_details("question-batch-story", 1),
            ("Story question", app.OPENAI_DEEP_MODEL),
        )

    def test_keyword_slot_locks_and_only_clears_explicitly(self) -> None:
        state = {
            "keyword_draft_3": "  Climate policy  ",
            "saved_keyword_3": "",
            "last_settings": ("old",),
        }
        query_params = {}
        with (
            patch.object(app.st, "session_state", state),
            patch.object(app.st, "query_params", query_params),
        ):
            app.lock_keyword_slot(3)

            self.assertEqual(state["saved_keyword_3"], "Climate policy")
            self.assertEqual(query_params["kw4"], "Climate policy")
            self.assertIsNone(state["last_settings"])

            app.clear_keyword_slot(3)

        self.assertEqual(state["saved_keyword_3"], "")
        self.assertEqual(state["keyword_draft_3"], "")
        self.assertNotIn("kw4", query_params)

    def test_each_keyword_adds_one_unique_headline_after_the_base_batch(self) -> None:
        def ranked(story_id: str, cluster_key: str, title: str) -> app.RankedStory:
            return app.RankedStory(
                story=self.news_story(story_id, title, "Keyword Search", group="Custom"),
                cluster_key=cluster_key,
                references=1,
                topic_story_count=1,
                score=10,
            )

        base = [ranked("base", "shared-cluster", "Existing semiconductor headline")]
        keyword_rankings = {
            "semiconductors": [
                ranked("duplicate", "shared-cluster", "Duplicate semiconductor headline"),
                ranked("chips", "chip-cluster", "Chipmakers announce new factories"),
            ],
            "Ukraine": [
                ranked("ukraine", "ukraine-cluster", "Ukraine talks resume in Geneva"),
            ],
        }

        combined = app.append_keyword_headlines(base, keyword_rankings, set())

        self.assertEqual(
            [item.cluster_key for item in combined],
            ["shared-cluster", "chip-cluster", "ukraine-cluster"],
        )

    def test_headline_selection_reserves_the_strongest_story_in_each_category(self) -> None:
        def ranked(
            story_id: str,
            title: str,
            score: float,
        ) -> app.RankedStory:
            return app.RankedStory(
                story=self.news_story(story_id, title, "Major News"),
                cluster_key=f"{story_id}-cluster",
                references=max(1, int(score // 100)),
                topic_story_count=max(1, int(score // 100)),
                score=score,
            )

        candidates = [
            ranked("conflict-top", "Missile attack hits military base", 1_000),
            ranked("conflict-next", "Drone strike damages weapons depot", 900),
            ranked("world", "Global leaders meet for humanitarian aid summit", 100),
            ranked("politics", "Senate votes on White House budget plan", 80),
            ranked("sports", "NBA finals series reaches decisive game", 70),
            ranked("entertainment", "Actor wins major film award", 60),
            ranked("technology", "Artificial intelligence company launches new model", 50),
            ranked("economy", "Central bank signals change in interest rates", 40),
        ]

        reserved = app.select_balanced_headlines(candidates, set(), limit=7)
        filled = app.select_balanced_headlines(candidates, set(), limit=8)

        self.assertEqual(
            {app.story_category(item.story) for item in reserved},
            set(app.CATEGORY_COLORS),
        )
        self.assertIn("conflict-top", {item.story.id for item in reserved})
        self.assertNotIn("conflict-next", {item.story.id for item in reserved})
        self.assertEqual(filled[-1].story.id, "conflict-next")

    def test_batch_timestamp_is_displayed_in_est(self) -> None:
        with patch.object(
            app.st,
            "session_state",
            {"batch_refreshed_at": "2026-01-15T17:30:00+00:00"},
        ):
            label = app.batch_refreshed_label()

        self.assertEqual(label, "Jan 15, 2026 at 12:30 PM EST")

    def test_headline_legend_renders_once_into_its_placeholder(self) -> None:
        calls = []
        target = SimpleNamespace(
            markdown=lambda *args, **kwargs: calls.append((args, kwargs))
        )

        with patch.object(
            app,
            "batch_refreshed_label",
            return_value="Jul 31, 2026 at 9:22 PM EST",
        ):
            app.render_headline_legend(target)

        self.assertEqual(len(calls), 1)
        markup = calls[0][0][0]
        self.assertEqual(markup.count('class="headline-legend"'), 1)
        self.assertEqual(markup.count('class="category-legend"'), 1)
        self.assertIn("Headlines updated as of Jul 31, 2026 at 9:22 PM EST", markup)

    def test_story_question_answer_is_short_grounded_and_costed(self) -> None:
        evidence_text = " ".join(
            "Officials described the investigation and the evidence needed for the next decision."
            for _ in range(30)
        )
        prepared = app.PreparedStory(
            ranked_story=app.RankedStory(
                story=self.story,
                cluster_key="question-cluster",
                references=2,
                topic_story_count=2,
                score=10,
            ),
            evidence=app.ArticleEvidence(
                url=self.story.link,
                title=self.story.title,
                text=evidence_text,
                word_count=len(evidence_text.split()),
            ),
            card={
                "__headline": "Officials begin review of childhood cancer cases",
                "": self.good_card()["summary"],
                "Background": self.good_card()["background"],
            },
        )
        ai_result = {
            "answer": (
                "A reported cluster does not prove that one exposure caused the illnesses. "
                "Investigators compare the observed cases with the number normally expected for "
                "a similar population, then study timing, diagnoses, and possible shared exposures. "
                "A rate above the expected baseline would justify a more focused investigation."
            ),
            "__usage_input_tokens": "1600",
            "__usage_output_tokens": "180",
            "__usage_cached_input_tokens": "0",
            "__usage_cache_write_tokens": "0",
        }

        with (
            patch.object(app.st, "session_state", SimpleNamespace(deep_analyses={})),
            patch.object(app, "configured_ai_provider", return_value="openai"),
            patch.object(app, "ai_model", return_value="gpt-5.6-terra"),
            patch.object(app, "ai_story_question_cached", return_value=ai_result),
        ):
            result = app.answer_story_question(prepared, "Does a cluster prove a common cause?")

        self.assertIn(app.sentence_count(result["answer"]), (3, 4))
        self.assertGreater(app.card_ai_cost(result), 0)

    def test_research_topic_falls_back_to_visible_research_trail(self) -> None:
        analysis = {
            "Research trail": (
                "Understand cancer-cluster methodology. "
                "Learn more: [CDC health topics](https://www.cdc.gov/health-topics.html)"
            )
        }

        topic = app.research_topic_from_analysis(analysis)

        self.assertEqual(topic, "Understand cancer-cluster methodology.")

    def test_further_research_link_is_a_complete_topic_sentence(self) -> None:
        analysis = {"__research_topic": "Understand cancer-cluster methodology"}

        link_text = app.further_research_link_text(analysis)

        self.assertEqual(link_text, "Understand cancer-cluster methodology.")

    def test_ai_working_markup_uses_the_multicolor_progress_bar(self) -> None:
        markup = app.ai_working_markup("Building brief using AI (gpt-test)...")

        self.assertIn('class="ai-working-bar"', markup)
        self.assertIn("Building brief using AI (gpt-test)...", markup)
        self.assertLess(markup.index("ai-working-copy"), markup.index("ai-working-bar"))
        self.assertNotIn("ai-working-newspaper", markup)
        self.assertNotIn("ai-working-lightbulb", markup)

    def test_ai_working_wrapper_reserves_space_for_the_full_bar(self) -> None:
        with patch.object(app.st, "markdown") as markdown:
            app.page_style()

        css = markdown.call_args.args[0]
        self.assertIn(':has(.ai-working-box)', css)
        self.assertIn("min-height: 2.75rem !important", css)
        self.assertIn("overflow: hidden", css)

    def test_expanded_story_sections_reserve_space_for_visible_borders(self) -> None:
        with patch.object(app.st, "markdown") as markdown:
            app.page_style()

        css = markdown.call_args.args[0]
        self.assertIn("--skim-section-gap: 0.3rem", css)
        self.assertIn(
            '[data-testid="stMarkdownContainer"]:has(.summary-grid)',
            css,
        )
        self.assertIn(
            '> [data-testid="stElementContainer"]:last-child',
            css,
        )
        self.assertGreaterEqual(css.count("margin-bottom: 0 !important"), 2)

    def test_research_topic_brief_is_simple_and_tracks_openai_cost(self) -> None:
        evidence_text = " ".join(
            "Officials asked epidemiologists to review whether the reported cases exceed expectations."
            for _ in range(30)
        )
        evidence = app.ArticleEvidence(
            url=self.story.link,
            title=self.story.title,
            text=evidence_text,
            word_count=len(evidence_text.split()),
        )
        analysis = {
            "Deeper analysis": "Officials must compare observed cases with the expected local rate.",
            "Watch next": "Watch for the state epidemiology review.",
            "__research_topic": "Cancer-cluster methodology",
        }
        ai_result = {
            "brief": (
                "A cancer cluster is an unusual number of similar cancers found in a defined "
                "place and time. Investigators first compare the observed cases with the number "
                "normally expected for people of the same ages and backgrounds. They also check "
                "whether the diagnoses share a cancer type, exposure, or another plausible link. "
                "That process helps separate a meaningful pattern from a chance grouping of rare cases."
            ),
            "__usage_input_tokens": "1800",
            "__usage_output_tokens": "220",
            "__usage_cached_input_tokens": "0",
            "__usage_cache_write_tokens": "0",
        }

        with (
            patch.object(app, "configured_ai_provider", return_value="openai"),
            patch.object(app, "ai_research_brief_cached", return_value=ai_result),
        ):
            result = app.research_topic_brief(self.story, evidence, analysis)

        self.assertEqual(app.sentence_count(result["Research brief"]), 4)
        self.assertGreater(app.card_ai_cost(result), 0)

    def test_ai_cost_counter_does_not_count_same_batch_twice(self) -> None:
        first_total, first_changed = app.accumulate_ai_cost(0, set(), "batch-1", 0.25)
        recorded = {app.ai_cost_event_token("batch-1")}
        second_total, second_changed = app.accumulate_ai_cost(
            first_total,
            recorded,
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

    def test_expanded_free_source_catalog_includes_global_and_specialist_feeds(self) -> None:
        sources = {source.name: source for source in app.NEWS_SOURCES}

        self.assertTrue(
            {
                "Drudge Report",
                "PBS News",
                "Sky News",
                "Deutsche Welle",
                "France 24",
                "Euronews",
                "CBC News",
                "ABC Australia",
                "RNZ",
                "ProPublica",
                "Politico",
                "TechCrunch",
                "The Verge",
                "ESPN",
                "MarketWatch",
                "Variety",
                "NASA",
            }.issubset(sources)
        )
        self.assertEqual(sources["Drudge Report"].group, "Aggregator")
        self.assertEqual(sources["ESPN"].topics, ("Sports",))

    def test_drudge_feed_uses_direct_publisher_link_without_inflating_outlets(self) -> None:
        source = app.NewsSource(
            "Drudge Report",
            "https://feedpress.me/drudgereportfeed",
            "Aggregator",
            ("World", "US"),
            item_limit=5,
        )
        feed = b"""
        <rss><channel>
          <item>
            <title>Major policy reversal rocks Washington</title>
            <link>https://feedpress.me/link/20202/example</link>
            <description><![CDATA[<img src="https://example.com/photo.jpg">]]></description>
            <guid>https://www.washingtonpost.com/politics/2026/07/27/policy-reversal/</guid>
            <pubDate>Mon, 27 Jul 2026 20:00:00 GMT</pubDate>
          </item>
          <item>
            <title>Paywall mirror should not enter Skim</title>
            <link>https://feedpress.me/link/20202/archive</link>
            <guid>https://archive.ph/example</guid>
            <pubDate>Mon, 27 Jul 2026 20:00:00 GMT</pubDate>
          </item>
        </channel></rss>
        """

        stories, error = app.parse_source_feed(source, feed)

        self.assertIsNone(error)
        self.assertEqual(len(stories), 1)
        self.assertEqual(
            stories[0].link,
            "https://www.washingtonpost.com/politics/2026/07/27/policy-reversal/",
        )
        self.assertEqual(stories[0].source, "Washington Post via Drudge")
        self.assertEqual(stories[0].summary_text, "")
        self.assertEqual(stories[0].image_url, "https://example.com/photo.jpg")
        self.assertEqual(
            app.outlet_identity(stories[0].source),
            app.outlet_identity("Washington Post"),
        )

    def test_single_aggregator_pick_ranks_below_direct_reporting(self) -> None:
        direct = self.news_story(
            "direct-report",
            "Government announces emergency policy reversal",
            "Washington Post",
        )
        aggregator = replace(
            direct,
            id="aggregator-report",
            source="Washington Post via Drudge",
            group="Aggregator",
        )

        direct_score = app.story_score(direct, references=1, cluster_size=1)
        aggregator_score = app.story_score(aggregator, references=1, cluster_size=1)

        self.assertAlmostEqual(direct_score - aggregator_score, 14.0, places=5)

    def test_parallel_feed_batch_preserves_source_filters_and_keywords(self) -> None:
        major = app.NewsSource("Major", "https://example.com/major", "Major News", ("World",))
        aggregator = app.NewsSource(
            "Aggregator",
            "https://example.com/aggregator",
            "Aggregator",
            ("World",),
        )
        social = app.NewsSource("Social", "https://example.com/social", "Social", ("World",))

        def fake_fetch(source: app.NewsSource) -> tuple[list[app.Story], None]:
            story = app.Story(
                id=source.name,
                source=source.name,
                group=source.group,
                title=f"{source.name} reports a major international development",
                link=source.url,
                summary_text="",
                published=datetime.now(timezone.utc),
                topics=source.topics,
            )
            return [story], None

        with (
            patch.object(app, "NEWS_SOURCES", (major, aggregator, social)),
            patch.object(app, "fetch_source", side_effect=fake_fetch) as fetch,
        ):
            stories, errors = app.fetch_stories(
                ("World",),
                include_aggregators=False,
                include_social=False,
                custom_keywords=("semiconductors",),
                include_gdelt=False,
            )

        self.assertEqual(errors, [])
        self.assertEqual({story.source for story in stories}, {"Major", "Keyword: semiconductors"})
        self.assertEqual(
            {call.args[0].name for call in fetch.call_args_list},
            {"Major", "Keyword: semiconductors"},
        )

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

    def test_coverage_outlet_text_lists_first_then_remaining_outlets(self) -> None:
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
            "BBC News · 4 more outlets",
        )

        credited = replace(
            ranked,
            outlets=("© Photojournalist Name, Reuters", "BBC News", "NPR"),
            references=3,
        )
        self.assertEqual(app.coverage_outlet_text(credited), "Reuters · 2 more outlets")

    def test_headline_display_sort_is_newest_first(self) -> None:
        newest = app.RankedStory(
            story=self.news_story("newest", "Newest story", "BBC News", hours_ago=0.2),
            cluster_key="newest",
            references=1,
            topic_story_count=1,
            score=1.0,
        )
        older = app.RankedStory(
            story=self.news_story("older", "Older story", "Reuters", hours_ago=4),
            cluster_key="older",
            references=5,
            topic_story_count=5,
            score=100.0,
        )
        undated = app.RankedStory(
            story=replace(self.news_story("undated", "Undated story", "NPR"), published=None),
            cluster_key="undated",
            references=3,
            topic_story_count=3,
            score=50.0,
        )

        sorted_stories = app.sort_headlines_by_age([older, undated, newest])

        self.assertEqual(
            [ranked.story.id for ranked in sorted_stories],
            ["newest", "older", "undated"],
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
        self.assertGreaterEqual(long_mobile, 0.72)

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
