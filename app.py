from __future__ import annotations

import html
import json
import os
import re
import textwrap
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Iterable

import streamlit as st


APP_NAME = "Skim"
BATCH_SIZE = 15
ITEMS_PER_SOURCE = 50
FEED_TIMEOUT_SECONDS = 15
RESEARCH_TIMEOUT_SECONDS = 8
MIN_SUMMARY_WORDS = 18
MIN_NEW_SUMMARY_TERMS = 7
NO_REPEAT_HOURS = 48
OPENAI_SUMMARY_MODEL = "gpt-5.6-terra"
OPENAI_DEEP_MODEL = "gpt-5.6-terra"
AI_SUMMARY_PROMPT_VERSION = "composed-card-v4"
GEMINI_SUMMARY_MODEL = "gemini-2.5-flash"
GEMINI_DEEP_MODEL = "gemini-2.5-pro"
GROQ_SUMMARY_MODEL = "llama-3.3-70b-versatile"
GROQ_DEEP_MODEL = "llama-3.3-70b-versatile"
XAI_SUMMARY_MODEL = "grok-4.20-0309-non-reasoning"
XAI_DEEP_MODEL = "grok-4.5"
OPENAI_MODEL_PRICES_PER_MTOK = {
    "gpt-5.6-luna": (1.00, 6.00),
    "gpt-5.6-terra": (2.50, 15.00),
    "gpt-5.6-sol": (5.00, 30.00),
    "gpt-5.6": (5.00, 30.00),
}
REQUEST_HEADERS = {
    "User-Agent": "SkimPersonalNews/0.1 (+https://github.com/theleitas)",
}


@dataclass(frozen=True)
class NewsSource:
    name: str
    url: str
    group: str
    topics: tuple[str, ...]


@dataclass(frozen=True)
class Story:
    id: str
    source: str
    group: str
    title: str
    link: str
    summary_text: str
    published: datetime | None
    topics: tuple[str, ...]
    image_url: str | None = None


@dataclass(frozen=True)
class RankedStory:
    story: Story
    cluster_key: str
    references: int
    topic_story_count: int
    score: float


TOPICS = {
    "World": ("world", "war", "conflict", "diplomacy", "election", "government"),
    "US": ("u.s.", "us ", "america", "congress", "white house", "supreme court"),
    "Politics": ("politic", "election", "senate", "president", "minister", "policy"),
    "Business": ("business", "company", "earnings", "market", "economy", "trade"),
    "Tech": ("technology", "software", "startup", "semiconductor", "cyber"),
    "AI": (" ai ", "artificial intelligence", "openai", "model", "chatbot"),
    "Science": ("science", "space", "research", "study", "nasa", "physics"),
    "Climate": ("climate", "weather", "emissions", "energy", "warming"),
    "Health": ("health", "disease", "drug", "vaccine", "hospital", "medicine"),
    "Culture": ("film", "music", "book", "culture", "art", "media"),
    "Sports": ("sport", "nba", "nfl", "mlb", "soccer", "tennis", "golf"),
    "Reddit Hot": ("reddit",),
    "Hacker News": ("hacker news", "startup", "programming", "developer"),
}

NEWS_SOURCES = (
    NewsSource("BBC World", "https://feeds.bbci.co.uk/news/world/rss.xml", "Major News", ("World",)),
    NewsSource("BBC Top Stories", "https://feeds.bbci.co.uk/news/rss.xml", "Major News", ("World", "US")),
    NewsSource("NPR News", "https://feeds.npr.org/1001/rss.xml", "Major News", ("US", "Politics", "Culture")),
    NewsSource("The Guardian World", "https://www.theguardian.com/world/rss", "Major News", ("World", "Politics")),
    NewsSource("The Guardian US", "https://www.theguardian.com/us-news/rss", "Major News", ("US", "Politics")),
    NewsSource("Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml", "Major News", ("World",)),
    NewsSource("NYT Top Stories", "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml", "Major News", ("World", "US")),
    NewsSource("NYT World", "https://rss.nytimes.com/services/xml/rss/nyt/World.xml", "Major News", ("World",)),
    NewsSource("NYT Technology", "https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml", "Major News", ("Tech", "AI")),
    NewsSource("CNN Top Stories", "http://rss.cnn.com/rss/cnn_topstories.rss", "Major News", ("World", "US")),
    NewsSource("ABC News", "https://abcnews.go.com/abcnews/topstories", "Major News", ("US", "World")),
    NewsSource("CBS News", "https://www.cbsnews.com/latest/rss/main", "Major News", ("US", "World")),
    NewsSource("Google News Top", "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en", "Aggregator", ("World", "US")),
    NewsSource("Google News Business", "https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=en-US&gl=US&ceid=US:en", "Aggregator", ("Business",)),
    NewsSource("Google News Technology", "https://news.google.com/rss/headlines/section/topic/TECHNOLOGY?hl=en-US&gl=US&ceid=US:en", "Aggregator", ("Tech", "AI")),
    NewsSource("Google News Science", "https://news.google.com/rss/headlines/section/topic/SCIENCE?hl=en-US&gl=US&ceid=US:en", "Aggregator", ("Science",)),
    NewsSource("Google News Health", "https://news.google.com/rss/headlines/section/topic/HEALTH?hl=en-US&gl=US&ceid=US:en", "Aggregator", ("Health",)),
    NewsSource("Reddit r/news", "https://www.reddit.com/r/news/hot/.rss", "Social", ("Reddit Hot", "US", "World")),
    NewsSource("Reddit r/worldnews", "https://www.reddit.com/r/worldnews/hot/.rss", "Social", ("Reddit Hot", "World")),
    NewsSource("Reddit r/technology", "https://www.reddit.com/r/technology/hot/.rss", "Social", ("Reddit Hot", "Tech")),
    NewsSource("Reddit r/artificial", "https://www.reddit.com/r/artificial/hot/.rss", "Social", ("Reddit Hot", "AI")),
    NewsSource("Hacker News", "https://news.ycombinator.com/rss", "Social", ("Hacker News", "Tech", "AI")),
)

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from", "has",
    "have", "he", "her", "his", "in", "is", "it", "its", "new", "of", "on", "or",
    "said", "says", "she", "that", "the", "their", "this", "to", "was", "were",
    "with", "you", "after", "about", "over", "into", "latest", "live", "updates",
    "how", "why", "what", "when", "where", "who", "more", "than",
}


def page_style() -> None:
    st.markdown(
        """
        <style>
            :root {
                --skim-ink: #f6f3ed;
                --skim-muted: #b5aea3;
                --skim-border: #3d3934;
                --skim-paper: #000000;
                --skim-card: #11100f;
                --skim-accent: #f1c45b;
                --skim-green: #77d2a1;
            }

            .stApp {
                background: #000000;
                color: var(--skim-ink);
            }

            [data-testid="stAppViewContainer"] > .main {
                padding-top: 1.2rem;
            }

            .block-container {
                max-width: 860px;
                padding-left: 1.1rem;
                padding-right: 1.1rem;
            }

            h1, h2, h3, p {
                letter-spacing: 0;
            }

            .skim-header {
                display: flex;
                align-items: end;
                justify-content: space-between;
                gap: 1rem;
                border-bottom: 1px solid #2f2b25;
                padding-bottom: 0.9rem;
                margin-bottom: 1rem;
            }

            .skim-brand {
                font-size: 2.2rem;
                line-height: 1;
                font-weight: 800;
            }

            .skim-tagline {
                color: var(--skim-muted);
                font-size: 0.95rem;
                margin-top: 0.25rem;
            }

            .skim-pill {
                border: 1px solid var(--skim-border);
                border-radius: 999px;
                padding: 0.35rem 0.7rem;
                background: #151412;
                color: #ddd5c8;
                font-size: 0.82rem;
                white-space: nowrap;
            }

            [data-testid="stVerticalBlockBorderWrapper"] {
                background:
                    linear-gradient(145deg, rgba(255, 255, 255, 0.055), rgba(255, 255, 255, 0.018)),
                    var(--skim-card);
                border: 1px solid #4a443c;
                border-left: 4px solid var(--skim-accent);
                border-radius: 8px;
                box-shadow: 0 18px 44px rgba(0, 0, 0, 0.42);
            }

            .story-meta {
                display: flex;
                flex-wrap: wrap;
                gap: 0.45rem;
                color: var(--skim-muted);
                font-size: 0.78rem;
                text-transform: uppercase;
                letter-spacing: 0;
                margin-bottom: 0.45rem;
            }

            .story-title {
                font-size: clamp(0.82rem, 1.2vw, 0.96rem);
                line-height: 1.5;
                margin: 0 0 0.85rem 0;
                color: var(--skim-ink);
                max-width: 34rem;
                display: -webkit-box;
                -webkit-box-orient: vertical;
                -webkit-line-clamp: 3;
                overflow: hidden;
            }

            .story-title-full {
                max-width: none;
            }

            .story-image {
                display: block;
                width: 100%;
                aspect-ratio: 4 / 3;
                object-fit: cover;
                border: 0;
                border-radius: 12px;
            }

            .story-source {
                color: #9d968d;
                font-size: 0.76rem;
                font-style: italic;
                line-height: 1.35;
                margin-top: 0.75rem;
                margin-bottom: 0.45rem;
            }

            .summary-grid {
                display: grid;
                grid-template-columns: 1fr;
                gap: 0.7rem;
                color: #ebe5da;
                font-size: 0.95rem;
                line-height: 1.5;
                background: #171512;
                border: 1px solid #373229;
                border-radius: 8px;
                padding: 0.85rem 0.9rem;
                margin-bottom: 0.95rem;
            }

            .summary-grid b {
                color: var(--skim-ink);
            }

            .summary-field {
                border-top: 1px solid #332f29;
                padding-top: 0.62rem;
            }

            .summary-field:first-child {
                border-top: 0;
                padding-top: 0;
            }

            .lesson-link {
                display: inline-flex;
                align-items: center;
                border: 1px solid #6b613d;
                border-radius: 999px;
                background: #1e1b12;
                color: #f7d66e;
                padding: 0.1rem 0.38rem;
                margin: 0.08rem 0.12rem 0.08rem 0;
                font-size: 0.72rem;
                line-height: 1.15;
                text-decoration: none;
                white-space: nowrap;
                box-shadow: none;
            }

            .learn-more-row {
                display: flex;
                align-items: center;
                flex-wrap: wrap;
                gap: 0.16rem;
                margin-top: 0.46rem;
            }

            .learn-more-label {
                color: var(--skim-muted);
                font-size: 0.72rem;
                font-weight: 700;
                margin-right: 0.18rem;
                text-transform: uppercase;
            }

            .lesson-link:hover {
                border-color: #f1c45b;
                background: #262111;
                color: #ffe58c;
                text-decoration: none;
            }

            .story-ai-cost {
                color: #8f887e;
                font-size: 0.7rem;
                line-height: 1.3;
                margin-top: -0.45rem;
                margin-bottom: 0.72rem;
            }

            .interaction-label {
                color: var(--skim-muted);
                font-size: 0.76rem;
                text-transform: uppercase;
                margin: 0.85rem 0 0.35rem 0;
            }

            .skim-footnote {
                color: var(--skim-muted);
                font-size: 0.82rem;
                line-height: 1.4;
            }

            div[data-testid="stMetric"] {
                background: #0f0e0d;
                border: 1px solid #2c2823;
                border-radius: 8px;
                padding: 0.55rem 0.7rem;
            }

            .stButton > button,
            .stLinkButton > a {
                background: #d8d8d8;
                border-color: #c8c8c8;
                color: #111111;
                border-radius: 6px;
                min-height: 2.15rem;
                height: 2.15rem;
                line-height: 1;
                font-size: 0.78rem;
                display: inline-flex;
                align-items: center;
                justify-content: center;
                box-shadow: 0 0 13px rgba(210, 210, 210, 0.22);
                white-space: nowrap;
            }

            .stButton > button:hover,
            .stLinkButton > a:hover {
                border-color: var(--skim-accent);
                color: #000000;
            }

            [data-testid="stExpander"] {
                background: #0e0d0c;
                border: 1px solid #2c2823;
                border-radius: 8px;
            }
        </style>
        """,
        unsafe_allow_html=True,
    )


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    text = re.sub(r"<[^>]+>", " ", value)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def clean_page_text(value: str | None) -> str:
    if not value:
        return ""
    text = re.sub(r"(?is)<(script|style|noscript|svg|nav|footer|header|form)[^>]*>.*?</\1>", " ", value)
    text = re.sub(r"(?i)</(p|h1|h2|h3|li|div)>", ". ", text)
    return clean_text(text)


def parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed
    except (TypeError, ValueError, IndexError):
        try:
            iso_value = value.replace("Z", "+00:00")
            parsed = datetime.fromisoformat(iso_value)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed
        except ValueError:
            return None


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def child_text(parent: ET.Element, names: Iterable[str]) -> str:
    return clean_text(child_raw_text(parent, names))


def child_raw_text(parent: ET.Element, names: Iterable[str]) -> str:
    wanted = set(names)
    for child in parent:
        if local_name(child.tag) in wanted and child.text:
            return child.text
    return ""


def child_link(parent: ET.Element) -> str:
    for child in parent:
        if local_name(child.tag) == "link":
            href = child.attrib.get("href")
            if href:
                return href
            if child.text:
                return clean_text(child.text)
    return ""


def is_probable_image_url(url: str, type_hint: str = "") -> bool:
    lowered = url.lower()
    if type_hint.lower().startswith("image/"):
        return True
    return any(ext in lowered for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif"))


def child_image(parent: ET.Element, summary_html: str) -> str | None:
    img_match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', summary_html or "", flags=re.IGNORECASE)
    if img_match:
        url = html.unescape(img_match.group(1)).strip()
        if url.startswith("http"):
            return url

    for node in parent.iter():
        tag = local_name(node.tag)
        url = node.attrib.get("url") or node.attrib.get("href")
        type_hint = node.attrib.get("type", "")
        medium = node.attrib.get("medium", "")
        if tag in {"thumbnail", "content"} and url and (medium == "image" or is_probable_image_url(url, type_hint)):
            return url
        if tag == "enclosure" and url and is_probable_image_url(url, type_hint):
            return url
    return None


def stable_id(source_name: str, title: str, link: str) -> str:
    raw = f"{source_name}|{title}|{link}".lower()
    return re.sub(r"[^a-z0-9]+", "-", raw).strip("-")[:96]


def normalize_word(word: str) -> str:
    replacements = {
        "iranian": "iran",
        "american": "america",
        "americans": "america",
        "british": "britain",
        "chinese": "china",
        "russian": "russia",
    }
    word = replacements.get(word, word)
    for suffix in ("ing", "ed", "es", "s"):
        if len(word) > 5 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def significant_words(text: str) -> tuple[str, ...]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return tuple(
        normalize_word(word)
        for word in words
        if len(word) > 2 and word not in STOPWORDS and not word.isdigit()
    )


def story_tokens(story: Story) -> set[str]:
    return set(significant_words(f"{story.title} {story.summary_text}"))


def cluster_key_from_tokens(tokens: set[str], fallback: str) -> str:
    if not tokens:
        return stable_id("story", fallback, "")
    return "-".join(sorted(tokens)[:10])


def clean_headline_source(title: str) -> str:
    title = clean_text(title)
    title = re.sub(r"\s+-\s+[^-]{2,45}$", "", title)
    title = re.sub(r"\s+\|\s+[^|]{2,45}$", "", title)
    title = re.sub(r"\s+", " ", title).strip()
    return title.rstrip(" .")


def normalized_story_text(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", clean_text(text).lower()).strip()


def has_reported_detail_language(summary: str) -> bool:
    summary_lower = f" {clean_text(summary).lower()} "
    detail_markers = (
        " according to ",
        " announced ",
        " confirmed ",
        " reported ",
        " said ",
        " says ",
        " told ",
        " warned ",
        " found ",
        " showed ",
        " shows ",
        " officials ",
        " authorities ",
        " researchers ",
        " analysts ",
        " company ",
        " agency ",
        " ministry ",
        " department ",
        " government ",
    )
    return any(marker in summary_lower for marker in detail_markers)


def has_enough_reported_material(title: str, summary: str) -> bool:
    summary = clean_text(summary)
    if is_weak_summary(summary):
        return False

    headline = clean_headline_source(title)
    summary_norm = normalized_story_text(summary)
    headline_norm = normalized_story_text(headline)
    if not summary_norm or summary_norm == headline_norm:
        return False

    total_words = re.findall(r"[a-z0-9]+", summary_norm)
    if len(total_words) < MIN_SUMMARY_WORDS:
        return False

    headline_terms = set(significant_words(headline))
    summary_terms = set(significant_words(summary))
    new_terms = summary_terms - headline_terms
    if len(new_terms) < MIN_NEW_SUMMARY_TERMS:
        return False

    useful_sentences = [sentence for sentence in split_sentences(summary) if not is_weak_summary(sentence)]
    return len(useful_sentences) >= 2 or (len(total_words) >= 32 and has_reported_detail_language(summary))


@st.cache_data(ttl=300, show_spinner=False)
def fetch_source(source: NewsSource) -> tuple[list[Story], str | None]:
    request = urllib.request.Request(source.url, headers=REQUEST_HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=FEED_TIMEOUT_SECONDS) as response:
            xml_bytes = response.read()
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        return [], f"{source.name}: {exc}"

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        return [], f"{source.name}: could not parse feed ({exc})"

    entries = [node for node in root.iter() if local_name(node.tag) in {"item", "entry"}]
    stories: list[Story] = []
    for entry in entries[:ITEMS_PER_SOURCE]:
        title = child_text(entry, ("title",))
        link = child_link(entry)
        summary_raw = child_raw_text(entry, ("description", "summary", "content", "encoded"))
        summary = clean_text(summary_raw)
        date_text = child_text(entry, ("pubDate", "published", "updated"))
        if not title or not link:
            continue
        if not has_enough_reported_material(title, summary):
            continue
        stories.append(
            Story(
                id=stable_id(source.name, title, link),
                source=source.name,
                group=source.group,
                title=title,
                link=link,
                summary_text=summary,
                published=parse_date(date_text),
                topics=source.topics,
                image_url=child_image(entry, summary_raw),
            )
        )
    return stories, None


def keyword_news_source(keyword: str) -> NewsSource:
    query = urllib.parse.quote_plus(keyword.strip())
    return NewsSource(
        name=f"Keyword: {keyword.strip()}",
        url=f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en",
        group="Custom",
        topics=("Custom",),
    )


def fetch_stories(
    selected_topics: tuple[str, ...],
    include_aggregators: bool,
    include_social: bool,
    custom_keywords: tuple[str, ...],
) -> tuple[list[Story], list[str]]:
    stories: list[Story] = []
    errors: list[str] = []
    topic_set = set(selected_topics)

    for source in NEWS_SOURCES:
        if source.group == "Aggregator" and not include_aggregators:
            continue
        if source.group == "Social" and not include_social:
            continue
        if topic_set and not topic_set.intersection(source.topics):
            continue
        source_stories, error = fetch_source(source)
        stories.extend(source_stories)
        if error:
            errors.append(error)

    for keyword in custom_keywords:
        source_stories, error = fetch_source(keyword_news_source(keyword))
        stories.extend(source_stories)
        if error:
            errors.append(error)

    return stories, errors


def fetch_keyword_rankings(custom_keywords: tuple[str, ...]) -> tuple[dict[str, list[RankedStory]], list[str]]:
    keyword_rankings: dict[str, list[RankedStory]] = {}
    errors: list[str] = []

    for keyword in custom_keywords:
        source_stories, error = fetch_source(keyword_news_source(keyword))
        keyword_rankings[keyword] = rank_stories(source_stories, (keyword,))
        if error:
            errors.append(error)

    return keyword_rankings, errors


def cluster_stories(stories: list[Story]) -> list[list[Story]]:
    clusters: list[list[Story]] = []
    cluster_tokens: list[set[str]] = []

    for story in stories:
        tokens = story_tokens(story)
        matched_index = None
        for index, existing_tokens in enumerate(cluster_tokens):
            shared = tokens.intersection(existing_tokens)
            union = tokens.union(existing_tokens)
            overlap = len(shared) / max(1, len(union))
            if overlap >= 0.3 or len(shared) >= 4:
                matched_index = index
                break

        if matched_index is None:
            clusters.append([story])
            cluster_tokens.append(tokens)
        else:
            clusters[matched_index].append(story)
            cluster_tokens[matched_index].update(tokens)

    return clusters


def custom_keywords() -> tuple[str, ...]:
    keywords = []
    for index in range(9):
        value = str(st.session_state.get(f"custom_keyword_{index}", "")).strip()
        if value:
            keywords.append(value)
    return tuple(dict.fromkeys(keywords))


def query_param_text(name: str) -> str:
    value = st.query_params.get(name, "")
    if isinstance(value, list):
        return str(value[-1]) if value else ""
    return str(value)


def initialize_keyword_state() -> None:
    first_load = not st.session_state.get("keyword_state_initialized", False)
    for index in range(9):
        key = f"custom_keyword_{index}"
        query_key = f"kw{index + 1}"
        if first_load:
            st.session_state.setdefault(key, query_param_text(query_key))
        else:
            st.session_state.setdefault(key, "")
    st.session_state.keyword_state_initialized = True


def persist_keywords_to_query_params() -> None:
    for index in range(9):
        query_key = f"kw{index + 1}"
        value = str(st.session_state.get(f"custom_keyword_{index}", "")).strip()
        if value:
            st.query_params[query_key] = value
        elif query_key in st.query_params:
            del st.query_params[query_key]


def complete_story_refresh() -> None:
    fetch_source.clear()
    fetch_research_snippet.clear()
    st.session_state.current_cluster_keys = []
    st.session_state.last_settings = None
    st.session_state.deep_analyses = {}


def keyword_match_count(story: Story, keywords: tuple[str, ...]) -> int:
    if not keywords:
        return 0
    haystack = f"{story.title} {story.summary_text}".lower()
    token_set = story_tokens(story)
    matches = 0
    for keyword in keywords:
        normalized_keyword = keyword.lower().strip()
        keyword_tokens = set(significant_words(normalized_keyword))
        if normalized_keyword in haystack or (keyword_tokens and keyword_tokens.issubset(token_set)):
            matches += 1
    return matches


def story_score(story: Story, references: int, cluster_size: int, keywords: tuple[str, ...] = ()) -> float:
    now = datetime.now(timezone.utc)
    published = story.published or now
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    age_hours = max(0.0, (now - published.astimezone(timezone.utc)).total_seconds() / 3600)
    recency_score = max(0.0, 48.0 - age_hours)
    group_weight = {"Custom": 24.0, "Aggregator": 16.0, "Social": 12.0, "Major News": 10.0}.get(story.group, 8.0)
    keyword_boost = keyword_match_count(story, keywords) * 34.0
    return (references * 18.0) + (cluster_size * 8.0) + recency_score + group_weight + keyword_boost


def rank_stories(stories: list[Story], keywords: tuple[str, ...] = ()) -> list[RankedStory]:
    ranked: list[RankedStory] = []
    for cluster in cluster_stories(stories):
        sources = {story.source for story in cluster}
        groups = {story.group for story in cluster}
        references = (
            len(sources)
            + (3 if "Custom" in groups else 0)
            + (2 if "Aggregator" in groups else 0)
            + (1 if "Social" in groups else 0)
        )
        representative = max(
            cluster,
            key=lambda story: story_score(story, references=references, cluster_size=len(cluster), keywords=keywords),
        )
        tokens = story_tokens(representative)
        ranked.append(
            RankedStory(
                story=representative,
                cluster_key=cluster_key_from_tokens(tokens, representative.title),
                references=references,
                topic_story_count=len(cluster),
                score=story_score(representative, references=references, cluster_size=len(cluster), keywords=keywords),
            )
        )

    ranked.sort(key=lambda item: item.score, reverse=True)
    return ranked


def headline(title: str, max_words: int) -> str:
    return clean_headline_source(title)


def sensible_display_headline(candidate: str, story: Story) -> str:
    candidate = clean_headline_source(candidate)
    candidate = candidate.replace("...", "").strip(" -:;,.")
    if not candidate or len(candidate.split()) < 4:
        candidate = clean_headline_source(story.title)

    words = candidate.split()
    if len(words) <= 14:
        return " ".join(words)

    phrase_breaks = {"after", "as", "amid", "while", "over", "following", "despite", "because"}
    for index in range(min(14, len(words) - 1), 7, -1):
        if words[index].lower().strip(",:;") in phrase_breaks:
            return " ".join(words[:index]).rstrip(",:;")

    punctuation_text = " ".join(words[:16])
    punctuation_match = re.match(r"^(.{30,95}?)[,:;]\s", punctuation_text)
    if punctuation_match and len(punctuation_match.group(1).split()) >= 6:
        return punctuation_match.group(1).rstrip(",:;")
    return " ".join(words[:12]).rstrip(",:;")


def excerpt(text: str, width: int) -> str:
    shortened = textwrap.shorten(clean_text(text), width=width, placeholder="")
    shortened = shortened.rstrip(" ,;:")
    if shortened and shortened[-1] not in ".!?":
        shortened += "."
    return shortened


def split_sentences(text: str) -> list[str]:
    text = clean_text(text)
    if not text:
        return []
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]


def sentence_count(text: str) -> int:
    return len(split_sentences(text))


def is_weak_summary(text: str) -> bool:
    normalized = clean_text(text).lower().strip(" .:-")
    if not normalized:
        return True
    weak_values = {
        "comments",
        "comment",
        "read more",
        "continue reading",
        "view comments",
        "submitted by",
    }
    if normalized in weak_values:
        return True
    if len(normalized.split()) <= 3 and any(word in normalized for word in ("comment", "url", "link")):
        return True
    return normalized.startswith(("comments url", "article url", "submitted by"))


def happened_summary(story: Story, detail: int) -> str:
    title_summary = clean_headline_source(story.title)
    if story.group == "Aggregator":
        return (
            f"{excerpt(title_summary, width=220)} "
            f"The key fact is that multiple outlets or feeds are giving this subject attention right now. "
            f"Read the full story to separate the confirmed details from the fast-moving headline framing."
        )

    useful_sentences = [sentence for sentence in split_sentences(story.summary_text) if not is_weak_summary(sentence)]
    if not useful_sentences:
        return (
            f"{excerpt(title_summary, width=220)} "
            f"The feed did not provide a strong summary, so the headline is the clearest confirmed signal. "
            f"Use the full story link for names, dates, quotes, and details before treating the item as settled."
        )

    happened_sentences = useful_sentences[:3]
    while len(happened_sentences) < 3:
        if len(happened_sentences) == 1:
            happened_sentences.append(
                "The immediate importance is that the event has broken through enough to be surfaced by a major feed."
            )
        else:
            happened_sentences.append(
                "The full story will matter for the specific people, institutions, and decisions behind the headline."
            )
    happened = " ".join(happened_sentences)
    if is_weak_summary(happened):
        happened = title_summary
    return excerpt(happened, width=620)


def infer_topics(story: Story) -> tuple[str, ...]:
    headline_text = f" {story.title} ".lower()
    haystack = f"{headline_text} {story.summary_text} ".lower()
    matches = list(story.topics)
    for topic, needles in TOPICS.items():
        if topic in matches:
            continue
        headline_match = any(needle in headline_text for needle in needles)
        body_match_count = sum(1 for needle in needles if needle in haystack)
        if headline_match or body_match_count >= 2:
            matches.append(topic)
    return tuple(matches[:4]) or story.topics[:2]


def why_theme(story: Story, topics: tuple[str, ...]) -> str:
    headline_text = f" {story.title} ".lower()
    if "Business" in topics and (
        "Business" in story.topics
        or any(word in headline_text for word in ("market", "earnings", "company", "trade", "economy"))
    ):
        return "business"
    if ("AI" in topics or "Tech" in topics) and (
        "AI" in story.topics
        or "Tech" in story.topics
        or any(word in headline_text for word in ("ai", "technology", "software", "chip", "cyber"))
    ):
        return "technology"
    if "Health" in topics and (
        "Health" in story.topics
        or any(word in headline_text for word in ("health", "hospital", "medicine", "drug", "disease", "vaccine"))
    ):
        return "health"
    if "World" in topics or "Politics" in topics or "US" in topics:
        return "politics"
    return "general"


def wikipedia_links(story: Story, topics: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
    haystack = story_haystack(story)
    if "wildberries" in haystack:
        return (
            ("Wildberries", "https://en.wikipedia.org/wiki/Wildberries"),
            ("Russian invasion of Ukraine", "https://en.wikipedia.org/wiki/Russian_invasion_of_Ukraine"),
            ("Drone warfare", "https://en.wikipedia.org/wiki/Drone_warfare"),
        )
    candidates = (
        ("Wildberries", "Wildberries", ("wildberries",), 130),
        ("Russian invasion of Ukraine", "Russian_invasion_of_Ukraine", ("ukraine", "russia's attacks", "russian", "drone"), 90),
        ("Drone warfare", "Drone_warfare", ("drone", "drones", "unmanned"), 85),
        ("Economy of Russia", "Economy_of_Russia", ("russia", "russian business", "businesses under strain"), 70),
        ("Strait of Hormuz", "Strait_of_Hormuz", ("hormuz",), 120),
        ("Iran", "Iran", ("iran", "tehran"), 80),
        ("Saudi Arabia", "Saudi_Arabia", ("saudi",), 80),
        ("Tariff", "Tariff", ("tariff", "trade crosshairs"), 100),
        ("Protectionism", "Protectionism", ("tariff", "trade", "imports", "exports"), 65),
        ("Supply chain", "Supply_chain", ("supply chain", "shipping", "ports", "warehouse", "warehouses"), 75),
        ("Nuclear power", "Nuclear_power", ("nuclear",), 85),
        ("International relations", "International_relations", ("diplomacy", "alliance", "treaty"), 70),
        ("Artificial intelligence", "Artificial_intelligence", (" ai ", "artificial intelligence", "openai", "model"), 95),
        ("Cloud computing", "Cloud_computing", ("cloud",), 70),
        ("Social media", "Social_media", ("social media", "meta", "reddit", "x "), 90),
        ("Algorithm", "Algorithm", ("algorithm",), 65),
        ("Climate change", "Climate_change", ("climate", "temperature", "warming", "heat"), 95),
        ("Public health", "Public_health", ("health", "hospital", "vaccine", "disease"), 85),
        ("Financial market", "Financial_market", ("market", "earnings", "stocks"), 75),
        ("Human rights", "Human_rights", ("protest", "rights", "censorship"), 85),
        ("Cybersecurity", "Computer_security", ("cyber", "hack", "data breach"), 90),
        ("Cultural heritage", "Cultural_heritage", ("louvre", "museum", "jewel", "artifact", "heritage"), 80),
    )
    scored: list[tuple[int, str, str]] = []
    for label, slug, needles, weight in candidates:
        matches = sum(1 for needle in needles if needle in haystack)
        if matches:
            scored.append((weight + (matches * 12), label, f"https://en.wikipedia.org/wiki/{slug}"))

    fallback_links: list[tuple[str, str]] = []
    if "Business" in topics:
        fallback_links.extend((
            ("Economics", "https://en.wikipedia.org/wiki/Economics"),
            ("Supply chain", "https://en.wikipedia.org/wiki/Supply_chain"),
        ))
    if "Tech" in topics or "AI" in topics:
        fallback_links.extend((
            ("Technology", "https://en.wikipedia.org/wiki/Technology"),
            ("Artificial intelligence", "https://en.wikipedia.org/wiki/Artificial_intelligence"),
        ))
    if "Health" in topics:
        fallback_links.append(("Public health", "https://en.wikipedia.org/wiki/Public_health"))
    if "Science" in topics:
        fallback_links.append(("Science", "https://en.wikipedia.org/wiki/Science"))
    if "Politics" in topics or "World" in topics or "US" in topics:
        fallback_links.extend((
            ("International relations", "https://en.wikipedia.org/wiki/International_relations"),
            ("Geopolitics", "https://en.wikipedia.org/wiki/Geopolitics"),
        ))
    fallback_links.append(("Current events", "https://en.wikipedia.org/wiki/Portal:Current_events"))

    ranked_links = [(label, url) for _, label, url in sorted(scored, reverse=True)]
    ranked_links.extend(fallback_links)

    unique_links: list[tuple[str, str]] = []
    seen_urls: set[str] = set()
    for label, url in ranked_links:
        if url in seen_urls:
            continue
        unique_links.append((label, url))
        seen_urls.add(url)
        if len(unique_links) == 3:
            break
    return tuple(unique_links)


def is_wikipedia_url(url: str) -> bool:
    return urllib.parse.urlparse(url).netloc.lower().endswith("wikipedia.org")


def google_news_search_link(story: Story) -> tuple[str, str]:
    query = urllib.parse.quote_plus(clean_headline_source(story.title))
    return ("Related coverage", f"https://news.google.com/search?q={query}&hl=en-US&gl=US&ceid=US:en")


def source_site_link(story: Story) -> tuple[str, str] | None:
    parsed = urllib.parse.urlparse(story.link)
    if not parsed.scheme or not parsed.netloc or "wikipedia.org" in parsed.netloc:
        return None
    domain = parsed.netloc.removeprefix("www.")
    if domain in {"news.google.com", "google.com"}:
        return None
    return (story.source, f"{parsed.scheme}://{parsed.netloc}")


def reference_links(story: Story, topics: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
    haystack = story_haystack(story)
    candidates = (
        ("AP Russia-Ukraine hub", "https://apnews.com/hub/russia-ukraine", ("ukraine", "russia", "russian", "drone"), 120),
        ("ISW Ukraine updates", "https://www.understandingwar.org/backgrounder/ukraine-conflict-updates", ("ukraine", "russia", "drone", "war"), 115),
        ("CFR backgrounders", "https://www.cfr.org/backgrounders", ("war", "diplomacy", "alliance", "geopolitics", "election"), 80),
        ("Reuters world coverage", "https://www.reuters.com/world/", ("war", "strike", "diplomacy", "government", "election"), 70),
        ("WTO trade topics", "https://www.wto.org/english/tratop_e/tratop_e.htm", ("tariff", "trade", "imports", "exports"), 115),
        ("World Bank data", "https://data.worldbank.org/", ("economy", "market", "inflation", "trade", "business"), 80),
        ("NIST AI resources", "https://www.nist.gov/artificial-intelligence", (" ai ", "artificial intelligence", "model", "algorithm"), 115),
        ("Stanford AI Index", "https://aiindex.stanford.edu/", (" ai ", "artificial intelligence", "openai", "model"), 110),
        ("CISA cyber guidance", "https://www.cisa.gov/topics/cybersecurity-best-practices", ("cyber", "hack", "breach", "ransomware"), 120),
        ("NASA climate", "https://climate.nasa.gov/", ("climate", "warming", "temperature", "heat"), 115),
        ("IPCC reports", "https://www.ipcc.ch/reports/", ("climate", "emissions", "warming"), 105),
        ("WHO news", "https://www.who.int/news", ("health", "disease", "vaccine", "outbreak"), 110),
        ("CDC health topics", "https://www.cdc.gov/health-topics.html", ("health", "disease", "vaccine", "outbreak"), 95),
        ("UNESCO heritage", "https://www.unesco.org/en/culture", ("museum", "heritage", "artifact", "louvre", "culture"), 110),
        ("Pew Research", "https://www.pewresearch.org/", ("social media", "platform", "election", "public opinion"), 80),
    )
    scored: list[tuple[int, str, str]] = []
    for label, url, needles, weight in candidates:
        matches = sum(1 for needle in needles if needle in haystack)
        if matches:
            scored.append((weight + (matches * 10), label, url))

    ranked_links = [(label, url) for _, label, url in sorted(scored, reverse=True)]
    ranked_links.append(google_news_search_link(story))
    source_link = source_site_link(story)
    if source_link:
        ranked_links.append(source_link)
    if "Business" in topics:
        ranked_links.append(("Financial Times markets", "https://www.ft.com/markets"))
    if "Tech" in topics or "AI" in topics:
        ranked_links.append(("MIT Technology Review", "https://www.technologyreview.com/"))
    if "Politics" in topics or "World" in topics or "US" in topics:
        ranked_links.append(("Council on Foreign Relations", "https://www.cfr.org/"))

    unique_links: list[tuple[str, str]] = []
    seen_urls: set[str] = set()
    for label, url in ranked_links:
        if url in seen_urls or is_wikipedia_url(url):
            continue
        unique_links.append((label, url))
        seen_urls.add(url)
        if len(unique_links) == 2:
            break
    return tuple(unique_links)


def story_learning_links(story: Story, topics: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
    links = list(reference_links(story, topics))
    for fallback in (google_news_search_link(story), source_site_link(story)):
        if fallback and len(links) < 2 and fallback[1] not in {url for _, url in links}:
            links.append(fallback)
    while len(links) < 2:
        links.append(("Related coverage", "https://news.google.com/topstories?hl=en-US&gl=US&ceid=US:en"))

    wiki_link = wikipedia_links(story, topics)[0]
    return tuple([*links[:2], wiki_link])


def extract_research_snippet(page_html: str, max_chars: int = 900) -> str:
    description_match = re.search(
        r'<meta[^>]+(?:name|property)=["\'](?:description|og:description)["\'][^>]+content=["\']([^"\']+)["\']',
        page_html,
        flags=re.IGNORECASE,
    )
    snippets: list[str] = []
    if description_match:
        snippets.append(clean_text(description_match.group(1)))

    paragraph_matches = re.findall(r"(?is)<p[^>]*>(.*?)</p>", page_html)
    for paragraph_html in paragraph_matches[:8]:
        paragraph = clean_page_text(paragraph_html)
        if len(paragraph.split()) >= 12:
            snippets.append(paragraph)
        if len(" ".join(snippets)) >= max_chars:
            break

    if not snippets:
        snippets.append(clean_page_text(page_html[:20000]))
    return excerpt(" ".join(snippets), width=max_chars)


@st.cache_data(ttl=1800, show_spinner=False)
def fetch_research_snippet(url: str) -> str:
    if not url.startswith(("https://", "http://")):
        return ""
    request = urllib.request.Request(url, headers=REQUEST_HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=RESEARCH_TIMEOUT_SECONDS) as response:
            content_type = response.headers.get("Content-Type", "")
            if "text/html" not in content_type and "application/xhtml" not in content_type:
                return ""
            page_bytes = response.read(250_000)
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return ""

    page_html = page_bytes.decode("utf-8", errors="ignore")
    return extract_research_snippet(page_html)


def research_notes(story: Story, topics: tuple[str, ...]) -> str:
    candidate_links = [("Article page", story.link), *story_learning_links(story, topics)[:2]]
    notes: list[str] = []
    seen_urls: set[str] = set()
    for label, url in candidate_links:
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        snippet = fetch_research_snippet(url)
        if snippet:
            notes.append(f"{label}: {snippet}")
        if len(notes) == 3:
            break
    return "\n".join(notes)


def lesson_text(story: Story, topics: tuple[str, ...]) -> str:
    haystack = story_haystack(story)
    if "wildberries" in haystack:
        return "Know that the war is reaching Russia's private logistics and consumer economy, not just military targets; research how e-commerce, warehouses, and drone warfare have become part of modern conflict."
    theme = why_theme(story, topics)
    if theme == "health":
        return "Know how this changes risk, access, and trust. Research which institutions are responsible and what evidence they are using."
    if theme == "business":
        return "Watch the second-order effects: prices, jobs, supply chains, and bargaining power often matter more than the first headline."
    if theme == "technology":
        return "Look for who gains leverage from the technology shift: users, companies, governments, workers, or the systems that connect them."
    if theme == "politics":
        return "Track the precedent, not just the event. The durable lesson is often how power responds under pressure."
    return "Separate the immediate event from the pattern it reveals. Research the system behind the story before deciding what it means."


def story_haystack(story: Story) -> str:
    return f" {story.title} {story.summary_text} ".lower()


def has_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)


def context_subject(story: Story) -> str:
    title = clean_headline_source(story.title)
    title = re.sub(r"^(live|updates?|breaking|watch)\s*[:|-]\s*", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s+", " ", title).strip(" .")
    words = title.split()
    if len(words) > 16:
        title = " ".join(words[:16]).rstrip(",:;")
    return title or "this story"


def context_text(story: Story, topics: tuple[str, ...]) -> str:
    haystack = story_haystack(story)
    subject = context_subject(story)

    if "wildberries" in haystack:
        return (
            "This is a sign that the Russia-Ukraine war is pushing deeper into the infrastructure of ordinary economic life. "
            "Wildberries is not just a retailer; it represents logistics, warehousing, consumer access, and the private-sector systems Russians rely on every day. "
            "If attacks like this continue, they could pressure insurers, landlords, delivery networks, regional officials, and business owners, while signaling that Ukraine wants the costs of war felt inside Russia's domestic economy."
        )
    if has_any(haystack, ("ebola", "mpox", "measles", "cholera", "outbreak", "epidemic")):
        return (
            f"{subject} is mainly a test of outbreak control: surveillance, contact tracing, isolation, vaccination, safe burials, and public trust have to move faster than the disease. "
            "In places with fragile health systems, conflict, displacement, or weak transportation networks, even a localized outbreak can become a regional stress signal because patients, families, and health workers cross administrative borders. "
            "The next thing to watch is whether authorities contain transmission chains quickly or whether rising deaths start changing travel behavior, aid flows, school activity, and confidence in public-health institutions."
        )
    if has_any(haystack, ("hormuz", "iran", "missile", "strike", "war", "defense bill", "military", "nuclear deal")):
        return (
            f"{subject} sits inside the machinery of escalation: military signaling, domestic politics, alliances, and economic exposure all press on each other at once. "
            "The immediate event matters less than how rivals interpret it; if either side treats the moment as a credibility test, it can trigger retaliatory moves, emergency diplomacy, shipping or energy anxiety, and new pressure on allied governments. "
            "The larger question is whether institutions and back channels can absorb the shock before symbolic retaliation becomes a self-sustaining cycle."
        )
    if has_any(haystack, ("tariff", "trade", "dairy", "supply chain", "imports", "exports", "sanctions")):
        return (
            f"{subject} points to trade policy becoming a bargaining weapon, not just an economic rulebook. "
            "A fight that starts with one product or sector can spread into retaliation, domestic lobbying, price pressure, and negotiations over unrelated issues such as security, migration, industrial policy, or access to strategic materials. "
            "The broader pattern is a world moving away from frictionless globalization toward managed trade, national leverage, and a more political supply chain."
        )
    if has_any(haystack, ("meta", "social media", "addiction", "algorithm", "platform", "tiktok", "reddit", "online")):
        return (
            f"{subject} is part of the fight over whether platforms are neutral tools or environments that actively shape behavior, politics, and mental health. "
            "Even a narrow lawsuit, policy change, or viral controversy can trigger copycat claims, regulatory hearings, advertiser pressure, school or family rule changes, and demands that companies reveal how their ranking systems work. "
            "The bigger issue is responsibility: when software becomes social infrastructure, design choices start looking less like product tweaks and more like governance decisions."
        )
    if has_any(haystack, ("earnings", "profit", "alphabet", "google", "ai spending", "cloud", "startup", "semiconductor", "chip", "chips")):
        return (
            f"{subject} is a signal about who can afford the next technology cycle and who is being priced into dependency. "
            "Investors, competitors, workers, and regulators will read the news as evidence for whether spending on AI, cloud, chips, or software infrastructure is producing durable advantage or simply feeding a costly arms race. "
            "The follow-on effects could include more capital spending, consolidation, layoffs, antitrust scrutiny, or a market repricing of which companies actually control the stack."
        )
    if has_any(haystack, ("climate", "temperature", "heat", "warming", "flood", "drought", "wildfire", "emissions")):
        return (
            f"{subject} is climate showing up as a systems problem rather than a single environmental headline. "
            "Heat, water stress, migration, food production, insurance, public health, and local budgets can all move together when the physical baseline changes. "
            "The story may trigger adaptation spending or political fights over responsibility, but the deeper issue is whether communities can adjust before disruption becomes part of normal planning."
        )
    if has_any(haystack, ("supreme court", "court ruling", "election law", "constitutional", "judicial")):
        return (
            f"{subject} is about how legal decisions become operating rules for politics, institutions, and ordinary civic life. "
            "Court rulings can outlast the immediate dispute because they change what future actors are allowed to do, how states or agencies design policy, and what strategies interest groups pursue next. "
            "The larger question is whether the decision settles a conflict or simply moves the fight into legislatures, campaigns, administrative agencies, and future litigation."
        )
    if has_any(haystack, ("protest", "censor", "censorship", "rights", "police", "blackmail", "rape", "jailed", "trial")):
        return (
            f"{subject} is really about institutional legitimacy: whether courts, police, governments, or public platforms are trusted to handle power fairly. "
            "Cases like this can trigger protests, legal reforms, backlash, copycat scrutiny, or a deeper loss of confidence if people see the system protecting itself instead of producing accountability. "
            "The larger pattern is a contest over who gets believed, who gets punished, and whether public institutions can still create shared facts."
        )
    if has_any(haystack, ("health", "hospital", "disease", "vaccine", "drug", "medicine", "public health", "outbreak")):
        return (
            f"{subject} is a stress test for the health system around evidence, capacity, cost, and public trust. "
            "A single development can change patient behavior, funding priorities, regulation, insurance decisions, and how much confidence people place in experts or institutions. "
            "The bigger issue is whether the system can respond early and transparently or whether it only moves once personal risk becomes impossible to ignore."
        )
    if has_any(haystack, ("hack", "cyber", "data breach", "ransomware", "security flaw", "leak")):
        return (
            f"{subject} shows how digital security failures have become real-world governance problems. "
            "A breach, flaw, or attack can trigger lawsuits, regulation, copycat operations, insurance changes, and lasting damage to trust between users and institutions. "
            "The larger issue is that modern life depends on systems most people cannot inspect but everyone is forced to rely on."
        )
    if has_any(haystack, ("louvre", "museum", "jewel", "artifact", "heritage", "art theft")):
        return (
            f"{subject} is not only a crime or culture story; it is about how societies protect shared memory and public trust. "
            "A high-profile loss or breach can trigger security overhauls, political blame, insurance changes, and renewed questions about who gets to own or safeguard cultural treasures. "
            "The larger issue is that symbolic places carry national identity, so failures there feel bigger than the immediate damage."
        )
    if "Business" in topics:
        return (
            f"{subject} is a business story with consequences beyond one company or sector. "
            "Changes in pricing, demand, labor, capital spending, or regulation can ripple into households, suppliers, workers, and competitors faster than the headline suggests. "
            "The larger issue is whether this is a temporary adjustment or a deeper shift in where value and leverage are moving through the economy."
        )
    if "Tech" in topics or "AI" in topics:
        return (
            f"{subject} is a technology story about control: who builds the tools, who depends on them, and who absorbs the risk when they change. "
            "It may trigger regulation, new investment, user backlash, or competitive moves from companies trying not to fall behind. "
            "The bigger pattern is that technical decisions are increasingly becoming labor, privacy, education, and governance decisions."
        )
    if "Politics" in topics or "World" in topics or "US" in topics:
        return (
            f"{subject} sits inside a wider struggle over power, legitimacy, and public trust. "
            "The event itself may be brief, but the response can set precedents that shape alliances, elections, institutional behavior, or citizen expectations. "
            "The key question is whether it resolves pressure or reveals that the pressure has been building for a long time."
        )
    if story.group == "Social":
        return (
            f"{subject} is still an early signal, which is exactly why it needs careful reading. "
            "Social attention can reveal something before formal institutions catch up, but it can also distort scale, context, and certainty. "
            "Watch whether the story crosses into verified reporting, official response, market behavior, or policy debate; that transition is what turns online heat into real-world consequence."
        )
    if story.group == "Aggregator":
        return (
            f"{subject} matters because multiple outlets are converging on the same subject at the same time. "
            "That pickup can turn a story into a feedback loop: officials respond to coverage, institutions react to the response, and the framing can harden before all the facts settle. "
            "The question to watch is whether the coverage leads to measurable action, changed behavior, or official confirmation, or whether it fades as a burst of attention around a volatile moment."
        )
    return (
        f"{subject} matters most as a signal of incentives, risks, or tensions that may show up again in stronger form. "
        "It may not be world-changing by itself, but the surrounding reaction can reveal who has leverage, who is exposed, and which institutions are expected to respond. "
        "The useful move is to ask what system produced the story, who benefits if the pattern continues, and what would happen if it spreads."
    )


def summarize(story: Story, detail: int) -> dict[str, str]:
    topics = infer_topics(story)
    links = learning_links_text(story_learning_links(story, topics))

    return {
        "__headline": headline(story.title, 0),
        "": happened_summary(story, detail),
        "Background": context_text(story, topics),
        "Learn More": f"Learn more: {links}",
    }


def secret_or_env(name: str) -> str:
    try:
        secret_value = st.secrets.get(name, "")
    except Exception:
        secret_value = ""
    return str(secret_value or os.environ.get(name, "")).strip()


def openai_api_key() -> str:
    return secret_or_env("OPENAI_API_KEY")


def openai_is_configured() -> bool:
    return bool(openai_api_key())


def configured_ai_provider() -> str:
    available = {
        "openai": bool(secret_or_env("OPENAI_API_KEY")),
        "gemini": bool(secret_or_env("GEMINI_API_KEY")),
        "groq": bool(secret_or_env("GROQ_API_KEY")),
        "xai": bool(secret_or_env("XAI_API_KEY")),
    }
    requested = secret_or_env("SKIM_AI_PROVIDER").lower()
    if requested in available and available[requested]:
        return requested
    for provider in ("openai", "gemini", "groq", "xai"):
        if available[provider]:
            return provider
    return ""


def ai_provider_label() -> str:
    labels = {
        "gemini": "Gemini free tier",
        "groq": "Groq free tier",
        "xai": "xAI Grok",
        "openai": "OpenAI GPT-5.6",
    }
    return labels.get(configured_ai_provider(), "Free feeds / local summaries")


def ai_model(provider: str, deep: bool) -> str:
    default_models = {
        ("gemini", False): GEMINI_SUMMARY_MODEL,
        ("gemini", True): GEMINI_DEEP_MODEL,
        ("groq", False): GROQ_SUMMARY_MODEL,
        ("groq", True): GROQ_DEEP_MODEL,
        ("xai", False): XAI_SUMMARY_MODEL,
        ("xai", True): XAI_DEEP_MODEL,
        ("openai", False): OPENAI_SUMMARY_MODEL,
        ("openai", True): OPENAI_DEEP_MODEL,
    }
    env_name = f"SKIM_{provider.upper()}_{'DEEP' if deep else 'SUMMARY'}_MODEL"
    return secret_or_env(env_name) or default_models[(provider, deep)]


def estimated_token_count(*parts: str, overhead_tokens: int = 0) -> int:
    text = " ".join(clean_text(part) for part in parts if part)
    return max(1, int(len(text) / 4) + overhead_tokens)


def openai_cost(model: str, input_tokens: int, output_tokens: int) -> float | None:
    prices = OPENAI_MODEL_PRICES_PER_MTOK.get(model)
    if not prices:
        return None
    input_price, output_price = prices
    return ((input_tokens / 1_000_000) * input_price) + ((output_tokens / 1_000_000) * output_price)


def format_cost(value: float) -> str:
    if value < 0.01:
        return f"${value:.4f}"
    return f"${value:.2f}"


def openai_cost_note(story: Story, research_text: str) -> str:
    if configured_ai_provider() != "openai":
        return ""

    summary_model = ai_model("openai", deep=False)
    summary_input_tokens = estimated_token_count(
        story.title,
        story.summary_text,
        research_text,
        overhead_tokens=850,
    )
    summary_output_tokens = 950
    summary_cost = openai_cost(summary_model, summary_input_tokens, summary_output_tokens)
    if summary_cost is None:
        return ""

    deep_model = ai_model("openai", deep=True)
    deep_input_tokens = estimated_token_count(
        story.title,
        story.summary_text,
        research_text,
        overhead_tokens=520,
    )
    deep_output_tokens = 700
    deep_cost = openai_cost(deep_model, deep_input_tokens, deep_output_tokens)
    deep_note = f" · deep if clicked ~{format_cost(deep_cost)}" if deep_cost is not None else ""
    return f"AI estimate: article ~{format_cost(summary_cost)}{deep_note}"


def parse_openai_json(raw_text: str) -> dict:
    if not raw_text:
        return {}
    try:
        return json.loads(raw_text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw_text, flags=re.DOTALL)
        if not match:
            return {}
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}


def post_json(url: str, headers: dict[str, str], payload: dict) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={**headers, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        return json.loads(response.read().decode("utf-8"))


def gemini_json(model: str, instructions: str, prompt: str, max_output_tokens: int) -> dict:
    model_path = urllib.parse.quote(model, safe="")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_path}:generateContent"
    payload = {
        "systemInstruction": {"parts": [{"text": instructions}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.25,
            "maxOutputTokens": max_output_tokens,
        },
    }
    response = post_json(url, {"x-goog-api-key": secret_or_env("GEMINI_API_KEY")}, payload)
    parts = response.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    raw_text = " ".join(str(part.get("text", "")) for part in parts if isinstance(part, dict))
    return parse_openai_json(raw_text)


def chat_completions_json(
    url: str,
    api_key: str,
    model: str,
    instructions: str,
    prompt: str,
    max_output_tokens: int,
) -> dict:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": instructions},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.25,
        "max_completion_tokens": max_output_tokens,
        "response_format": {"type": "json_object"},
    }
    response = post_json(url, {"Authorization": f"Bearer {api_key}"}, payload)
    raw_text = response.get("choices", [{}])[0].get("message", {}).get("content", "")
    return parse_openai_json(raw_text)


def openai_json(model: str, instructions: str, prompt: str, effort: str, max_output_tokens: int) -> dict:
    from openai import OpenAI

    client = OpenAI(api_key=openai_api_key())
    response = client.responses.create(
        model=model,
        instructions=instructions,
        input=prompt,
        reasoning={"effort": effort},
        text={"format": {"type": "json_object"}},
        max_output_tokens=max_output_tokens,
    )
    return parse_openai_json(getattr(response, "output_text", ""))


def ai_json(provider: str, model: str, instructions: str, prompt: str, effort: str, max_output_tokens: int) -> dict:
    if provider == "gemini":
        return gemini_json(model, instructions, prompt, max_output_tokens)
    if provider == "groq":
        return chat_completions_json(
            "https://api.groq.com/openai/v1/chat/completions",
            secret_or_env("GROQ_API_KEY"),
            model,
            instructions,
            prompt,
            max_output_tokens,
        )
    if provider == "xai":
        return chat_completions_json(
            "https://api.x.ai/v1/chat/completions",
            secret_or_env("XAI_API_KEY"),
            model,
            instructions,
            prompt,
            max_output_tokens,
        )
    return openai_json(model, instructions, prompt, effort, max_output_tokens)


@st.cache_data(ttl=86400, show_spinner=False)
def ai_summary_cached(
    provider: str,
    model: str,
    prompt_version: str,
    refresh_key: str,
    story_id: str,
    title: str,
    source: str,
    group: str,
    summary_text: str,
    link: str,
    topics: tuple[str, ...],
    detail: int,
    research_text: str,
) -> dict:
    prompt = textwrap.dedent(
        f"""
        Source: {source}
        Source type: {group}
        Topics: {", ".join(topics)}
        Headline: {clean_headline_source(title)}
        RSS summary: {clean_text(summary_text) or "No useful RSS summary was provided."}
        Full story URL: {link}
        Desired detail level: {detail}/5
        Stable story id: {story_id}
        Story refresh key: {refresh_key}
        Prompt version: {prompt_version}
        Research notes gathered at refresh time:
        {research_text or "No additional research notes were available; use the RSS material carefully."}
        """
    ).strip()
    instructions = """
    You are Skim, a sharp personal news analyst. Use the provided headline, source,
    RSS summary, URL, topic labels, and refresh-time research notes; do not invent facts.
    Return valid JSON with:
    headline, summary, background, and links. headline is a complete, natural headline
    of 6-12 words; it must not end abruptly and must not use ellipses. summary is 4-5
    well-written sentences explaining what happened in plain English, never the word
    "comments", and never just a headline. Write with calm authority and useful detail,
    not filler. background is one smart, specific paragraph that teaches the backstory
    and perspective behind this exact story: why it is important, what history or prior
    tension makes it news, who has leverage, what could happen next, and what larger
    stress or change it may reveal. Use the research notes to add specificity when they
    are available. Do not use generic reusable background. Name or clearly refer to the
    story's central subject, place, institution, company, disease, technology, market,
    or conflict.
    links is exactly three objects with label and url fields. The first two links must
    be useful non-Wikipedia references tied to the story, such as source pages,
    official institutions, data/background pages, reputable topic hubs, or related
    coverage. The third and final link must be one relevant Wikipedia page for the
    central entity, conflict, institution, technology, geography, or historical pattern.
    """
    return ai_json(provider, model, instructions, prompt, effort="medium", max_output_tokens=1800)


@st.cache_data(ttl=86400, show_spinner=False)
def ai_deep_analysis_cached(
    provider: str,
    model: str,
    story_id: str,
    title: str,
    source: str,
    group: str,
    summary_text: str,
    link: str,
    topics: tuple[str, ...],
) -> dict:
    prompt = textwrap.dedent(
        f"""
        Source: {source}
        Source type: {group}
        Topics: {", ".join(topics)}
        Headline: {clean_headline_source(title)}
        RSS summary: {clean_text(summary_text) or "No useful RSS summary was provided."}
        Full story URL: {link}
        Stable story id: {story_id}
        """
    ).strip()
    instructions = """
    You are Terra inside Skim: an intellectually serious but readable news analyst.
    Use only the provided story material; do not invent unreported facts. Think through
    the event as a signal in a broader system. Return valid JSON with: analysis,
    watch_next, research, and links. analysis is 4-6 sentences that explain the deeper
    stakes, historical echo, who has leverage, who may react, and what future events this
    could trigger. watch_next is one sentence naming the concrete sign that would make
    the story more important. research is one sentence telling the reader what to learn
    next. links is exactly three objects with label and url fields. The first two links
    must be useful non-Wikipedia references tied to the story, such as source pages,
    official institutions, data/background pages, reputable topic hubs, or related
    coverage. The third and final link must be one relevant Wikipedia page.
    """
    return ai_json(provider, model, instructions, prompt, effort="medium", max_output_tokens=1200)


def coerce_learning_links(raw_links: object, fallback_links: tuple[tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
    raw_non_wiki_links: list[tuple[str, str]] = []
    raw_wiki_links: list[tuple[str, str]] = []
    if isinstance(raw_links, list):
        for item in raw_links:
            label = ""
            url = ""
            if isinstance(item, dict):
                label = str(item.get("label", "")).strip()
                url = str(item.get("url", "")).strip()
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                label = str(item[0]).strip()
                url = str(item[1]).strip()
            if label and url.startswith(("https://", "http://")):
                if is_wikipedia_url(url):
                    raw_wiki_links.append((label, url))
                else:
                    raw_non_wiki_links.append((label, url))

    fallback_non_wiki_links = [(label, url) for label, url in fallback_links if not is_wikipedia_url(url)]
    fallback_wiki_links = [(label, url) for label, url in fallback_links if is_wikipedia_url(url)]

    non_wiki_links: list[tuple[str, str]] = []
    seen_urls: set[str] = set()
    for label, url in [*raw_non_wiki_links, *fallback_non_wiki_links]:
        if url in seen_urls:
            continue
        non_wiki_links.append((label, url))
        seen_urls.add(url)
        if len(non_wiki_links) == 2:
            break

    wiki_link = (raw_wiki_links + fallback_wiki_links)[0]
    return tuple([*non_wiki_links[:2], wiki_link])


def learning_links_text(links: tuple[tuple[str, str], ...]) -> str:
    return " ".join(f"[{label}]({url})" for label, url in links)


def strip_markdown_links(text: str) -> str:
    return re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r"\1", text)


def context_is_too_generic(context: str, story: Story) -> bool:
    normalized_context = clean_text(context).lower()
    if not normalized_context:
        return True

    old_generic_markers = (
        "machinery of escalation",
        "multiple outlets are converging",
        "wider struggle over power, legitimacy, and public trust",
        "business story with consequences beyond one company or sector",
        "technology story about control",
        "stress test for health systems and public trust",
    )
    if not any(marker in normalized_context for marker in old_generic_markers):
        return False

    title_tokens = set(significant_words(clean_headline_source(story.title)))
    context_tokens = set(significant_words(context))
    return len(title_tokens.intersection(context_tokens)) < 3


def ensure_robust_summary(summary: str, story: Story, detail: int) -> str:
    minimum_sentences = 4
    if sentence_count(summary) >= minimum_sentences and not is_weak_summary(summary):
        return summary

    fallback_sentences = split_sentences(happened_summary(story, detail))
    summary_sentences = [sentence for sentence in split_sentences(summary) if not is_weak_summary(sentence)]
    combined: list[str] = []
    for sentence in [*summary_sentences, *fallback_sentences]:
        if sentence and sentence not in combined:
            combined.append(sentence)
        if len(combined) == minimum_sentences:
            break

    while len(combined) < minimum_sentences:
        if len(combined) == 0:
            combined.append(excerpt(clean_headline_source(story.title), width=220))
        elif len(combined) == 1:
            combined.append("The story is important enough to watch because it has surfaced across a live news feed.")
        elif len(combined) == 2:
            combined.append("The full article should clarify the specific facts, timeline, and people involved.")
        else:
            combined.append("The bigger value is understanding what this event reveals beyond the first headline.")
    return " ".join(combined)


def smart_summarize(story: Story, detail: int, refresh_key: str, research_text: str = "") -> dict[str, str]:
    provider = configured_ai_provider()
    if not provider:
        return summarize(story, detail)

    topics = infer_topics(story)
    fallback_links = story_learning_links(story, topics)
    gathered_research = research_text or research_notes(story, topics)
    try:
        ai_result = ai_summary_cached(
            provider,
            ai_model(provider, deep=False),
            AI_SUMMARY_PROMPT_VERSION,
            refresh_key,
            story.id,
            story.title,
            story.source,
            story.group,
            story.summary_text,
            story.link,
            topics,
            detail,
            gathered_research,
        )
    except Exception:
        return summarize(story, detail)

    ai_headline = clean_text(strip_markdown_links(str(ai_result.get("headline", ""))))
    summary = clean_text(strip_markdown_links(str(ai_result.get("summary", ""))))
    background_value = ai_result.get("background") or ai_result.get("context", "")
    background = clean_text(strip_markdown_links(str(background_value)))
    links = learning_links_text(coerce_learning_links(ai_result.get("links"), fallback_links))
    summary = ensure_robust_summary(summary, story, detail)
    if context_is_too_generic(background, story):
        background = context_text(story, topics)

    return {
        "__headline": sensible_display_headline(ai_headline, story),
        "": summary,
        "Background": background,
        "Learn More": f"Learn more: {links}",
    }


def deeper_analysis(story: Story) -> dict[str, str]:
    topics = infer_topics(story)
    fallback_links = story_learning_links(story, topics)
    provider = configured_ai_provider()
    if not provider:
        return {
            "Deeper analysis": "Add GEMINI_API_KEY in Streamlit secrets to enable free AI deeper analysis for this story.",
            "Research trail": f"Learn more: {learning_links_text(fallback_links)}",
        }

    ai_result = ai_deep_analysis_cached(
        provider,
        ai_model(provider, deep=True),
        story.id,
        story.title,
        story.source,
        story.group,
        story.summary_text,
        story.link,
        topics,
    )
    links = learning_links_text(coerce_learning_links(ai_result.get("links"), fallback_links))
    analysis = clean_text(strip_markdown_links(str(ai_result.get("analysis", "")))) or context_text(story, topics)
    watch_next = clean_text(strip_markdown_links(str(ai_result.get("watch_next", ""))))
    research = clean_text(strip_markdown_links(str(ai_result.get("research", "")))) or lesson_text(story, topics)

    result = {"Deeper analysis": analysis}
    if watch_next:
        result["Watch next"] = watch_next
    result["Research trail"] = f"{research} Learn more: {links}"
    return result


def story_age(story: Story) -> str:
    if not story.published:
        return "recent"
    now = datetime.now(story.published.tzinfo or timezone.utc)
    delta = now - story.published
    hours = max(0, int(delta.total_seconds() // 3600))
    if hours < 1:
        return "just now"
    if hours < 24:
        return f"{hours}h ago"
    return story.published.strftime("%b %-d")


def share_sms_url(story: Story) -> str:
    body = urllib.parse.quote(f"{clean_headline_source(story.title)} {story.link}")
    return f"sms:&body={body}"


def render_summary_value(value: str) -> str:
    link_pattern = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")

    def render_link_pills(link_text: str) -> str:
        rendered_links = []
        cursor = 0
        for match in link_pattern.finditer(link_text):
            lead_text = link_text[cursor : match.start()].replace(" / ", " ")
            rendered_links.append(html.escape(lead_text))
            label = html.escape(match.group(1))
            url = html.escape(match.group(2), quote=True)
            rendered_links.append(
                f'<a class="lesson-link" href="{url}" target="_blank" rel="noopener noreferrer">{label}</a>'
            )
            cursor = match.end()
        rendered_links.append(html.escape(link_text[cursor:].replace(" / ", " ")))
        return "".join(rendered_links)

    learn_more_match = re.search(r"\s*Learn more:\s*", value, flags=re.IGNORECASE)
    if learn_more_match:
        intro = html.escape(value[: learn_more_match.start()].strip())
        link_text = value[learn_more_match.end() :]
        learn_more = (
            '<div class="learn-more-row">'
            '<span class="learn-more-label">Learn More</span>'
            f"{render_link_pills(link_text)}"
            "</div>"
        )
        return f"{intro}{learn_more}" if intro else learn_more

    rendered = []
    cursor = 0
    for match in link_pattern.finditer(value):
        lead_text = value[cursor : match.start()].replace(" / ", " ")
        rendered.append(html.escape(lead_text))
        label = html.escape(match.group(1))
        url = html.escape(match.group(2), quote=True)
        rendered.append(
            f'<a class="lesson-link" href="{url}" target="_blank" rel="noopener noreferrer">{label}</a>'
        )
        cursor = match.end()
    rendered.append(html.escape(value[cursor:].replace(" / ", " ")))
    return "".join(rendered)


def render_story(ranked_story: RankedStory, detail: int, refresh_key: str) -> None:
    story = ranked_story.story
    archived = story.id in st.session_state.archived
    with st.container(border=True):
        story_word = "story" if ranked_story.topic_story_count == 1 else "stories"
        meta = (
            f"{story.group} / {story_age(story)} / reference score {ranked_story.references}x / "
            f"{ranked_story.topic_story_count} {story_word} on this topic"
        )
        st.markdown(f'<div class="story-meta">{html.escape(meta)}</div>', unsafe_allow_html=True)
        topics = infer_topics(story)
        provider = configured_ai_provider()
        gathered_research = research_notes(story, topics) if provider else ""
        summary = smart_summarize(story, detail, refresh_key, gathered_research)
        display_headline = summary.pop("__headline", headline(story.title, 0))
        story_title_text = html.escape(display_headline)
        if story.image_url:
            story_title = f'<h2 class="story-title">{story_title_text}</h2>'
            title_col, image_col = st.columns([3, 1], vertical_alignment="top")
            with title_col:
                st.markdown(story_title, unsafe_allow_html=True)
            with image_col:
                image_url = html.escape(story.image_url, quote=True)
                st.markdown(f'<img class="story-image" src="{image_url}" alt="">', unsafe_allow_html=True)
        else:
            story_title = f'<h2 class="story-title story-title-full">{story_title_text}</h2>'
            st.markdown(story_title, unsafe_allow_html=True)

        rows = ""
        for label, value in summary.items():
            if label.startswith("__"):
                continue
            label_html = "" if label == "Learn More" else (f"<b>{html.escape(label)}:</b> " if label else "")
            rows += f'<div class="summary-field">{label_html}{render_summary_value(value)}</div>'
        st.markdown(f'<div class="summary-grid">{rows}</div>', unsafe_allow_html=True)
        cost_note = openai_cost_note(story, gathered_research)
        if cost_note:
            st.markdown(f'<div class="story-ai-cost">{html.escape(cost_note)}</div>', unsafe_allow_html=True)

        col1, col2, col3, col4 = st.columns([1, 1, 1, 1], gap="small", vertical_alignment="top")
        with col1:
            st.link_button("Full story", story.link, use_container_width=True)
        with col2:
            label = "Archived" if archived else "Archive"
            if st.button(label, key=f"archive-{story.id}", icon=":material/bookmark:", use_container_width=True):
                if archived:
                    st.session_state.archived.remove(story.id)
                else:
                    st.session_state.archived.add(story.id)
                st.rerun()
        with col3:
            st.link_button("Share", share_sms_url(story), use_container_width=True)
        with col4:
            if st.button("Deep analysis", key=f"deep-{story.id}", use_container_width=True):
                with st.spinner("Building the deeper read..."):
                    try:
                        st.session_state.deep_analyses[story.id] = deeper_analysis(story)
                    except Exception as exc:
                        st.session_state.deep_analyses[story.id] = {
                            "Deeper analysis": f"The AI provider could not complete this request: {exc}"
                        }

        if story.id in st.session_state.deep_analyses:
            deep_rows = ""
            for label, value in st.session_state.deep_analyses[story.id].items():
                label_html = f"<b>{html.escape(label)}:</b> "
                deep_rows += f'<div class="summary-field">{label_html}{render_summary_value(value)}</div>'
            st.markdown(f'<div class="summary-grid">{deep_rows}</div>', unsafe_allow_html=True)

        source = f"Source: {story.source}"
        st.markdown(f'<div class="story-source">{html.escape(source)}</div>', unsafe_allow_html=True)


def render_header() -> None:
    st.markdown(
        f"""
        <div class="skim-header">
            <div>
                <div class="skim-brand">{APP_NAME}</div>
                <div class="skim-tagline">Fast personal news, trimmed to what matters.</div>
            </div>
            <div class="skim-pill">{html.escape(ai_provider_label())}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def settings_signature(
    selected_topics: list[str],
    include_aggregators: bool,
    include_social: bool,
    show_archived: bool,
    keywords: tuple[str, ...],
) -> tuple:
    return (tuple(selected_topics), include_aggregators, include_social, show_archived, keywords)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_iso_datetime(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def prune_shown_cluster_history() -> None:
    history = dict(st.session_state.get("shown_cluster_history", {}))
    cutoff = utc_now() - timedelta(hours=NO_REPEAT_HOURS)
    pruned = {
        cluster_key: timestamp
        for cluster_key, timestamp in history.items()
        if (parsed := parse_iso_datetime(str(timestamp))) and parsed >= cutoff
    }
    st.session_state.shown_cluster_history = pruned


def mark_batch_shown(batch: list[RankedStory]) -> None:
    if not batch:
        return
    now = utc_now().isoformat()
    history = dict(st.session_state.get("shown_cluster_history", {}))
    for item in batch:
        history[item.cluster_key] = now
    st.session_state.shown_cluster_history = history
    st.session_state.batch_refreshed_at = now
    st.session_state.batch_refresh_id = now


def batch_refreshed_label() -> str:
    refreshed_at = parse_iso_datetime(str(st.session_state.get("batch_refreshed_at", "")))
    if not refreshed_at:
        return ""
    local_time = refreshed_at.astimezone()
    formatted = local_time.strftime("%b %d, %Y at %I:%M %p %Z")
    return formatted.replace(" 0", " ").replace(" at 0", " at ")


def render_batch_timestamp(batch_size: int) -> None:
    label = batch_refreshed_label()
    if not label:
        return
    story_word = "article" if batch_size == 1 else "articles"
    st.caption(f"{batch_size} {story_word} refreshed: {label} · No repeats for {NO_REPEAT_HOURS} hours")


def ranked_item_is_available(item: RankedStory, show_archived: bool, blocked_cluster_keys: set[str]) -> bool:
    return (
        item.cluster_key not in blocked_cluster_keys
        and (show_archived or item.story.id not in st.session_state.archived)
    )


def current_batch_from_keys(
    ranked_stories: list[RankedStory],
    keyword_rankings: dict[str, list[RankedStory]],
    show_archived: bool,
) -> list[RankedStory]:
    current_cluster_keys = st.session_state.current_cluster_keys
    if not current_cluster_keys:
        return []

    available_by_key: dict[str, RankedStory] = {}
    for item in ranked_stories:
        available_by_key.setdefault(item.cluster_key, item)
    for keyword_items in keyword_rankings.values():
        for item in keyword_items:
            available_by_key.setdefault(item.cluster_key, item)

    current: list[RankedStory] = []
    for cluster_key in current_cluster_keys:
        item = available_by_key.get(cluster_key)
        if item and ranked_item_is_available(item, show_archived, set()):
            current.append(item)
    return current


def select_batch(
    ranked_stories: list[RankedStory],
    keyword_rankings: dict[str, list[RankedStory]],
    show_archived: bool,
) -> list[RankedStory]:
    prune_shown_cluster_history()
    shown_cluster_keys = set(st.session_state.shown_cluster_history)
    current = current_batch_from_keys(ranked_stories, keyword_rankings, show_archived)
    if current:
        return current

    batch: list[RankedStory] = []
    used_cluster_keys: set[str] = set()

    for item in ranked_stories:
        if len(batch) >= BATCH_SIZE:
            break
        if ranked_item_is_available(item, show_archived, shown_cluster_keys | used_cluster_keys):
            batch.append(item)
            used_cluster_keys.add(item.cluster_key)

    for keyword in keyword_rankings:
        for item in keyword_rankings[keyword]:
            if ranked_item_is_available(item, show_archived, shown_cluster_keys | used_cluster_keys):
                batch.append(item)
                used_cluster_keys.add(item.cluster_key)
                break

    st.session_state.current_cluster_keys = [item.cluster_key for item in batch]
    mark_batch_shown(batch)
    return batch


def main() -> None:
    st.set_page_config(page_title=APP_NAME, page_icon="S", layout="centered")
    page_style()

    if "archived" not in st.session_state:
        st.session_state.archived = set()
    if "current_cluster_keys" not in st.session_state:
        st.session_state.current_cluster_keys = []
    if "last_settings" not in st.session_state:
        st.session_state.last_settings = None
    if "deep_analyses" not in st.session_state:
        st.session_state.deep_analyses = {}
    if "shown_cluster_history" not in st.session_state:
        legacy_seen = st.session_state.get("seen_cluster_keys", set())
        now = utc_now().isoformat()
        st.session_state.shown_cluster_history = {cluster_key: now for cluster_key in legacy_seen}
    if "batch_refresh_id" not in st.session_state:
        st.session_state.batch_refresh_id = ""
    if "batch_refreshed_at" not in st.session_state:
        st.session_state.batch_refreshed_at = ""
    st.session_state.setdefault("selected_topics", ["World", "US", "Politics", "Tech", "AI", "Reddit Hot"])
    st.session_state.setdefault("detail", 3)
    st.session_state.setdefault("include_social", True)
    st.session_state.setdefault("include_aggregators", True)
    st.session_state.setdefault("show_archived", False)
    initialize_keyword_state()

    render_header()

    if st.button("Complete story refresh", icon=":material/sync:", use_container_width=True):
        complete_story_refresh()

    selected_topics = st.session_state.selected_topics
    detail = st.session_state.detail
    include_social = st.session_state.include_social
    include_aggregators = st.session_state.include_aggregators
    show_archived = st.session_state.show_archived
    keywords = custom_keywords()

    current_settings = settings_signature(selected_topics, include_aggregators, include_social, show_archived, keywords)
    if st.session_state.last_settings != current_settings:
        st.session_state.current_cluster_keys = []
        st.session_state.last_settings = current_settings

    with st.spinner("Building a stronger story list..."):
        stories, errors = fetch_stories(tuple(selected_topics), include_aggregators, include_social, ())
        ranked_stories = rank_stories(stories, keywords)
        keyword_rankings, keyword_errors = fetch_keyword_rankings(keywords)
        errors.extend(keyword_errors)
    batch = select_batch(ranked_stories, keyword_rankings, show_archived)
    render_batch_timestamp(len(batch))

    if not batch:
        st.info(
            "No stories had enough reported material for this setup. Skim now filters out headline-only items; "
            f"it also will not repeat stories shown in the last {NO_REPEAT_HOURS} hours. Open Customize and broaden the topics or source types."
        )
    else:
        refresh_key = str(st.session_state.get("batch_refresh_id", ""))
        for ranked_story in batch:
            render_story(
                ranked_story,
                detail=detail,
                refresh_key=refresh_key,
            )

    st.divider()

    col1, col2, col3 = st.columns([1, 1, 1])
    col1.metric("Stories", len(batch))
    col2.metric("Archived", len(st.session_state.archived))
    if col3.button("Refresh", icon=":material/refresh:", use_container_width=True):
        fetch_research_snippet.clear()
        st.session_state.current_cluster_keys = []
        st.session_state.deep_analyses = {}
        st.rerun()

    if errors:
        with st.expander("Feed notes", expanded=False):
            for error in errors[:12]:
                st.write(error)

    with st.expander("Customize", expanded=False):
        st.multiselect(
            "Topics",
            options=list(TOPICS.keys()),
            key="selected_topics",
        )
        col1, col2 = st.columns(2)
        with col1:
            st.slider("Summary depth", min_value=1, max_value=5, step=1, key="detail")
        with col2:
            st.toggle("Reddit and Hacker News", key="include_social")
            st.toggle("Google News aggregators", key="include_aggregators")
            st.toggle("Show archived stories", key="show_archived")
            if st.button("Clear 48-hour history", use_container_width=True):
                st.session_state.shown_cluster_history = {}
                st.session_state.current_cluster_keys = []
                st.rerun()
        st.markdown("Keyword boosters")
        st.caption("Each saved keyword adds one extra trending article on top of the 15-story main feed.")
        for row_index in range(3):
            cols = st.columns(3)
            for col_index, col in enumerate(cols):
                keyword_index = (row_index * 3) + col_index
                with col:
                    st.text_input(
                        f"Keyword {keyword_index + 1}",
                        key=f"custom_keyword_{keyword_index}",
                        placeholder=f"Keyword {keyword_index + 1}",
                        label_visibility="collapsed",
                    )
        persist_keywords_to_query_params()
        st.caption(
            "X is not included yet because the official useful API paths generally require paid access. "
            "Skim can add it later when you want to connect an X developer account."
        )

    st.markdown(
        """
        <p class="skim-footnote">
            Skim uses public RSS feeds. Add OPENAI_API_KEY in Streamlit secrets for AI-written
            headlines, summaries, and Background. Without an AI key, Skim uses local summaries.
        </p>
        """,
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
