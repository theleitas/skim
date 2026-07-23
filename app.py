from __future__ import annotations

import html
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
                line-height: 1.22;
                margin: 0 0 0.9rem 0;
                color: var(--skim-ink);
                max-width: 34rem;
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
                color: #f1c45b;
                text-decoration: none;
            }

            .lesson-link:hover {
                text-decoration: underline;
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
                padding: 0.32rem 0.56rem;
                font-size: 0.82rem;
                cursor: pointer;
                width: 100%;
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
                min-height: 1.95rem;
                font-size: 0.82rem;
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
    st.cache_data.clear()
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
        return excerpt(title_summary, width=300)

    useful_sentences = [sentence for sentence in split_sentences(story.summary_text) if not is_weak_summary(sentence)]
    if not useful_sentences:
        return excerpt(title_summary, width=300)

    happened = useful_sentences[0]
    if detail >= 4 and len(useful_sentences) > 1:
        happened = f"{happened} {useful_sentences[1]}"
    if is_weak_summary(happened):
        happened = title_summary
    return excerpt(happened, width=300)


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


def wikipedia_topic(story: Story, topics: tuple[str, ...]) -> tuple[str, str]:
    haystack = f"{story.title} {story.summary_text}".lower()
    candidates = (
        ("Strait of Hormuz", "Strait_of_Hormuz", ("hormuz",)),
        ("Iran", "Iran", ("iran", "tehran")),
        ("Saudi Arabia", "Saudi_Arabia", ("saudi",)),
        ("Tariff", "Tariff", ("tariff", "trade crosshairs")),
        ("Nuclear power", "Nuclear_power", ("nuclear",)),
        ("International relations", "International_relations", ("diplomacy", "alliance", "treaty")),
        ("Artificial intelligence", "Artificial_intelligence", (" ai ", "artificial intelligence", "openai", "model")),
        ("Social media", "Social_media", ("social media", "meta", "reddit", "x ")),
        ("Climate change", "Climate_change", ("climate", "temperature", "warming")),
        ("Public health", "Public_health", ("health", "hospital", "vaccine", "disease")),
        ("Supply chain", "Supply_chain", ("supply chain", "shipping", "ports")),
        ("Financial market", "Financial_market", ("market", "earnings", "stocks")),
        ("Human rights", "Human_rights", ("protest", "rights", "censorship")),
        ("Cybersecurity", "Computer_security", ("cyber", "hack", "data breach")),
    )
    padded_haystack = f" {haystack} "
    for label, slug, needles in candidates:
        if any(needle in padded_haystack for needle in needles):
            return label, f"https://en.wikipedia.org/wiki/{slug}"
    if "Business" in topics:
        return "Economics", "https://en.wikipedia.org/wiki/Economics"
    if "Tech" in topics or "AI" in topics:
        return "Technology", "https://en.wikipedia.org/wiki/Technology"
    if "Health" in topics:
        return "Public health", "https://en.wikipedia.org/wiki/Public_health"
    if "Science" in topics:
        return "Science", "https://en.wikipedia.org/wiki/Science"
    if "Politics" in topics or "World" in topics or "US" in topics:
        return "International relations", "https://en.wikipedia.org/wiki/International_relations"
    return "Current events", "https://en.wikipedia.org/wiki/Portal:Current_events"


def lesson_text(story: Story, topics: tuple[str, ...]) -> str:
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
    wiki_label, wiki_url = wikipedia_topic(story, topics)

    return {
        "": happened_summary(story, detail),
        "Context": context_text(story, topics),
        "Lesson": f"{lesson_text(story, topics)} Learn more: [{wiki_label}]({wiki_url})",
    }


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
                padding: 0.32rem 0.56rem;
                font-size: 0.82rem;
                cursor: pointer;
                width: 100%;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            }}
            .share-button:hover {{ border-color: #f1c45b; }}
        </style>
        """,
        height=45,
    )


def render_summary_value(value: str) -> str:
    link_match = re.search(r"\[([^\]]+)\]\((https://en\.wikipedia\.org/wiki/[^)]+)\)", value)
    if not link_match:
        return html.escape(value)

    before = value[: link_match.start()]
    after = value[link_match.end() :]
    label = html.escape(link_match.group(1))
    url = html.escape(link_match.group(2), quote=True)
    return (
        f"{html.escape(before)}"
        f'<a class="lesson-link" href="{url}" target="_blank" rel="noopener noreferrer">{label}</a>'
        f"{html.escape(after)}"
    )


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
        story_title = f'<h2 class="story-title">{html.escape(headline(story.title, max_headline_words))}</h2>'
        if story.image_url:
            title_col, image_col = st.columns([3, 1], vertical_alignment="top")
            with title_col:
                st.markdown(story_title, unsafe_allow_html=True)
            with image_col:
                image_url = html.escape(story.image_url, quote=True)
                st.markdown(f'<img class="story-image" src="{image_url}" alt="">', unsafe_allow_html=True)
        else:
            st.markdown(story_title, unsafe_allow_html=True)

        summary = summarize(story, detail)
        rows = ""
        for label, value in summary.items():
            label_html = f"<b>{html.escape(label)}:</b> " if label else ""
            rows += f'<div class="summary-field">{label_html}{render_summary_value(value)}</div>'
        st.markdown(f'<div class="summary-grid">{rows}</div>', unsafe_allow_html=True)

        col1, col2, col3 = st.columns([1, 1, 1])
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
            <div class="skim-pill">Free feeds / local summaries</div>
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
    st.session_state.setdefault("selected_topics", ["World", "US", "Politics", "Tech", "AI", "Reddit Hot"])
    st.session_state.setdefault("detail", 3)
    st.session_state.setdefault("max_headline_words", 13)
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
            st.slider("Headline words", min_value=8, max_value=16, step=1, key="max_headline_words")
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
            Skim currently uses public RSS feeds and an offline summarizer. No OpenAI key,
            paid news API, Reddit token, or X token is required for this first version.
        </p>
        """,
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
