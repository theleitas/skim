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
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Iterable

import streamlit as st
import streamlit.components.v1 as components


APP_NAME = "Skim"
BATCH_SIZE = 20
ITEMS_PER_SOURCE = 50
FEED_TIMEOUT_SECONDS = 15
OPENAI_SUMMARY_MODEL = "gpt-5.6-luna"
OPENAI_DEEP_MODEL = "gpt-5.6-terra"
GEMINI_SUMMARY_MODEL = "gemini-2.5-flash"
GEMINI_DEEP_MODEL = "gemini-2.5-pro"
GROQ_SUMMARY_MODEL = "llama-3.3-70b-versatile"
GROQ_DEEP_MODEL = "llama-3.3-70b-versatile"
XAI_SUMMARY_MODEL = "grok-4.20-0309-non-reasoning"
XAI_DEEP_MODEL = "grok-4.5"
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
                font-size: clamp(1.18rem, 2vw, 1.42rem);
                line-height: 1.28;
                margin: 0 0 1.1rem 0;
                color: var(--skim-ink);
                max-width: 34rem;
                display: -webkit-box;
                -webkit-box-orient: vertical;
                -webkit-line-clamp: 2;
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
                border: 1px solid #5a554d;
                border-radius: 999px;
                background: #2a2927;
                color: #f1c45b;
                padding: 0.16rem 0.5rem;
                margin: 0.12rem 0.16rem 0.12rem 0;
                text-decoration: none;
                white-space: nowrap;
                box-shadow: 0 0 8px rgba(210, 210, 210, 0.08);
            }

            .lesson-link:hover {
                border-color: #80776a;
                background: #34322f;
                text-decoration: none;
            }

            .interaction-label {
                color: var(--skim-muted);
                font-size: 0.76rem;
                text-transform: uppercase;
                margin: 0.85rem 0 0.35rem 0;
            }

            .share-button {
                border: 1px solid #c8c8c8;
                background: #d8d8d8;
                color: #111111;
                border-radius: 6px;
                padding: 0;
                font-size: 0.78rem;
                cursor: pointer;
                width: 100%;
                min-height: 2.15rem;
                line-height: 1;
                box-shadow: 0 0 13px rgba(210, 210, 210, 0.22);
            }

            .share-button:hover {
                border-color: var(--skim-accent);
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
    st.session_state.seen_cluster_keys = set()
    st.session_state.current_cluster_keys = []
    st.session_state.last_settings = None


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
    max_words = min(max_words, 10)
    words = clean_headline_source(title).split()
    if len(words) <= max_words:
        return " ".join(words)
    return " ".join(words[:max_words]).rstrip(",:;")


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


def context_text(story: Story, topics: tuple[str, ...]) -> str:
    haystack = story_haystack(story)

    if "wildberries" in haystack:
        return (
            "This is a sign that the Russia-Ukraine war is pushing deeper into the infrastructure of ordinary economic life. "
            "Wildberries is not just a retailer; it represents logistics, warehousing, consumer access, and the private-sector systems Russians rely on every day. "
            "If attacks like this continue, they could pressure insurers, landlords, delivery networks, regional officials, and business owners, while signaling that Ukraine wants the costs of war felt inside Russia's domestic economy."
        )
    if has_any(haystack, ("hormuz", "iran", "missile", "strike", "war", "defense bill", "military", "nuclear deal")):
        return (
            "This is really about the machinery of escalation: military threats, energy routes, alliances, and domestic politics all pressing on each other at once. "
            "If leaders treat the event as a test of credibility, it can trigger follow-on moves from rivals, shipping disruptions, oil-price anxiety, emergency diplomacy, or new defense spending. "
            "The larger issue is whether the current international system can contain conflict before symbolic retaliation becomes a cycle."
        )
    if has_any(haystack, ("tariff", "trade", "dairy", "supply chain", "imports", "exports", "sanctions")):
        return (
            "This story points to how trade policy has become a tool of power rather than just economics. "
            "A dispute over one sector can trigger retaliation, lobbying at home, price pressure for consumers, and negotiations that spill into unrelated areas like security or immigration. "
            "The larger pattern is a world moving away from frictionless globalization toward bargaining, protection, and economic nationalism."
        )
    if has_any(haystack, ("meta", "social media", "addiction", "algorithm", "platform", "tiktok", "reddit", "online")):
        return (
            "This is part of the wider fight over whether digital platforms are neutral tools or environments that shape behavior, politics, and mental health. "
            "Even a narrow lawsuit or policy change can trigger copycat claims, regulatory hearings, school or family policy shifts, and pressure on companies to reveal how their systems work. "
            "The bigger question is who should be responsible when software design becomes social infrastructure."
        )
    if has_any(haystack, ("earnings", "profit", "alphabet", "google", "ai spending", "cloud", "startup", "semiconductor", "chips")):
        return (
            "This is a signal about the economics behind the next technology cycle. "
            "Investors, competitors, workers, and regulators will read it as evidence for whether heavy spending on AI and infrastructure is turning into durable advantage or just a costly arms race. "
            "It could trigger more capital spending, consolidation, layoffs, new regulation, or a market repricing of which companies actually control the future stack."
        )
    if has_any(haystack, ("climate", "temperature", "heat", "warming", "flood", "drought", "wildfire", "emissions")):
        return (
            "This is a glimpse of climate change as a lived systems problem, not just an environmental headline. "
            "Heat, water stress, migration, food production, insurance, public health, and local economies can all move together when the physical baseline changes. "
            "The story may trigger adaptation spending or political fights over responsibility, but the larger issue is whether communities can adjust before disruption becomes normal."
        )
    if has_any(haystack, ("protest", "censor", "censorship", "rights", "police", "blackmail", "rape", "court", "jailed", "trial")):
        return (
            "This is about institutional legitimacy: whether courts, police, governments, or public platforms are trusted to handle power fairly. "
            "Stories like this can trigger protests, legal reforms, backlash, copycat scrutiny, or a deeper loss of confidence if people see the system protecting itself. "
            "The larger pattern is a contest over who gets believed, who gets punished, and whether public institutions can still produce shared facts."
        )
    if has_any(haystack, ("health", "hospital", "disease", "vaccine", "drug", "medicine", "public health", "outbreak")):
        return (
            "This is a stress test for health systems and public trust. "
            "A single development can change patient behavior, funding priorities, regulation, and how much confidence people place in experts or institutions. "
            "The bigger issue is whether the system can respond early and transparently, or whether it only moves once personal risk becomes impossible to ignore."
        )
    if has_any(haystack, ("hack", "cyber", "data breach", "ransomware", "security flaw", "leak")):
        return (
            "This is a reminder that digital security failures are now real-world governance problems. "
            "One breach can trigger lawsuits, regulation, copycat attacks, insurance changes, and lasting damage to trust between users and institutions. "
            "The larger issue is that modern life depends on systems most people cannot inspect, but everyone is forced to rely on."
        )
    if has_any(haystack, ("louvre", "museum", "jewel", "artifact", "heritage", "art theft")):
        return (
            "This is not only a crime story; it is about how societies protect shared memory and public trust. "
            "A high-profile breach can trigger security overhauls, political blame, insurance changes, and renewed questions about who gets to own or safeguard cultural treasures. "
            "The larger issue is that symbolic places carry national identity, so failures there feel bigger than the immediate loss."
        )
    if story.group == "Social":
        return (
            "This is still an early signal, which is exactly why it matters. "
            "Social attention can reveal something before formal institutions catch up, but it can also distort scale, context, and certainty. "
            "Watch whether the story crosses into verified reporting, official response, market behavior, or policy debate; that transition is what turns online heat into real-world consequence."
        )
    if story.group == "Aggregator":
        return (
            "The important clue is that multiple outlets are converging on the same subject at the same time. "
            "That kind of pickup can turn a story into a feedback loop: politicians respond to coverage, markets or institutions react to the response, and the framing hardens before the full facts settle. "
            "The larger question is whether this becomes a durable issue or just a burst of attention around a volatile moment."
        )
    if "Business" in topics:
        return (
            "This is a business story with consequences beyond one company or sector. "
            "Changes in pricing, demand, labor, capital spending, or regulation can ripple into households and competitors faster than the headline suggests. "
            "The larger issue is whether this reflects a temporary adjustment or a deeper shift in how value and leverage are moving through the economy."
        )
    if "Tech" in topics or "AI" in topics:
        return (
            "This is a technology story about control: who builds the tools, who depends on them, and who absorbs the risk when they change. "
            "It may trigger regulation, new investment, user backlash, or competitive moves from companies trying not to fall behind. "
            "The bigger pattern is that technical decisions are increasingly becoming labor, privacy, education, and governance decisions."
        )
    if "Politics" in topics or "World" in topics or "US" in topics:
        return (
            "This sits inside a wider struggle over power, legitimacy, and public trust. "
            "The event itself may be brief, but the response can set precedents that shape alliances, elections, institutional behavior, or citizen expectations. "
            "The key question is whether it resolves pressure or reveals that the pressure has been building for a long time."
        )
    return (
        "This story matters most as a signal. "
        "It may not be world-changing by itself, but it points to incentives, risks, or tensions that could show up again in stronger form. "
        "The useful move is to ask what system produced it, who benefits if it continues, and what would happen if it spreads."
    )


def summarize(story: Story, detail: int) -> dict[str, str]:
    topics = infer_topics(story)
    links = learning_links_text(story_learning_links(story, topics))

    return {
        "": happened_summary(story, detail),
        "Context": context_text(story, topics),
        "Lesson": f"{lesson_text(story, topics)} Learn more: {links}",
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
        "gemini": bool(secret_or_env("GEMINI_API_KEY")),
        "groq": bool(secret_or_env("GROQ_API_KEY")),
        "xai": bool(secret_or_env("XAI_API_KEY")),
        "openai": bool(secret_or_env("OPENAI_API_KEY")),
    }
    requested = secret_or_env("SKIM_AI_PROVIDER").lower()
    if requested in available and available[requested]:
        return requested
    for provider in ("gemini", "groq", "xai", "openai"):
        if available[provider]:
            return provider
    return ""


def ai_provider_label() -> str:
    labels = {
        "gemini": "Gemini free tier",
        "groq": "Groq free tier",
        "xai": "xAI Grok",
        "openai": "OpenAI Luna + Terra",
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
    story_id: str,
    title: str,
    source: str,
    group: str,
    summary_text: str,
    link: str,
    topics: tuple[str, ...],
    detail: int,
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
        """
    ).strip()
    instructions = """
    You are Skim, a sharp personal news analyst. Use only the provided headline, source,
    RSS summary, topic labels, and URL; do not invent facts. Return valid JSON with:
    summary, context, lesson, and links. summary is at least three sentences explaining
    what happened in plain English, never the word "comments", and never just a headline.
    context is one thoughtful paragraph
    about the larger system, historical pattern, likely follow-on effects, and why this
    event is a signal. lesson is a succinct thing to know, understand, or research next.
    links is exactly three objects with label and url fields. The first two links must
    be useful non-Wikipedia references tied to the story, such as source pages,
    official institutions, data/background pages, reputable topic hubs, or related
    coverage. The third and final link must be one relevant Wikipedia page for the
    central entity, conflict, institution, technology, geography, or historical pattern.
    """
    return ai_json(provider, model, instructions, prompt, effort="low", max_output_tokens=1300)


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


def ensure_three_sentence_summary(summary: str, story: Story, detail: int) -> str:
    if sentence_count(summary) >= 3 and not is_weak_summary(summary):
        return summary

    fallback_sentences = split_sentences(happened_summary(story, detail))
    summary_sentences = [sentence for sentence in split_sentences(summary) if not is_weak_summary(sentence)]
    combined: list[str] = []
    for sentence in [*summary_sentences, *fallback_sentences]:
        if sentence and sentence not in combined:
            combined.append(sentence)
        if len(combined) == 3:
            break

    while len(combined) < 3:
        if len(combined) == 0:
            combined.append(excerpt(clean_headline_source(story.title), width=220))
        elif len(combined) == 1:
            combined.append("The story is important enough to watch because it has surfaced across a live news feed.")
        else:
            combined.append("The full article should clarify the specific facts, timeline, and people involved.")
    return " ".join(combined)


def smart_summarize(story: Story, detail: int) -> dict[str, str]:
    provider = configured_ai_provider()
    if not provider:
        return summarize(story, detail)

    topics = infer_topics(story)
    fallback_links = story_learning_links(story, topics)
    try:
        ai_result = ai_summary_cached(
            provider,
            ai_model(provider, deep=False),
            story.id,
            story.title,
            story.source,
            story.group,
            story.summary_text,
            story.link,
            topics,
            detail,
        )
    except Exception:
        return summarize(story, detail)

    summary = clean_text(strip_markdown_links(str(ai_result.get("summary", ""))))
    context = clean_text(strip_markdown_links(str(ai_result.get("context", ""))))
    lesson = clean_text(strip_markdown_links(str(ai_result.get("lesson", ""))))
    links = learning_links_text(coerce_learning_links(ai_result.get("links"), fallback_links))
    summary = ensure_three_sentence_summary(summary, story, detail)
    if not context:
        context = context_text(story, topics)
    if not lesson:
        lesson = lesson_text(story, topics)

    return {
        "": summary,
        "Context": context,
        "Lesson": f"{lesson} Learn more: {links}",
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


def share_component(story: Story) -> None:
    payload_title = html.escape(story.title, quote=True)
    payload_url = html.escape(story.link, quote=True)
    fallback = urllib.parse.quote(story.link)
    components.html(
        f"""
        <button class="share-button" onclick="
            if (navigator.share) {{
                navigator.share({{title: '{payload_title}', url: '{payload_url}'}});
            }} else {{
                window.open('sms:&body={fallback}', '_blank');
            }}
        ">Share</button>
        <style>
            .share-button {{
                border: 1px solid #c8c8c8;
                background: #d8d8d8;
                color: #111111;
                border-radius: 6px;
                padding: 0;
                font-size: 0.78rem;
                cursor: pointer;
                width: 100%;
                min-height: 2.15rem;
                height: 2.15rem;
                line-height: 1;
                box-shadow: 0 0 13px rgba(210, 210, 210, 0.22);
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            }}
            .share-button:hover {{ border-color: #f1c45b; }}
        </style>
        """,
        height=38,
    )


def render_summary_value(value: str) -> str:
    link_pattern = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
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


def render_story(ranked_story: RankedStory, detail: int, max_headline_words: int) -> None:
    story = ranked_story.story
    archived = story.id in st.session_state.archived
    with st.container(border=True):
        story_word = "story" if ranked_story.topic_story_count == 1 else "stories"
        meta = (
            f"{story.group} / {story_age(story)} / reference score {ranked_story.references}x / "
            f"{ranked_story.topic_story_count} {story_word} on this topic"
        )
        st.markdown(f'<div class="story-meta">{html.escape(meta)}</div>', unsafe_allow_html=True)
        story_title_text = html.escape(headline(story.title, max_headline_words))
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

        summary = smart_summarize(story, detail)
        rows = ""
        for label, value in summary.items():
            label_html = f"<b>{html.escape(label)}:</b> " if label else ""
            rows += f'<div class="summary-field">{label_html}{render_summary_value(value)}</div>'
        st.markdown(f'<div class="summary-grid">{rows}</div>', unsafe_allow_html=True)

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
            share_component(story)
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


def select_batch(ranked_stories: list[RankedStory], show_archived: bool) -> list[RankedStory]:
    seen_cluster_keys = st.session_state.seen_cluster_keys
    current_cluster_keys = st.session_state.current_cluster_keys

    if current_cluster_keys:
        current = [item for item in ranked_stories if item.cluster_key in current_cluster_keys]
        if not show_archived:
            current = [item for item in current if item.story.id not in st.session_state.archived]
        if current:
            return current[:BATCH_SIZE]

    fresh = [
        item
        for item in ranked_stories
        if item.cluster_key not in seen_cluster_keys
        and (show_archived or item.story.id not in st.session_state.archived)
    ]
    if len(fresh) < BATCH_SIZE:
        st.session_state.seen_cluster_keys = set()
        fresh = [
            item
            for item in ranked_stories
            if show_archived or item.story.id not in st.session_state.archived
        ]

    batch = fresh[:BATCH_SIZE]
    st.session_state.current_cluster_keys = [item.cluster_key for item in batch]
    return batch


def main() -> None:
    st.set_page_config(page_title=APP_NAME, page_icon="S", layout="centered")
    page_style()

    if "archived" not in st.session_state:
        st.session_state.archived = set()
    if "seen_cluster_keys" not in st.session_state:
        st.session_state.seen_cluster_keys = set()
    if "current_cluster_keys" not in st.session_state:
        st.session_state.current_cluster_keys = []
    if "last_settings" not in st.session_state:
        st.session_state.last_settings = None
    if "deep_analyses" not in st.session_state:
        st.session_state.deep_analyses = {}
    st.session_state.setdefault("selected_topics", ["World", "US", "Politics", "Tech", "AI", "Reddit Hot"])
    st.session_state.setdefault("detail", 3)
    st.session_state.setdefault("max_headline_words", 10)
    st.session_state.max_headline_words = min(int(st.session_state.max_headline_words), 10)
    st.session_state.setdefault("include_social", True)
    st.session_state.setdefault("include_aggregators", True)
    st.session_state.setdefault("show_archived", False)
    initialize_keyword_state()

    render_header()

    if st.button("Complete story refresh", icon=":material/sync:", use_container_width=True):
        complete_story_refresh()

    selected_topics = st.session_state.selected_topics
    detail = st.session_state.detail
    max_headline_words = st.session_state.max_headline_words
    include_social = st.session_state.include_social
    include_aggregators = st.session_state.include_aggregators
    show_archived = st.session_state.show_archived
    keywords = custom_keywords()

    current_settings = settings_signature(selected_topics, include_aggregators, include_social, show_archived, keywords)
    if st.session_state.last_settings != current_settings:
        st.session_state.current_cluster_keys = []
        st.session_state.last_settings = current_settings

    with st.spinner("Building a stronger story list..."):
        stories, errors = fetch_stories(tuple(selected_topics), include_aggregators, include_social, keywords)
        ranked_stories = rank_stories(stories, keywords)
    batch = select_batch(ranked_stories, show_archived)

    if not batch:
        st.info("No stories matched this setup. Open Customize and broaden the topics or source types.")
    else:
        for ranked_story in batch:
            render_story(ranked_story, detail=detail, max_headline_words=max_headline_words)

    st.divider()

    col1, col2, col3 = st.columns([1, 1, 1])
    col1.metric("Stories", len(batch))
    col2.metric("Archived", len(st.session_state.archived))
    if col3.button("Refresh", icon=":material/refresh:", use_container_width=True):
        st.session_state.seen_cluster_keys.update(st.session_state.current_cluster_keys)
        st.session_state.current_cluster_keys = []
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
            st.slider("Headline words", min_value=6, max_value=10, step=1, key="max_headline_words")
        with col2:
            st.toggle("Reddit and Hacker News", key="include_social")
            st.toggle("Google News aggregators", key="include_aggregators")
            st.toggle("Show archived stories", key="show_archived")
            if st.button("Reset seen stories", use_container_width=True):
                st.session_state.seen_cluster_keys = set()
                st.session_state.current_cluster_keys = []
                st.rerun()
        st.markdown("Keyword boosters")
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
            Skim uses public RSS feeds. Add GEMINI_API_KEY in Streamlit secrets for the best free AI path;
            Groq, xAI, and OpenAI keys are optional fallbacks. Without an AI key, Skim uses local summaries.
        </p>
        """,
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
