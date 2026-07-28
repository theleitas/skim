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
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Callable, Iterable, Sequence

import streamlit as st


APP_NAME = "Skim"
BATCH_SIZE = 20
ITEMS_PER_SOURCE = 50
MAX_FEED_WORKERS = 8
FEED_TIMEOUT_SECONDS = 15
ARTICLE_TIMEOUT_SECONDS = 15
ARTICLE_MAX_BYTES = 3_000_000
ARTICLE_MAX_WORDS = 3_000
MIN_ARTICLE_WORDS = 120
MIN_ARTICLE_SENTENCES = 3
MAX_ARTICLE_CANDIDATES = 5
MAX_BASE_CANDIDATES = 40
MAX_KEYWORD_CANDIDATES = 10
MIN_SUMMARY_WORDS = 18
MIN_NEW_SUMMARY_TERMS = 7
NO_REPEAT_HOURS = 24
POPULAR_COVERAGE_HOURS = 24
FAST_COVERAGE_HOURS = 8
MAJOR_BREAKING_HOURS = 12
GDELT_DOC_API_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
GDELT_TIMEOUT_SECONDS = 20
GDELT_MAX_RECORDS = 250
GDELT_QUERY = (
    '(war OR attack OR election OR coup OR ceasefire OR sanctions OR earthquake OR wildfire '
    'OR flood OR outbreak OR cyberattack OR protest OR emergency OR tariff OR resignation '
    'OR "central bank" OR bankruptcy OR merger OR "artificial intelligence" OR "climate change") '
    "sourcelang:english"
)
OPENAI_SUMMARY_MODEL = "gpt-5.6-terra"
OPENAI_DEEP_MODEL = "gpt-5.6-terra"
AI_SUMMARY_PROMPT_VERSION = "grounded-article-v1"
GEMINI_SUMMARY_MODEL = "gemini-2.5-flash"
GEMINI_DEEP_MODEL = "gemini-2.5-pro"
GROQ_SUMMARY_MODEL = "llama-3.3-70b-versatile"
GROQ_DEEP_MODEL = "llama-3.3-70b-versatile"
XAI_SUMMARY_MODEL = "grok-4.20-0309-non-reasoning"
XAI_DEEP_MODEL = "grok-4.5"
OPENAI_MODEL_PRICES_PER_MTOK = {
    "gpt-5.6-luna": (1.00, 0.10, 6.00),
    "gpt-5.6-terra": (2.50, 0.25, 15.00),
    "gpt-5.6-sol": (5.00, 0.50, 30.00),
    "gpt-5.6": (5.00, 0.50, 30.00),
}
AI_COST_SCALE = 1_000_000
AI_COST_QUERY_TOTAL = "aiCostTotal"
AI_COST_QUERY_LATEST = "aiCostLatest"
AI_COST_QUERY_TOTAL_ARTICLES = "aiCostArticles"
AI_COST_QUERY_LATEST_ARTICLES = "aiCostLatestArticles"
AI_COST_QUERY_LAST_BATCH = "aiCostBatch"
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0 Safari/537.36 SkimPersonalNews/1.0"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.8",
}


@dataclass(frozen=True)
class NewsSource:
    name: str
    url: str
    group: str
    topics: tuple[str, ...]
    item_limit: int = ITEMS_PER_SOURCE


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
    coverage_span_hours: float = 0.0
    signal_label: str = ""
    outlets: tuple[str, ...] = ()
    article_candidates: tuple[Story, ...] = ()


@dataclass(frozen=True)
class ArticleEvidence:
    url: str
    title: str
    text: str
    word_count: int


@dataclass(frozen=True)
class PreparedStory:
    ranked_story: RankedStory
    evidence: ArticleEvidence
    card: dict[str, str]
    article_story: Story | None = None


@dataclass(frozen=True)
class SummaryAttempt:
    card: dict[str, str] | None
    ai_cost: float


TOPICS = {
    "World": ("world", "war", "conflict", "diplomacy", "election", "government"),
    "US": ("u.s.", "us ", "america", "congress", "white house", "supreme court"),
    "Politics": ("politic", "election", "senate", "president", "minister", "policy"),
    "Business": (
        "business",
        "company",
        "earnings",
        "market",
        "economy",
        "trade",
        "central bank",
        "bankruptcy",
        "merger",
        "tariff",
    ),
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
    NewsSource(
        "PBS News",
        "https://www.pbs.org/newshour/feeds/rss/headlines",
        "Major News",
        ("World", "US", "Politics", "Health", "Science", "Climate", "Culture"),
    ),
    NewsSource("The Guardian World", "https://www.theguardian.com/world/rss", "Major News", ("World", "Politics")),
    NewsSource("The Guardian US", "https://www.theguardian.com/us-news/rss", "Major News", ("US", "Politics")),
    NewsSource("Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml", "Major News", ("World",)),
    NewsSource(
        "Sky News",
        "https://feeds.skynews.com/feeds/rss/home.xml",
        "Major News",
        ("World", "US", "Politics", "Business", "Tech", "Culture"),
    ),
    NewsSource(
        "Deutsche Welle",
        "https://rss.dw.com/rdf/rss-en-all",
        "Major News",
        ("World", "Politics", "Business", "Climate"),
    ),
    NewsSource(
        "France 24",
        "https://www.france24.com/en/rss",
        "Major News",
        ("World", "Politics", "Business"),
    ),
    NewsSource(
        "Euronews",
        "https://www.euronews.com/rss?level=theme&name=news",
        "Major News",
        ("World", "Politics", "Business", "Climate"),
    ),
    NewsSource(
        "CBC News",
        "https://www.cbc.ca/cmlink/rss-topstories",
        "Major News",
        ("World", "US", "Politics", "Business", "Health", "Climate", "Culture"),
    ),
    NewsSource(
        "ABC Australia",
        "https://www.abc.net.au/news/feed/51120/rss.xml",
        "Major News",
        ("World", "Politics", "Business", "Climate", "Health", "Science", "Culture"),
    ),
    NewsSource(
        "RNZ",
        "https://www.rnz.co.nz/rss/news.xml",
        "Major News",
        ("World", "Politics", "Business", "Climate", "Health", "Culture"),
    ),
    NewsSource("NYT Top Stories", "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml", "Major News", ("World", "US")),
    NewsSource("NYT World", "https://rss.nytimes.com/services/xml/rss/nyt/World.xml", "Major News", ("World",)),
    NewsSource("NYT Technology", "https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml", "Major News", ("Tech", "AI")),
    NewsSource("CNN Top Stories", "http://rss.cnn.com/rss/cnn_topstories.rss", "Major News", ("World", "US")),
    NewsSource("ABC News", "https://abcnews.go.com/abcnews/topstories", "Major News", ("US", "World")),
    NewsSource("CBS News", "https://www.cbsnews.com/latest/rss/main", "Major News", ("US", "World")),
    NewsSource(
        "ProPublica",
        "https://www.propublica.org/feeds/propublica/main",
        "Specialist",
        ("US", "Politics", "Business", "Health", "Climate"),
    ),
    NewsSource(
        "Politico",
        "https://rss.politico.com/politics-news.xml",
        "Specialist",
        ("US", "Politics"),
    ),
    NewsSource(
        "TechCrunch",
        "https://techcrunch.com/feed/",
        "Specialist",
        ("Tech", "AI", "Business"),
    ),
    NewsSource(
        "The Verge",
        "https://www.theverge.com/rss/index.xml",
        "Specialist",
        ("Tech", "AI", "Science", "Culture"),
    ),
    NewsSource(
        "ESPN",
        "https://www.espn.com/espn/rss/news",
        "Specialist",
        ("Sports",),
    ),
    NewsSource(
        "MarketWatch",
        "https://feeds.content.dowjones.io/public/rss/mw_topstories",
        "Specialist",
        ("Business",),
    ),
    NewsSource(
        "Variety",
        "https://variety.com/feed/",
        "Specialist",
        ("Culture",),
    ),
    NewsSource(
        "NASA",
        "https://www.nasa.gov/news-release/feed/",
        "Specialist",
        ("Science", "Tech", "Climate"),
    ),
    NewsSource("Google News Top", "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en", "Aggregator", ("World", "US")),
    NewsSource("Google News World", "https://news.google.com/rss/headlines/section/topic/WORLD?hl=en-US&gl=US&ceid=US:en", "Aggregator", ("World",)),
    NewsSource("Google News Business", "https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=en-US&gl=US&ceid=US:en", "Aggregator", ("Business",)),
    NewsSource("Google News Technology", "https://news.google.com/rss/headlines/section/topic/TECHNOLOGY?hl=en-US&gl=US&ceid=US:en", "Aggregator", ("Tech", "AI")),
    NewsSource("Google News Science", "https://news.google.com/rss/headlines/section/topic/SCIENCE?hl=en-US&gl=US&ceid=US:en", "Aggregator", ("Science",)),
    NewsSource("Google News Health", "https://news.google.com/rss/headlines/section/topic/HEALTH?hl=en-US&gl=US&ceid=US:en", "Aggregator", ("Health",)),
    NewsSource(
        "Drudge Report",
        "https://feedpress.me/drudgereportfeed",
        "Aggregator",
        ("World", "US", "Politics", "Business", "Tech", "Culture"),
        item_limit=20,
    ),
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

MAJOR_OUTLET_MARKERS = (
    "abc news",
    "al jazeera",
    "associated press",
    "ap news",
    "bbc",
    "bloomberg",
    "cbc",
    "cbs news",
    "cnn",
    "deutsche welle",
    "dw",
    "financial times",
    "france 24",
    "guardian",
    "nbc news",
    "new york times",
    "npr",
    "pbs news",
    "reuters",
    "rnz",
    "sky news",
    "wall street journal",
    "washington post",
)

DOMAIN_SOURCE_NAMES = {
    "abcnews.go.com": "ABC News",
    "abc.net.au": "ABC Australia",
    "aljazeera.com": "Al Jazeera",
    "apnews.com": "Associated Press",
    "bbc.co.uk": "BBC",
    "bbc.com": "BBC",
    "bloomberg.com": "Bloomberg",
    "cbsnews.com": "CBS News",
    "cbc.ca": "CBC News",
    "cnn.com": "CNN",
    "dw.com": "Deutsche Welle",
    "ft.com": "Financial Times",
    "france24.com": "France 24",
    "euronews.com": "Euronews",
    "espn.com": "ESPN",
    "marketwatch.com": "MarketWatch",
    "nasa.gov": "NASA",
    "nbcnews.com": "NBC News",
    "npr.org": "NPR",
    "nytimes.com": "New York Times",
    "pbs.org": "PBS News",
    "politico.com": "Politico",
    "propublica.org": "ProPublica",
    "reuters.com": "Reuters",
    "rnz.co.nz": "RNZ",
    "sky.com": "Sky News",
    "techcrunch.com": "TechCrunch",
    "theguardian.com": "The Guardian",
    "theverge.com": "The Verge",
    "variety.com": "Variety",
    "washingtonpost.com": "Washington Post",
    "wsj.com": "Wall Street Journal",
}

BREAKING_NEWS_TERMS = {
    "airstrike",
    "assassination",
    "attack",
    "ceasefire",
    "collapse",
    "coup",
    "crash",
    "crisis",
    "dead",
    "death",
    "earthquake",
    "emergency",
    "evacuation",
    "explosion",
    "flood",
    "hostage",
    "invasion",
    "killed",
    "missile",
    "resigns",
    "resignation",
    "sanctions",
    "shooting",
    "strike",
    "tariff",
    "truce",
    "war",
    "wildfire",
}

CATEGORY_COLORS = {
    "World": "#39ff14",
    "Conflict": "#ff4f81",
    "US Politics": "#00e5ff",
    "Sports": "#ccff00",
    "Entertainment": "#ff7a00",
    "Technology": "#bb86fc",
    "Economy": "#ffe600",
}

CATEGORY_TERMS = {
    "Conflict": (
        "airstrike", "armed conflict", "armed forces", "attack", "bombing", "ceasefire", "drone",
        "drone strike", "drones", "hostage", "invasion", "military", "missile", "rocket fire",
        "settler violence", "shooting", "troops", "truce", "war", "warfare", "weapons",
    ),
    "US Politics": (
        "biden", "capitol hill", "congress", "democratic party", "department of justice",
        "doj", "federal judge", "house of representatives", "republican party", "senate",
        "supreme court", "trump", "u.s. election", "us election", "white house",
    ),
    "Sports": (
        "athlete", "baseball", "basketball", "coach", "cricket", "fifa", "formula 1",
        "golf", "hall of fame", "mlb", "mma", "nba", "nfl", "nhl", "olympic",
        "premier league", "soccer", "tennis", "tour de france", "world cup",
    ),
    "Entertainment": (
        "actor", "actress", "album", "box office", "celebrity", "concert", "film",
        "movie", "music", "musician", "netflix", "television", "tv series",
    ),
    "Technology": (
        "artificial intelligence", "chipmaker", "cyberattack", "cybersecurity", "data breach",
        "generative ai", "openai", "robot", "semiconductor", "software", "spacecraft",
        "startup", "technology",
    ),
    "Economy": (
        "acquisition", "bankruptcy", "central bank", "currency", "earnings", "economy",
        "federal reserve", "gdp", "inflation", "interest rates", "ipo", "merger",
        "oil price", "oil prices", "profit", "recession", "revenue", "shares", "stock market", "stocks",
        "tariff", "trade deal", "unemployment",
    ),
}

CATEGORY_SOURCE_HINTS = {
    "Sports": ("espn", "sports"),
    "Entertainment": ("billboard", "hollywood", "rolling stone", "variety"),
    "Technology": ("technology", "techcrunch", "the verge", "wired"),
    "Economy": ("bloomberg", "business", "cnbc", "financial times", "wall street journal"),
}

CATEGORY_TIEBREAK_ORDER = (
    "Conflict",
    "US Politics",
    "Sports",
    "Entertainment",
    "Technology",
    "Economy",
)


def category_term_score(text: str, term: str) -> int:
    return int(bool(re.search(rf"\b{re.escape(term)}\b", text)))


def story_category(story: Story) -> str:
    headline = clean_text(story.title).lower()
    summary = clean_text(story.summary_text).lower()
    source = story.source.lower()
    scores = {}
    for category, terms in CATEGORY_TERMS.items():
        headline_score = sum(category_term_score(headline, term) for term in terms)
        summary_score = sum(category_term_score(summary, term) for term in terms)
        scores[category] = (headline_score * 3) + summary_score
    for category, source_hints in CATEGORY_SOURCE_HINTS.items():
        if any(hint in source for hint in source_hints):
            scores[category] += 3

    best_score = max(scores.values(), default=0)
    if best_score < 2:
        return "World"
    return next(category for category in CATEGORY_TIEBREAK_ORDER if scores[category] == best_score)


def category_css_class(category: str) -> str:
    return f"category-{re.sub(r'[^a-z0-9]+', '-', category.lower()).strip('-')}"


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

            .ai-cost-strip {
                display: grid;
                grid-template-columns: minmax(0, 1fr) auto;
                align-items: center;
                gap: 1rem;
                border-bottom: 1px solid #2f2b25;
                padding: 0 0 0.85rem;
                margin: -0.15rem 0 0.85rem;
            }

            .ai-cost-latest {
                color: var(--skim-muted);
                font-size: 0.82rem;
                line-height: 1.35;
            }

            .ai-cost-latest strong {
                color: var(--skim-ink);
                font-weight: 650;
            }

            .ai-cost-total {
                min-width: 8.2rem;
                text-align: right;
            }

            .ai-cost-total-label {
                color: var(--skim-muted);
                font-size: 0.66rem;
                font-weight: 700;
                line-height: 1.2;
                text-transform: uppercase;
            }

            .ai-cost-total-value {
                color: var(--skim-accent);
                font-size: 1.55rem;
                font-weight: 800;
                line-height: 1.08;
                margin-top: 0.12rem;
            }

            .st-key-headline_feed > [data-testid="stLayoutWrapper"] {
                background: transparent;
                border: 0;
                border-radius: 0;
                box-shadow: none;
                padding: 0;
            }

            .st-key-headline_feed > [data-testid="stLayoutWrapper"] > div[data-testid="stVerticalBlock"] {
                position: relative;
                background: #050607;
                border: 1px solid #343a42;
                border-radius: 5px;
                box-shadow: none;
                padding: 0.72rem 0.78rem 0.72rem 1.2rem;
            }

            .st-key-headline_feed > [data-testid="stLayoutWrapper"]:has(.headline-category)
            > div[data-testid="stVerticalBlock"]::before {
                content: "";
                position: absolute;
                left: 0.48rem;
                top: 0.3rem;
                bottom: 0.32rem;
                width: 3px;
                border-radius: 2px;
                background: var(--skim-category, var(--skim-accent));
                box-shadow: 0 0 8px color-mix(in srgb, var(--skim-category, var(--skim-accent)) 55%, transparent);
            }

            .st-key-headline_feed > [data-testid="stLayoutWrapper"]:has(.story-meta)
            > div[data-testid="stVerticalBlock"]::before {
                top: 0.782rem;
            }

            .st-key-headline_feed > [data-testid="stLayoutWrapper"]:has(.category-world) {
                --skim-category: #39ff14;
            }

            .st-key-headline_feed > [data-testid="stLayoutWrapper"]:has(.category-conflict) {
                --skim-category: #ff4f81;
            }

            .st-key-headline_feed > [data-testid="stLayoutWrapper"]:has(.category-us-politics) {
                --skim-category: #00e5ff;
            }

            .st-key-headline_feed > [data-testid="stLayoutWrapper"]:has(.category-sports) {
                --skim-category: #ccff00;
            }

            .st-key-headline_feed > [data-testid="stLayoutWrapper"]:has(.category-entertainment) {
                --skim-category: #ff7a00;
            }

            .st-key-headline_feed > [data-testid="stLayoutWrapper"]:has(.category-technology) {
                --skim-category: #bb86fc;
            }

            .st-key-headline_feed > [data-testid="stLayoutWrapper"]:has(.category-economy) {
                --skim-category: #ffe600;
            }

            .st-key-headline_feed > [data-testid="stLayoutWrapper"]:has(.compact-headline-kicker) {
                background: transparent;
            }

            .st-key-headline_feed > [data-testid="stLayoutWrapper"]:has(.compact-headline-kicker)
            > div[data-testid="stVerticalBlock"] {
                gap: 0.06rem;
                padding: 0.28rem 0.55rem 0.36rem 1.05rem;
            }

            .st-key-headline_feed > [data-testid="stLayoutWrapper"]:has(.compact-headline-kicker) button {
                justify-content: flex-start !important;
                min-height: 1.72rem;
                height: auto;
                margin: 0;
                padding: 0.08rem 0;
                background: #000000;
                border: 0 !important;
                border-radius: 0;
                box-shadow: none;
                color: #d8d8d8;
                font-size: 1.1664rem;
                font-weight: 550;
                line-height: 1.22;
                text-align: left !important;
                white-space: nowrap;
                overflow: hidden;
                text-overflow: clip;
            }

            .st-key-headline_feed > [data-testid="stLayoutWrapper"]:has(.headline-long) button {
                font-size: 1.15rem;
            }

            .st-key-headline_feed > [data-testid="stLayoutWrapper"]:has(.headline-extra-long) button {
                font-size: 1.02rem;
            }

            .st-key-headline_feed > [data-testid="stLayoutWrapper"]:has(.compact-headline-kicker) button > div,
            .st-key-headline_feed > [data-testid="stLayoutWrapper"]:has(.compact-headline-kicker) button span {
                width: 100%;
                justify-content: flex-start !important;
                text-align: left !important;
            }

            .st-key-headline_feed > [data-testid="stLayoutWrapper"]:has(.compact-headline-kicker) button * {
                font-family: inherit !important;
                font-size: inherit !important;
                font-weight: inherit !important;
                line-height: inherit !important;
            }

            .st-key-headline_feed > [data-testid="stLayoutWrapper"]:has(.compact-headline-kicker) button p {
                display: block !important;
                width: 100% !important;
                margin: 0;
                color: inherit;
                font-family: inherit !important;
                font-size: inherit !important;
                font-weight: inherit !important;
                line-height: inherit !important;
                text-align: left !important;
                white-space: nowrap !important;
                overflow: hidden;
                text-overflow: clip !important;
            }

            .st-key-headline_feed > [data-testid="stLayoutWrapper"]:has(.compact-headline-kicker) button:hover,
            .st-key-headline_feed > [data-testid="stLayoutWrapper"]:has(.compact-headline-kicker) button:focus,
            .st-key-headline_feed > [data-testid="stLayoutWrapper"]:has(.compact-headline-kicker) button:active {
                background: #090a0c;
                border: 0 !important;
                box-shadow: none;
                color: #e3e3e3;
            }

            .headline-category {
                font-weight: 750;
            }

            .headline-category.category-world { color: #39ff14; }
            .headline-category.category-conflict { color: #ff4f81; }
            .headline-category.category-us-politics { color: #00e5ff; }
            .headline-category.category-sports { color: #ccff00; }
            .headline-category.category-entertainment { color: #ff7a00; }
            .headline-category.category-technology { color: #bb86fc; }
            .headline-category.category-economy { color: #ffe600; }

            .category-time {
                font-weight: 700;
            }

            .category-time.category-world { color: #39ff14; }
            .category-time.category-conflict { color: #ff4f81; }
            .category-time.category-us-politics { color: #00e5ff; }
            .category-time.category-sports { color: #ccff00; }
            .category-time.category-entertainment { color: #ff7a00; }
            .category-time.category-technology { color: #bb86fc; }
            .category-time.category-economy { color: #ffe600; }

            .compact-headline-kicker {
                display: flex;
                align-items: center;
                gap: 0.4rem;
                color: var(--skim-muted);
                font-size: 0.68rem;
                font-weight: 700;
                line-height: 1.1;
                margin-bottom: 0;
                text-transform: uppercase;
            }

            .compact-headline-kicker .headline-category {
                font-size: 0.72rem;
            }

            .st-key-headline_feed > [data-testid="stLayoutWrapper"]:has(.compact-headline-kicker)
            [data-testid="stElementContainer"]:has(.compact-headline-kicker) {
                height: auto !important;
                min-height: 0.76rem;
            }

            .compact-headline-meta,
            .compact-headline-time {
                color: #8390a1;
                font-size: 0.7rem;
                line-height: 1.15;
                margin: 0;
            }

            .compact-headline-time {
                font-size: 0.68rem;
                margin-top: 0.02rem;
            }

            .st-key-headline_feed > [data-testid="stLayoutWrapper"]:has(.compact-headline-kicker)
            [data-testid="stElementContainer"]:has(.compact-headline-meta) {
                height: auto !important;
                min-height: 1.58rem;
            }

            .st-key-headline_feed {
                gap: 0.3rem;
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
                font-size: var(--story-title-size, 1.575rem) !important;
                line-height: 1.14 !important;
                margin: 0 0 0.55rem 0 !important;
                color: var(--skim-ink);
                max-width: 34rem;
                display: -webkit-box;
                -webkit-box-orient: vertical;
                -webkit-line-clamp: 2;
                overflow: hidden;
                overflow-wrap: anywhere;
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

            .headline-expand-row {
                margin: -0.1rem 0 0.45rem;
            }

            .headline-brief-divider {
                border-top: 1px solid #332f29;
                margin: 0.9rem 0 0.85rem;
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

            .build-progress {
                color: var(--skim-muted);
                font-size: 0.78rem;
                line-height: 1.35;
                margin: 0.2rem 0 0.75rem;
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

            .skim-footnote a {
                color: var(--skim-accent);
                text-decoration: none;
            }

            .skim-footnote a:hover {
                text-decoration: underline;
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

            @media (max-width: 640px) {
                .ai-cost-strip {
                    align-items: end;
                    gap: 0.6rem;
                }

                .ai-cost-total {
                    min-width: 6.8rem;
                }

                .ai-cost-total-value {
                    font-size: 1.3rem;
                }

                .story-title {
                    font-size: var(--story-title-mobile-size, 1.3rem) !important;
                }

                .st-key-headline_feed > [data-testid="stLayoutWrapper"]:has(.compact-headline-kicker) button {
                    font-size: 1.0584rem;
                    max-height: 3.15rem;
                    white-space: normal !important;
                }

                .st-key-headline_feed > [data-testid="stLayoutWrapper"]:has(.compact-headline-kicker) button p {
                    display: block !important;
                    max-height: 2.44em;
                    white-space: normal !important;
                    overflow: hidden;
                    text-overflow: clip !important;
                }

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
    if re.fullmatch(r"\d{8}T\d{6}Z", value):
        try:
            return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
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


def source_name_from_domain(domain: str) -> str:
    host = domain.lower().strip().split(":", 1)[0].removeprefix("www.")
    for known_domain, source_name in DOMAIN_SOURCE_NAMES.items():
        if host == known_domain or host.endswith(f".{known_domain}"):
            return source_name

    parts = [part for part in host.split(".") if part]
    if not parts:
        return "GDELT publisher"
    country_suffix = len(parts) >= 3 and parts[-2] in {"co", "com", "net", "org"}
    label = parts[-3] if country_suffix else parts[-2] if len(parts) >= 2 else parts[0]
    label = re.sub(r"[-_]+", " ", label)
    label = label.replace("dailynews", "daily news").replace("newsdaily", "news daily")
    return label.title()


def inferred_topics_from_text(text: str) -> tuple[str, ...]:
    haystack = f" {clean_text(text).lower()} "
    matches = [
        topic
        for topic, needles in TOPICS.items()
        if topic not in {"Reddit Hot", "Hacker News"} and any(needle in haystack for needle in needles)
    ]
    return tuple(dict.fromkeys(matches)) or ("World",)


def normalized_story_url(url: str) -> str:
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return url.lower().rstrip("/")
    host = parsed.netloc.lower().removeprefix("www.")
    path = re.sub(r"/+", "/", parsed.path).rstrip("/")
    return f"{host}{path}".lower()


def deduplicate_stories(stories: Sequence[Story]) -> list[Story]:
    deduplicated: list[Story] = []
    seen_urls: set[str] = set()
    seen_titles: set[tuple[str, str]] = set()
    for story in stories:
        url_key = normalized_story_url(story.link)
        title_key = (outlet_identity(story.source), normalized_story_text(story.title))
        if (url_key and url_key in seen_urls) or title_key in seen_titles:
            continue
        deduplicated.append(story)
        if url_key:
            seen_urls.add(url_key)
        seen_titles.add(title_key)
    return deduplicated


def is_google_news_url(url: str) -> bool:
    try:
        return urllib.parse.urlparse(url).netloc.lower() == "news.google.com"
    except ValueError:
        return False


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


def headline_tokens(story: Story) -> set[str]:
    return set(significant_words(clean_headline_source(story.title)))


def cluster_key_from_tokens(tokens: set[str], fallback: str) -> str:
    if not tokens:
        return stable_id("story", fallback, "")
    return "-".join(sorted(tokens)[:10])


def cluster_key_from_stories(cluster: Sequence[Story], representative: Story) -> str:
    if len(cluster) == 1:
        return cluster_key_from_tokens(headline_tokens(representative), representative.title)
    counts = Counter(token for story in cluster for token in headline_tokens(story))
    threshold = max(2, (len(cluster) + 1) // 2)
    shared_tokens = {token for token, count in counts.items() if count >= threshold}
    tokens = shared_tokens or headline_tokens(representative)
    return cluster_key_from_tokens(tokens, representative.title)


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


def parse_source_feed(source: NewsSource, xml_bytes: bytes) -> tuple[list[Story], str | None]:
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        return [], f"{source.name}: could not parse feed ({exc})"

    entries = [node for node in root.iter() if local_name(node.tag) in {"item", "entry"}]
    stories: list[Story] = []
    for entry in entries[: source.item_limit]:
        title = child_text(entry, ("title",))
        link = child_link(entry)
        summary_raw = child_raw_text(entry, ("description", "summary", "content", "encoded"))
        image_html = " ".join(
            child.text or ""
            for child in entry
            if local_name(child.tag) in {"description", "summary", "content", "encoded"}
        )
        publisher = child_text(entry, ("source",))
        drudge_item = source.name == "Drudge Report"
        if drudge_item:
            direct_link = child_text(entry, ("guid",))
            if direct_link.startswith(("https://", "http://")):
                link = direct_link
                direct_host = urllib.parse.urlparse(link).netloc.lower().removeprefix("www.")
                if direct_host in {"archive.is", "archive.ph"}:
                    continue
                publisher_name = source_name_from_domain(direct_host)
                publisher = f"{publisher_name} via Drudge"
        google_news_item = is_google_news_url(link)
        summary = "" if google_news_item or drudge_item else clean_text(summary_raw)
        date_text = child_text(entry, ("pubDate", "published", "updated", "date"))
        if not title or not link:
            continue
        story_source = publisher or source.name
        stories.append(
            Story(
                id=stable_id(story_source, title, link),
                source=story_source,
                group=source.group,
                title=title,
                link=link,
                summary_text=summary,
                published=parse_date(date_text),
                topics=source.topics,
                image_url=child_image(entry, image_html or summary_raw),
            )
        )
    return stories, None


@st.cache_data(ttl=300, show_spinner=False)
def fetch_source(source: NewsSource) -> tuple[list[Story], str | None]:
    request = urllib.request.Request(source.url, headers=REQUEST_HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=FEED_TIMEOUT_SECONDS) as response:
            xml_bytes = response.read()
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        return [], f"{source.name}: {exc}"
    return parse_source_feed(source, xml_bytes)


def parse_gdelt_articles(payload: object) -> list[Story]:
    if not isinstance(payload, dict) or not isinstance(payload.get("articles"), list):
        return []

    stories: list[Story] = []
    for article in payload["articles"]:
        if not isinstance(article, dict):
            continue
        title = clean_text(str(article.get("title", "")))
        link = str(article.get("url", "")).strip()
        language = str(article.get("language", "")).strip().lower()
        if not title or not link.startswith(("http://", "https://")):
            continue
        if language and language != "english":
            continue
        domain = str(article.get("domain", "")).strip()
        source = source_name_from_domain(domain or urllib.parse.urlparse(link).netloc)
        image_url = str(article.get("socialimage", "")).strip()
        stories.append(
            Story(
                id=stable_id(source, title, link),
                source=source,
                group="GDELT",
                title=title,
                link=link,
                summary_text="",
                published=parse_date(str(article.get("seendate", ""))),
                topics=inferred_topics_from_text(title),
                image_url=image_url if image_url.startswith(("http://", "https://")) else None,
            )
        )
    return stories


@st.cache_data(ttl=600, show_spinner=False)
def fetch_gdelt_stories() -> tuple[list[Story], str | None]:
    params = urllib.parse.urlencode(
        {
            "query": GDELT_QUERY,
            "mode": "artlist",
            "format": "json",
            "maxrecords": GDELT_MAX_RECORDS,
            "timespan": "24h",
            "sort": "hybridrel",
        }
    )
    headers = dict(REQUEST_HEADERS)
    headers["Accept"] = "application/json"
    request = urllib.request.Request(f"{GDELT_DOC_API_URL}?{params}", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=GDELT_TIMEOUT_SECONDS) as response:
            raw_payload = response.read(4_000_000).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            return [], "GDELT global discovery is temporarily rate-limited; the RSS feeds are still active."
        return [], f"GDELT global discovery: HTTP {exc.code}; the RSS feeds are still active."
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        return [], f"GDELT global discovery: {exc}"

    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError:
        if "limit requests" in raw_payload.lower():
            return [], "GDELT global discovery is temporarily rate-limited; the RSS feeds are still active."
        return [], "GDELT global discovery returned an unreadable response; the RSS feeds are still active."
    return parse_gdelt_articles(payload), None


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
    include_gdelt: bool = True,
) -> tuple[list[Story], list[str]]:
    stories: list[Story] = []
    errors: list[str] = []
    topic_set = set(selected_topics)
    sources_to_fetch: list[NewsSource] = []

    for source in NEWS_SOURCES:
        if source.group == "Aggregator" and not include_aggregators:
            continue
        if source.group == "Social" and not include_social:
            continue
        if topic_set and not topic_set.intersection(source.topics):
            continue
        sources_to_fetch.append(source)

    sources_to_fetch.extend(keyword_news_source(keyword) for keyword in custom_keywords)
    worker_count = min(MAX_FEED_WORKERS, len(sources_to_fetch))
    if worker_count:
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            for source_stories, error in executor.map(fetch_source, sources_to_fetch):
                stories.extend(source_stories)
                if error:
                    errors.append(error)

    if include_gdelt:
        gdelt_stories, error = fetch_gdelt_stories()
        if topic_set:
            gdelt_stories = [
                story for story in gdelt_stories if topic_set.intersection(story.topics)
            ]
        stories.extend(gdelt_stories)
        if error:
            errors.append(error)

    return deduplicate_stories(stories), errors


def fetch_keyword_rankings(custom_keywords: tuple[str, ...]) -> tuple[dict[str, list[RankedStory]], list[str]]:
    keyword_rankings: dict[str, list[RankedStory]] = {}
    errors: list[str] = []

    for keyword in custom_keywords:
        source_stories, error = fetch_source(keyword_news_source(keyword))
        keyword_rankings[keyword] = rank_stories(
            source_stories,
            (keyword,),
            require_high_signal=False,
        )
        if error:
            errors.append(error)

    return keyword_rankings, errors


def cluster_stories(stories: list[Story]) -> list[list[Story]]:
    clusters: list[list[Story]] = []
    cluster_headlines: list[list[set[str]]] = []

    for story in stories:
        tokens = headline_tokens(story)
        matched_index = None
        best_similarity = 0.0
        for index, existing_headlines in enumerate(cluster_headlines):
            similarity = max(
                headline_event_similarity(tokens, existing_tokens)
                for existing_tokens in existing_headlines
            )
            if similarity > best_similarity and similarity >= 0.34:
                matched_index = index
                best_similarity = similarity

        if matched_index is None:
            clusters.append([story])
            cluster_headlines.append([tokens])
        else:
            clusters[matched_index].append(story)
            cluster_headlines[matched_index].append(tokens)

    return clusters


def headline_event_similarity(left_tokens: set[str], right_tokens: set[str]) -> float:
    if not left_tokens or not right_tokens:
        return 0.0
    shared = left_tokens.intersection(right_tokens)
    if len(shared) < 2:
        return 0.0
    overlap = len(shared) / len(left_tokens.union(right_tokens))
    if len(shared) >= 4:
        return max(overlap, 0.5)
    if len(shared) >= 3:
        return max(overlap, 0.38)
    return overlap


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


def query_param_nonnegative_int(name: str) -> int:
    try:
        return max(0, int(query_param_text(name)))
    except (TypeError, ValueError):
        return 0


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


def initialize_ai_cost_state() -> None:
    if st.session_state.get("ai_cost_state_initialized", False):
        return
    st.session_state.ai_cost_total_micros = query_param_nonnegative_int(AI_COST_QUERY_TOTAL)
    st.session_state.ai_cost_latest_micros = query_param_nonnegative_int(AI_COST_QUERY_LATEST)
    st.session_state.ai_cost_total_articles = query_param_nonnegative_int(AI_COST_QUERY_TOTAL_ARTICLES)
    st.session_state.ai_cost_latest_articles = query_param_nonnegative_int(AI_COST_QUERY_LATEST_ARTICLES)
    st.session_state.ai_cost_last_batch_id = query_param_text(AI_COST_QUERY_LAST_BATCH)
    st.session_state.ai_cost_state_initialized = True


def persist_ai_cost_state() -> None:
    values = {
        AI_COST_QUERY_TOTAL: st.session_state.ai_cost_total_micros,
        AI_COST_QUERY_LATEST: st.session_state.ai_cost_latest_micros,
        AI_COST_QUERY_TOTAL_ARTICLES: st.session_state.ai_cost_total_articles,
        AI_COST_QUERY_LATEST_ARTICLES: st.session_state.ai_cost_latest_articles,
        AI_COST_QUERY_LAST_BATCH: st.session_state.ai_cost_last_batch_id,
    }
    for key, value in values.items():
        st.query_params[key] = str(value)


def complete_story_refresh() -> None:
    fetch_source.clear()
    resolve_article_url.clear()
    fetch_article_evidence.clear()
    st.session_state.current_cluster_keys = []
    st.session_state.last_settings = None
    st.session_state.deep_analyses = {}
    st.session_state.expanded_story_ids = set()


def load_next_story_batch() -> None:
    st.session_state.current_cluster_keys = []
    st.session_state.deep_analyses = {}
    st.session_state.expanded_story_ids = set()


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


def story_age_hours(story: Story, now: datetime | None = None) -> float:
    now = now or datetime.now(timezone.utc)
    if not story.published:
        return 12.0
    published = story.published
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    return max(0.0, (now - published.astimezone(timezone.utc)).total_seconds() / 3600)


def is_major_outlet(story: Story) -> bool:
    source = story.source.lower()
    marker_match = any(
        re.search(rf"\b{re.escape(marker)}\b", source) if len(marker) <= 3 else marker in source
        for marker in MAJOR_OUTLET_MARKERS
    )
    return story.group == "Major News" or marker_match


def outlet_identity(source: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", " ", source.lower()).strip()
    normalized = re.sub(r"\s+via\s+drudge(?:\s+report)?$", "", normalized)
    aliases = (
        (("associated press", "ap news"), "associated press"),
        (("new york times", "nyt"), "new york times"),
        (("deutsche welle", "dw"), "deutsche welle"),
        (("british broadcasting corporation", "bbc"), "bbc"),
    )
    for names, canonical in aliases:
        if any(re.search(rf"\b{re.escape(name)}\b", normalized) for name in names):
            return canonical
    return normalized


def breaking_term_count(story: Story) -> int:
    raw_words = set(re.findall(r"[a-z0-9]+", clean_headline_source(story.title).lower()))
    normalized_words = {normalize_word(word) for word in raw_words}
    breaking_variants = BREAKING_NEWS_TERMS.union(
        normalize_word(term) for term in BREAKING_NEWS_TERMS
    )
    return len(raw_words.union(normalized_words).intersection(breaking_variants))


def cluster_coverage_span_hours(cluster: Sequence[Story]) -> float:
    published_times = []
    for story in cluster:
        if not story.published:
            continue
        published = story.published
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        published_times.append(published.astimezone(timezone.utc))
    if len(published_times) < 2:
        return 0.0
    return max(0.0, (max(published_times) - min(published_times)).total_seconds() / 3600)


def story_score(
    story: Story,
    references: int,
    cluster_size: int,
    keywords: tuple[str, ...] = (),
    coverage_span_hours: float = 0.0,
) -> float:
    now = datetime.now(timezone.utc)
    age_hours = story_age_hours(story, now)
    recency_score = max(0.0, 72.0 - (age_hours * 4.0))
    outlet_score = min(references, 8) * 30.0
    report_score = min(cluster_size, 12) * 5.0
    coverage_velocity = references / max(1.0, coverage_span_hours + 1.0)
    velocity_score = min(45.0, coverage_velocity * 20.0)
    major_outlet_score = 24.0 if is_major_outlet(story) else 0.0
    breaking_score = min(3, breaking_term_count(story)) * 14.0
    social_penalty = 32.0 if story.group == "Social" and references == 1 else 0.0
    aggregator_penalty = 14.0 if story.group == "Aggregator" and references == 1 else 0.0
    keyword_boost = keyword_match_count(story, keywords) * 34.0
    return (
        recency_score
        + outlet_score
        + report_score
        + velocity_score
        + major_outlet_score
        + breaking_score
        + keyword_boost
        - social_penalty
        - aggregator_penalty
    )


def representative_quality(story: Story) -> tuple[int, int, int]:
    direct_publisher_link = int(not is_google_news_url(story.link))
    substantial_feed_text = int(has_enough_reported_material(story.title, story.summary_text))
    source_priority = {
        "Major News": 4,
        "Specialist": 3,
        "Social": 3,
        "Aggregator": 2,
        "GDELT": 2,
        "Custom": 1,
    }.get(story.group, 0)
    return source_priority, direct_publisher_link, substantial_feed_text


def article_candidate_quality(story: Story) -> tuple[int, int, int]:
    """Favor links Skim can read directly before aggregator redirects."""
    direct_publisher_link = int(not is_google_news_url(story.link))
    substantial_feed_text = int(has_enough_reported_material(story.title, story.summary_text))
    source_priority = {
        "Major News": 4,
        "GDELT": 3,
        "Specialist": 3,
        "Aggregator": 2,
        "Social": 1,
        "Custom": 1,
    }.get(story.group, 0)
    return direct_publisher_link, source_priority, substantial_feed_text


def cluster_is_high_signal(cluster: Sequence[Story], references: int) -> bool:
    freshest_age = min(story_age_hours(story) for story in cluster)
    has_major_report = any(is_major_outlet(story) for story in cluster)
    has_breaking_language = any(breaking_term_count(story) for story in cluster)
    if references >= 3 and freshest_age <= POPULAR_COVERAGE_HOURS:
        return True
    if references >= 2 and freshest_age <= FAST_COVERAGE_HOURS:
        return True
    if has_major_report and freshest_age <= 4:
        return True
    return has_major_report and has_breaking_language and freshest_age <= MAJOR_BREAKING_HOURS


def cluster_signal_label(
    cluster: Sequence[Story],
    references: int,
    coverage_span_hours: float,
) -> str:
    freshest_age = min(story_age_hours(story) for story in cluster)
    if references >= 3 and coverage_span_hours <= FAST_COVERAGE_HOURS:
        return "Fast-rising"
    if references >= 2:
        return "Widely covered"
    if freshest_age <= MAJOR_BREAKING_HOURS and any(breaking_term_count(story) for story in cluster):
        return "Breaking"
    return "Major outlet"


def rank_stories(
    stories: list[Story],
    keywords: tuple[str, ...] = (),
    require_high_signal: bool = True,
) -> list[RankedStory]:
    ranked: list[RankedStory] = []
    for cluster in cluster_stories(stories):
        sources = {outlet_identity(story.source) for story in cluster}
        references = len(sources)
        if require_high_signal and not cluster_is_high_signal(cluster, references):
            continue
        coverage_span_hours = cluster_coverage_span_hours(cluster)
        representative = max(
            cluster,
            key=lambda story: (
                representative_quality(story),
                story_score(
                    story,
                    references=references,
                    cluster_size=len(cluster),
                    keywords=keywords,
                    coverage_span_hours=coverage_span_hours,
                ),
            ),
        )
        article_candidates: list[Story] = []
        seen_candidate_links: set[str] = set()
        for candidate in sorted(
            cluster,
            key=lambda story: (
                article_candidate_quality(story),
                story_score(
                    story,
                    references=references,
                    cluster_size=len(cluster),
                    keywords=keywords,
                    coverage_span_hours=coverage_span_hours,
                ),
            ),
            reverse=True,
        ):
            normalized_link = candidate.link.split("#", 1)[0]
            if normalized_link in seen_candidate_links:
                continue
            seen_candidate_links.add(normalized_link)
            article_candidates.append(candidate)
            if len(article_candidates) >= MAX_ARTICLE_CANDIDATES:
                break
        outlet_names: list[str] = []
        seen_outlets: set[str] = set()
        for clustered_story in sorted(
            cluster,
            key=lambda item: (item != representative, not is_major_outlet(item), item.source.lower()),
        ):
            identity = outlet_identity(clustered_story.source)
            if identity in seen_outlets:
                continue
            seen_outlets.add(identity)
            outlet_names.append(clustered_story.source)
        ranked.append(
            RankedStory(
                story=representative,
                cluster_key=cluster_key_from_stories(cluster, representative),
                references=references,
                topic_story_count=len(cluster),
                score=story_score(
                    representative,
                    references=references,
                    cluster_size=len(cluster),
                    keywords=keywords,
                    coverage_span_hours=coverage_span_hours,
                ),
                coverage_span_hours=coverage_span_hours,
                signal_label=cluster_signal_label(cluster, references, coverage_span_hours),
                outlets=tuple(outlet_names),
                article_candidates=tuple(article_candidates),
            )
        )

    ranked.sort(key=lambda item: item.score, reverse=True)
    return ranked


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


ARTICLE_BOILERPLATE_MARKERS = (
    "sign up for",
    "sign up to",
    "subscribe to",
    "subscribe for",
    "newsletter",
    "email address",
    "privacy policy",
    "cookie policy",
    "accept cookies",
    "all rights reserved",
    "follow us on",
    "share this article",
    "full story",
    "read more:",
    "related article",
    "advertisement",
)


def article_line_is_boilerplate(line: str) -> bool:
    normalized = clean_text(line).lower()
    if not normalized:
        return True
    if any(marker in normalized for marker in ARTICLE_BOILERPLATE_MARKERS):
        return True
    return len(normalized.split()) < 4


def sanitize_article_text(raw_text: str, max_words: int = ARTICLE_MAX_WORDS) -> str:
    kept_lines: list[str] = []
    seen_lines: set[str] = set()
    raw_lines = [line for line in raw_text.splitlines() if clean_text(line)]
    if len(raw_lines) <= 1:
        raw_lines = split_sentences(raw_text)
    for raw_line in raw_lines:
        line = clean_text(raw_line)
        if article_line_is_boilerplate(line):
            continue
        normalized = normalized_story_text(line)
        if normalized in seen_lines:
            continue
        seen_lines.add(normalized)
        kept_lines.append(line)

    combined = " ".join(kept_lines)
    sentences = split_sentences(combined)
    selected: list[str] = []
    selected_words = 0
    for sentence in sentences:
        sentence_words = len(sentence.split())
        if selected and selected_words + sentence_words > max_words:
            break
        selected.append(sentence)
        selected_words += sentence_words
    return " ".join(selected)


def extract_json_ld_article_body(page_html: str) -> str:
    bodies: list[str] = []

    def visit(value: object) -> None:
        if isinstance(value, dict):
            body = value.get("articleBody")
            if isinstance(body, str):
                bodies.append(body)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    scripts = re.findall(
        r'(?is)<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        page_html,
    )
    for script in scripts:
        try:
            visit(json.loads(html.unescape(script).strip()))
        except (json.JSONDecodeError, TypeError):
            continue
    return max(bodies, key=len, default="")


def extract_html_paragraph_candidates(page_html: str) -> tuple[tuple[str, int], ...]:
    try:
        from lxml import html as lxml_html
    except ImportError:
        return ()

    try:
        document = lxml_html.fromstring(page_html)
    except (ValueError, TypeError):
        return ()

    for blocked in document.xpath("//script|//style|//nav|//footer|//aside|//form|//noscript"):
        blocked.drop_tree()

    candidates: list[tuple[str, int]] = []
    for xpath, priority in (("//article//p", 4), ("//main//p", 3), ("//p", 1)):
        paragraphs = []
        for paragraph in document.xpath(xpath):
            text = clean_text(" ".join(paragraph.itertext()))
            if len(text.split()) >= 5:
                paragraphs.append(text)
        if paragraphs:
            candidates.append(("\n".join(paragraphs), priority))
    return tuple(candidates)


def article_extraction_score(
    text: str,
    expected_title: str,
    source_priority: int,
) -> tuple[int, int, int, int]:
    word_count = len(text.split())
    sentences = sentence_count(text)
    title_terms = set(significant_words(expected_title))
    body_terms = set(significant_words(text))
    overlap = len(title_terms.intersection(body_terms))
    sufficient = int(
        word_count >= MIN_ARTICLE_WORDS
        and sentences >= MIN_ARTICLE_SENTENCES
        and (not title_terms or overlap >= 1)
    )
    return sufficient, source_priority, overlap, min(word_count, ARTICLE_MAX_WORDS)


def extract_main_article_text(
    page_html: str,
    article_url: str,
    expected_title: str = "",
) -> str:
    from trafilatura import extract

    candidates: list[tuple[str, int]] = []
    precision_text = extract(
        page_html,
        url=article_url,
        output_format="txt",
        include_comments=False,
        include_tables=False,
        favor_precision=True,
        deduplicate=True,
    ) or ""
    candidates.append((precision_text, 5))

    json_ld_body = extract_json_ld_article_body(page_html)
    if json_ld_body:
        candidates.append((json_ld_body, 5))

    candidates.extend(extract_html_paragraph_candidates(page_html))

    if len(precision_text.split()) < MIN_ARTICLE_WORDS:
        recall_text = extract(
            page_html,
            url=article_url,
            output_format="txt",
            include_comments=False,
            include_tables=False,
            favor_recall=True,
            deduplicate=True,
        ) or ""
        candidates.append((recall_text, 2))

    sanitized_candidates = [
        (sanitize_article_text(candidate), priority)
        for candidate, priority in candidates
        if candidate
    ]
    if not sanitized_candidates:
        return ""
    return max(
        sanitized_candidates,
        key=lambda candidate: article_extraction_score(
            candidate[0],
            expected_title,
            candidate[1],
        ),
    )[0]


@st.cache_data(ttl=300, show_spinner=False)
def resolve_article_url(url: str) -> str:
    if not is_google_news_url(url):
        return url
    try:
        from googlenewsdecoder import gnewsdecoder

        result = gnewsdecoder(url)
    except Exception:
        return ""
    if isinstance(result, dict) and result.get("status"):
        decoded_url = str(result.get("decoded_url", "")).strip()
        if decoded_url.startswith(("https://", "http://")):
            return decoded_url
    return ""


@st.cache_data(ttl=300, show_spinner=False)
def fetch_article_evidence(url: str, expected_title: str) -> ArticleEvidence | None:
    article_url = resolve_article_url(url)
    if not article_url.startswith(("https://", "http://")):
        return None

    request = urllib.request.Request(article_url, headers=REQUEST_HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=ARTICLE_TIMEOUT_SECONDS) as response:
            content_type = response.headers.get("Content-Type", "").lower()
            if content_type and "html" not in content_type:
                return None
            charset = response.headers.get_content_charset() or "utf-8"
            page_bytes = response.read(ARTICLE_MAX_BYTES)
            final_url = response.geturl()
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None

    try:
        page_html = page_bytes.decode(charset, errors="ignore")
    except LookupError:
        page_html = page_bytes.decode("utf-8", errors="ignore")
    try:
        article_text = extract_main_article_text(page_html, final_url, expected_title)
    except (ImportError, ValueError, TypeError, RuntimeError):
        return None

    evidence = ArticleEvidence(
        url=final_url,
        title=clean_headline_source(expected_title),
        text=article_text,
        word_count=len(article_text.split()),
    )
    return evidence if article_evidence_is_sufficient(evidence) else None


def article_evidence_is_sufficient(evidence: ArticleEvidence | None) -> bool:
    if not evidence or evidence.word_count < MIN_ARTICLE_WORDS:
        return False
    if sentence_count(evidence.text) < MIN_ARTICLE_SENTENCES:
        return False

    title_terms = set(significant_words(evidence.title))
    body_terms = set(significant_words(evidence.text))
    required_overlap = min(1, len(title_terms))
    return required_overlap == 0 or len(title_terms.intersection(body_terms)) >= required_overlap


def story_haystack(story: Story) -> str:
    return f" {story.title} {story.summary_text} ".lower()


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
    return labels.get(configured_ai_provider(), "OpenAI key needed")


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


def openai_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float | None:
    prices = OPENAI_MODEL_PRICES_PER_MTOK.get(model)
    if not prices:
        return None
    input_price, cached_input_price, output_price = prices
    cached_tokens = min(max(0, cached_input_tokens), max(0, input_tokens))
    cache_write = min(max(0, cache_write_tokens), max(0, input_tokens - cached_tokens))
    uncached_tokens = max(0, input_tokens - cached_tokens - cache_write)
    return (
        (uncached_tokens / 1_000_000) * input_price
        + (cached_tokens / 1_000_000) * cached_input_price
        + (cache_write / 1_000_000) * input_price * 1.25
        + (max(0, output_tokens) / 1_000_000) * output_price
    )


def format_cost(value: float) -> str:
    if value <= 0:
        return "$0.00"
    if value < 0.01:
        return f"${value:.4f}"
    return f"${value:.2f}"


def result_openai_cost(result: object, model: str) -> float | None:
    if not isinstance(result, dict):
        return None
    try:
        input_tokens = int(result.get("__usage_input_tokens", 0))
        output_tokens = int(result.get("__usage_output_tokens", 0))
        cached_input_tokens = int(result.get("__usage_cached_input_tokens", 0))
        cache_write_tokens = int(result.get("__usage_cache_write_tokens", 0))
    except (TypeError, ValueError):
        return None
    if input_tokens <= 0 and output_tokens <= 0:
        return None
    return openai_cost(
        model,
        input_tokens,
        output_tokens,
        cached_input_tokens,
        cache_write_tokens,
    )


def card_ai_cost(card: object) -> float:
    if not isinstance(card, dict):
        return 0.0
    try:
        return max(0.0, float(card.get("__ai_cost", 0)))
    except (TypeError, ValueError):
        return 0.0


def openai_cost_note(story: Story, article_text: str, card: dict[str, str]) -> str:
    if configured_ai_provider() != "openai":
        return ""

    summary_model = ai_model("openai", deep=False)
    summary_cost = card_ai_cost(card)
    if summary_cost <= 0:
        summary_input_tokens = estimated_token_count(
            story.title,
            story.summary_text,
            article_text,
            overhead_tokens=850,
        )
        summary_cost = openai_cost(summary_model, summary_input_tokens, 1_500)
    if summary_cost is None:
        return ""

    deep_model = ai_model("openai", deep=True)
    deep_input_tokens = estimated_token_count(
        story.title,
        story.summary_text,
        article_text,
        overhead_tokens=520,
    )
    deep_output_tokens = 1_500
    deep_cost = openai_cost(deep_model, deep_input_tokens, deep_output_tokens)
    deep_note = f" · deep if clicked ~{format_cost(deep_cost)}" if deep_cost is not None else ""
    return f"AI cost: this card ~{format_cost(summary_cost)}{deep_note}"


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


SUMMARY_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "summary": {"type": "string"},
        "background": {"type": "string"},
    },
    "required": ["headline", "summary", "background"],
    "additionalProperties": False,
}

DEEP_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "analysis": {"type": "string"},
        "watch_next": {"type": "string"},
        "research": {"type": "string"},
    },
    "required": ["analysis", "watch_next", "research"],
    "additionalProperties": False,
}


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


def openai_json(
    model: str,
    instructions: str,
    prompt: str,
    effort: str,
    max_output_tokens: int,
    schema_name: str,
    schema: dict,
) -> dict:
    from openai import OpenAI

    client = OpenAI(api_key=openai_api_key())
    response = client.responses.create(
        model=model,
        instructions=instructions,
        input=prompt,
        reasoning={"effort": effort},
        text={
            "format": {
                "type": "json_schema",
                "name": schema_name,
                "strict": True,
                "schema": schema,
            },
            "verbosity": "medium",
        },
        max_output_tokens=max_output_tokens,
    )
    result = parse_openai_json(getattr(response, "output_text", ""))
    usage = getattr(response, "usage", None)
    input_details = getattr(usage, "input_tokens_details", None)
    if usage and isinstance(result, dict):
        result.update(
            {
                "__usage_input_tokens": str(max(0, int(getattr(usage, "input_tokens", 0) or 0))),
                "__usage_output_tokens": str(max(0, int(getattr(usage, "output_tokens", 0) or 0))),
                "__usage_cached_input_tokens": str(
                    max(0, int(getattr(input_details, "cached_tokens", 0) or 0))
                ),
                "__usage_cache_write_tokens": str(
                    max(
                        0,
                        int(
                            getattr(input_details, "cache_write_tokens", 0)
                            or getattr(usage, "cache_write_tokens", 0)
                            or 0
                        ),
                    )
                ),
            }
        )
    return result


def ai_json(
    provider: str,
    model: str,
    instructions: str,
    prompt: str,
    effort: str,
    max_output_tokens: int,
    schema_name: str,
    schema: dict,
) -> dict:
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
    return openai_json(model, instructions, prompt, effort, max_output_tokens, schema_name, schema)


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
    rss_summary: str,
    article_url: str,
    article_text: str,
    topics: tuple[str, ...],
    detail: int,
) -> dict:
    prompt = textwrap.dedent(
        f"""
        PUBLISHER: {source}
        SOURCE TYPE: {group}
        TOPICS: {", ".join(topics)}
        PUBLISHER HEADLINE: {clean_headline_source(title)}
        RSS DESCRIPTION: {clean_text(rss_summary) or "Not available."}
        PUBLISHER URL: {article_url}
        DESIRED DETAIL: {detail}/5

        <ARTICLE_BODY>
        {article_text}
        </ARTICLE_BODY>
        """
    ).strip()
    instructions = """
    You are the editor of Skim. Read the supplied publisher article body closely before
    writing. The current event facts must come from that body. You may use reliable
    general knowledge only to explain established background or cautious implications,
    never to add unreported current-event facts.

    Return the required JSON fields:
    - headline: 5-10 words and no more than 78 characters. State the central development as a complete, natural thought.
      Keep names, places, and stakes that make it meaningful. No ellipses, label, teaser,
      clickbait, dangling preposition, or abrupt truncation.
    - summary: 3-4 cohesive sentences and 65-150 words. Explain who did what, where and
      when relevant, the strongest specifics or numbers, and the immediate consequence.
      Synthesize the body instead of copying its opening. Every sentence must add a fact
      or a concrete implication.
    - background: 2-3 cohesive sentences and 45-125 words. Explain the specific backstory,
      institutional setting, historical pressure, or connected event that makes this
      development significant. End with a disciplined assessment of what it could change
      or what concrete development to watch. Mark uncertain consequences with may, could,
      or would.

    Write only publishable news prose. Never refer to "the article," "this article,"
    "the story," "this story," "the headline," a feed, coverage, reporting mechanics,
    reading more, newsletters, or what the reader should click. Never discuss missing
    information. Never use generic filler about public trust, legitimacy, leverage,
    systems, pressure, or a wider struggle unless you identify the exact institution,
    actor, and mechanism involved here. Do not repeat the summary in background.
    """
    return ai_json(
        provider,
        model,
        instructions,
        prompt,
        effort="high",
        max_output_tokens=3500,
        schema_name="skim_story_card",
        schema=SUMMARY_RESPONSE_SCHEMA,
    )


@st.cache_data(ttl=86400, show_spinner=False)
def ai_summary_repair_cached(
    provider: str,
    model: str,
    prompt_version: str,
    refresh_key: str,
    story_id: str,
    title: str,
    source: str,
    article_text: str,
    draft_json: str,
    quality_errors: tuple[str, ...],
) -> dict:
    prompt = textwrap.dedent(
        f"""
        PUBLISHER: {source}
        PUBLISHER HEADLINE: {clean_headline_source(title)}

        <ARTICLE_BODY>
        {article_text}
        </ARTICLE_BODY>

        <REJECTED_DRAFT>
        {draft_json}
        </REJECTED_DRAFT>

        QUALITY FAILURES:
        {"; ".join(quality_errors)}
        """
    ).strip()
    instructions = """
    Rewrite the rejected Skim card so every listed quality failure is fixed. Ground all
    current facts in ARTICLE_BODY. Return only the required JSON fields. The headline is
    5-10 words, no more than 78 characters, and a complete thought. The summary is 3-4 cohesive sentences and 65-150
    words. The background is 2-3 specific sentences and 45-125 words. Do not mention an
    article, story, headline, feed, coverage, newsletter, missing details, reading, or
    clicking. Remove promotional fragments and generic analysis. Do not repeat sentences.
    """
    return ai_json(
        provider,
        model,
        instructions,
        prompt,
        effort="high",
        max_output_tokens=3000,
        schema_name="skim_story_card_repair",
        schema=SUMMARY_RESPONSE_SCHEMA,
    )


@st.cache_data(ttl=86400, show_spinner=False)
def ai_deep_analysis_cached(
    provider: str,
    model: str,
    story_id: str,
    title: str,
    source: str,
    group: str,
    article_url: str,
    article_text: str,
    topics: tuple[str, ...],
) -> dict:
    prompt = textwrap.dedent(
        f"""
        PUBLISHER: {source}
        SOURCE TYPE: {group}
        TOPICS: {", ".join(topics)}
        PUBLISHER HEADLINE: {clean_headline_source(title)}
        PUBLISHER URL: {article_url}

        <ARTICLE_BODY>
        {article_text}
        </ARTICLE_BODY>
        """
    ).strip()
    instructions = """
    You are Terra inside Skim: an intellectually serious but readable news analyst.
    Read the complete supplied publisher text. Ground current facts in it and clearly
    mark inference. Return valid JSON with analysis, watch_next, and research. analysis
    is 4-6 sentences explaining the deeper stakes, relevant historical or institutional
    context, actors with decision-making power, plausible reactions, and connected events.
    watch_next is one sentence naming a concrete signal that would materially change the
    assessment. research is one sentence naming the most useful subject to understand
    next. Never refer to an article, story, headline, feed, coverage, or reading process.
    """
    return ai_json(
        provider,
        model,
        instructions,
        prompt,
        effort="high",
        max_output_tokens=3500,
        schema_name="skim_deep_analysis",
        schema=DEEP_RESPONSE_SCHEMA,
    )


def learning_links_text(links: tuple[tuple[str, str], ...]) -> str:
    return " ".join(f"[{label}]({url})" for label, url in links)


def strip_markdown_links(text: str) -> str:
    return re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r"\1", text)


FORBIDDEN_CARD_PATTERNS = (
    r"\b(?:the|this|that) article\b",
    r"\b(?:the|this|that) story\b",
    r"\b(?:the|this|that) headline\b",
    r"\bfull (?:article|story)\b",
    r"\bnews feed\b",
    r"\bmultiple outlets\b",
    r"\bfeed did not provide\b",
    r"\bread (?:the|more)\b",
    r"\bclick (?:here|through)\b",
    r"\bsign up\b",
    r"\bnewsletter\b",
    r"\bmissing (?:details|information|context)\b",
)

GENERIC_ANALYSIS_MARKERS = (
    "machinery of escalation",
    "wider struggle over power, legitimacy, and public trust",
    "business story with consequences beyond one company or sector",
    "technology story about control",
    "stress test for the health system",
    "the bigger value is understanding",
    "important enough to watch",
)

ABRUPT_HEADLINE_ENDINGS = {
    "a", "an", "and", "as", "at", "because", "before", "by", "for", "from",
    "in", "of", "on", "or", "over", "the", "to", "under", "with",
}


def prose_has_forbidden_language(text: str) -> bool:
    normalized = clean_text(text).lower()
    return any(re.search(pattern, normalized) for pattern in FORBIDDEN_CARD_PATTERNS)


def prose_is_complete(text: str) -> bool:
    cleaned = clean_text(text)
    return bool(cleaned) and cleaned[-1] in ".!?"


def sentence_similarity(left: str, right: str) -> float:
    left_terms = set(significant_words(left))
    right_terms = set(significant_words(right))
    if not left_terms or not right_terms:
        return 0.0
    return len(left_terms.intersection(right_terms)) / len(left_terms.union(right_terms))


def card_quality_errors(card: dict[str, str], story: Story) -> tuple[str, ...]:
    headline_text = clean_text(card.get("headline", ""))
    summary_text = clean_text(card.get("summary", ""))
    background_text = clean_text(card.get("background", ""))
    errors: list[str] = []

    headline_words = headline_text.split()
    if not 5 <= len(headline_words) <= 10:
        errors.append("headline must contain 5-10 words")
    if len(headline_text) > 78:
        errors.append("headline must contain no more than 78 characters")
    if "..." in headline_text or "…" in headline_text:
        errors.append("headline contains an ellipsis")
    if headline_words and headline_words[-1].lower().strip(".,:;!?") in ABRUPT_HEADLINE_ENDINGS:
        errors.append("headline ends abruptly")

    summary_words = len(summary_text.split())
    summary_sentences = split_sentences(summary_text)
    if not 3 <= len(summary_sentences) <= 4:
        errors.append("summary must contain 3-4 complete sentences")
    if not 65 <= summary_words <= 150:
        errors.append("summary must contain 65-150 words")
    if not prose_is_complete(summary_text):
        errors.append("summary ends with an incomplete sentence")

    background_words = len(background_text.split())
    background_sentences = split_sentences(background_text)
    if not 2 <= len(background_sentences) <= 3:
        errors.append("background must contain 2-3 complete sentences")
    if not 45 <= background_words <= 125:
        errors.append("background must contain 45-125 words")
    if not prose_is_complete(background_text):
        errors.append("background ends with an incomplete sentence")

    combined = f"{headline_text} {summary_text} {background_text}"
    if prose_has_forbidden_language(combined):
        errors.append("card contains meta or promotional language")
    if any(marker in combined.lower() for marker in GENERIC_ANALYSIS_MARKERS):
        errors.append("background contains generic stock analysis")

    title_terms = set(significant_words(clean_headline_source(story.title)))
    summary_terms = set(significant_words(summary_text))
    required_overlap = min(2, len(title_terms))
    if required_overlap and len(title_terms.intersection(summary_terms)) < required_overlap:
        errors.append("summary is not clearly tied to the central subject")
    if title_terms and not title_terms.intersection(significant_words(background_text)):
        errors.append("background is not clearly tied to the central subject")

    all_sentences = [*summary_sentences, *background_sentences]
    for index, sentence in enumerate(all_sentences):
        for other in all_sentences[index + 1 :]:
            if sentence_similarity(sentence, other) >= 0.78:
                errors.append("card repeats substantially the same sentence")
                return tuple(dict.fromkeys(errors))
    return tuple(dict.fromkeys(errors))


def normalize_ai_card(raw_result: object) -> dict[str, str]:
    if not isinstance(raw_result, dict):
        return {"headline": "", "summary": "", "background": ""}
    return {
        "headline": clean_text(strip_markdown_links(str(raw_result.get("headline", "")))),
        "summary": clean_text(strip_markdown_links(str(raw_result.get("summary", "")))),
        "background": clean_text(strip_markdown_links(str(raw_result.get("background", "")))),
    }


def ai_failure_message(provider: str, model: str, exc: Exception) -> str:
    raw_message = clean_text(str(exc))
    safe_message = re.sub(r"sk-[A-Za-z0-9_-]+", "[redacted]", raw_message)
    normalized = safe_message.lower()
    provider_name = "OpenAI" if provider == "openai" else provider.title()

    if any(term in normalized for term in ("incorrect api key", "invalid api key", "authentication")):
        return f"{provider_name} rejected the configured API key. Check the app's Secrets entry."
    if any(term in normalized for term in ("insufficient_quota", "insufficient quota", "billing", "credit")):
        return f"{provider_name} cannot generate summaries because the API account has no available credit."
    if "rate limit" in normalized or "rate_limit" in normalized:
        return f"{provider_name} is temporarily rate-limiting summary requests. Try again shortly."
    if "model" in normalized and any(term in normalized for term in ("not found", "does not exist", "not available")):
        return f"{provider_name} cannot use the configured model ({model}). Check the model setting in Secrets."
    detail = safe_message[:220] or exc.__class__.__name__
    return f"{provider_name} could not generate a summary ({detail})."


def record_generation_issue(message: str) -> None:
    try:
        issues = list(st.session_state.get("generation_issues", []))
        if message not in issues:
            issues.append(message)
        st.session_state.generation_issues = issues[-4:]
    except Exception:
        return


def smart_summarize(
    story: Story,
    evidence: ArticleEvidence,
    detail: int,
    refresh_key: str,
) -> SummaryAttempt:
    provider = configured_ai_provider()
    if not provider:
        return SummaryAttempt(card=None, ai_cost=0.0)

    topics = infer_topics(story)
    fallback_links = story_learning_links(story, topics)
    model = ai_model(provider, deep=False)
    rss_summary = sanitize_article_text(story.summary_text, max_words=180)
    if not has_enough_reported_material(story.title, rss_summary):
        rss_summary = ""
    try:
        ai_result = ai_summary_cached(
            provider,
            model,
            AI_SUMMARY_PROMPT_VERSION,
            refresh_key,
            story.id,
            story.title,
            story.source,
            story.group,
            rss_summary,
            evidence.url,
            evidence.text,
            topics,
            detail,
        )
    except Exception as exc:
        record_generation_issue(ai_failure_message(provider, model, exc))
        return SummaryAttempt(card=None, ai_cost=0.0)

    ai_cost = 0.0
    if provider == "openai":
        ai_cost = result_openai_cost(ai_result, model) or 0.0
        if ai_cost <= 0:
            estimated_input = estimated_token_count(
                story.title,
                rss_summary,
                evidence.text,
                overhead_tokens=850,
            )
            ai_cost = openai_cost(model, estimated_input, 1_500) or 0.0

    card = normalize_ai_card(ai_result)
    errors = card_quality_errors(card, story)
    if errors:
        try:
            repaired = ai_summary_repair_cached(
                provider,
                model,
                AI_SUMMARY_PROMPT_VERSION,
                refresh_key,
                story.id,
                story.title,
                story.source,
                evidence.text,
                json.dumps(card, ensure_ascii=True, sort_keys=True),
                errors,
            )
        except Exception as exc:
            record_generation_issue(ai_failure_message(provider, model, exc))
            return SummaryAttempt(card=None, ai_cost=ai_cost)
        if provider == "openai":
            repair_cost = result_openai_cost(repaired, model)
            if repair_cost is None:
                estimated_repair_input = estimated_token_count(
                    story.title,
                    evidence.text,
                    json.dumps(card, ensure_ascii=True, sort_keys=True),
                    "; ".join(errors),
                    overhead_tokens=650,
                )
                repair_cost = openai_cost(model, estimated_repair_input, 1_500)
            ai_cost += repair_cost or 0.0
        card = normalize_ai_card(repaired)
        if card_quality_errors(card, story):
            return SummaryAttempt(card=None, ai_cost=ai_cost)

    return SummaryAttempt(
        card={
            "__headline": card["headline"],
            "__ai_cost": f"{ai_cost:.8f}",
            "": card["summary"],
            "Background": card["background"],
            "Learn More": f"Learn more: {learning_links_text(fallback_links)}",
        },
        ai_cost=ai_cost,
    )


def deeper_analysis(story: Story, evidence: ArticleEvidence) -> dict[str, str]:
    topics = infer_topics(story)
    fallback_links = story_learning_links(story, topics)
    provider = configured_ai_provider()
    if not provider:
        return {
            "Deeper analysis": "Add OPENAI_API_KEY in Streamlit secrets to enable deeper analysis.",
            "Research trail": f"Learn more: {learning_links_text(fallback_links)}",
        }

    ai_result = ai_deep_analysis_cached(
        provider,
        ai_model(provider, deep=True),
        story.id,
        story.title,
        story.source,
        story.group,
        evidence.url,
        evidence.text,
        topics,
    )
    analysis = clean_text(strip_markdown_links(str(ai_result.get("analysis", ""))))
    watch_next = clean_text(strip_markdown_links(str(ai_result.get("watch_next", ""))))
    research = clean_text(strip_markdown_links(str(ai_result.get("research", ""))))
    if not analysis or prose_has_forbidden_language(f"{analysis} {watch_next} {research}"):
        raise ValueError("The generated analysis did not pass Skim's quality checks.")

    result = {"Deeper analysis": analysis}
    if watch_next:
        result["Watch next"] = watch_next
    research_intro = f"{research} " if research else ""
    result["Research trail"] = f"{research_intro}Learn more: {learning_links_text(fallback_links)}"
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


def share_sms_url(story: Story, article_url: str, display_headline: str) -> str:
    body = urllib.parse.quote(f"{display_headline or clean_headline_source(story.title)} {article_url}")
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


def coverage_outlet_text(ranked_story: RankedStory) -> str:
    names = list(ranked_story.outlets[:3])
    if not names:
        outlet_word = "outlet" if ranked_story.references == 1 else "outlets"
        return f"{ranked_story.references} {outlet_word}"
    remaining = max(0, ranked_story.references - len(names))
    label = ", ".join(names)
    if remaining:
        outlet_word = "outlet" if remaining == 1 else "outlets"
        label += f" · {remaining} more {outlet_word}"
    return label


def expanded_headline_font_sizes(display_headline: str, has_image: bool) -> tuple[float, float]:
    headline_length = max(1, len(clean_text(display_headline)))
    desktop_capacity = 62 if has_image else 92
    desktop_size = max(1.0, min(1.575, 1.575 * desktop_capacity / headline_length))
    mobile_size = max(0.9, min(1.3, 1.3 * 58 / headline_length))
    return desktop_size, mobile_size


def render_story_header(
    ranked_story: RankedStory,
    display_headline: str,
    compact: bool = False,
    headline_button_key: str | None = None,
) -> bool:
    story = ranked_story.story
    category = story_category(story)
    category_class = category_css_class(category)
    report_word = "report" if ranked_story.topic_story_count == 1 else "reports"
    if compact:
        signal = html.escape(ranked_story.signal_label or "Top story")
        category_label = html.escape(category)
        headline_length_class = (
            "headline-extra-long"
            if len(display_headline) > 105
            else "headline-long"
            if len(display_headline) > 95
            else ""
        )
        st.markdown(
            f'<div class="compact-headline-kicker {category_class} {headline_length_class}">'
            f'<span class="headline-category {category_class}">{category_label}</span>'
            f'<span>{signal}</span>'
            '</div>',
            unsafe_allow_html=True,
        )
        pressed = st.button(
            display_headline,
            key=headline_button_key,
            use_container_width=True,
        )
        st.markdown(
            f'<div class="compact-headline-meta">'
            f"{html.escape(coverage_outlet_text(ranked_story))} · "
            f"{ranked_story.topic_story_count} {report_word} in this cluster"
            f'</div>'
            f'<div class="compact-headline-time category-time {category_class}">'
            f"{html.escape(story_age(story))}"
            f'</div>',
            unsafe_allow_html=True,
        )
        return pressed

    meta = (
        f'<span class="headline-category {category_class}">{html.escape(category)}</span> / '
        f"{html.escape(coverage_outlet_text(ranked_story))} / "
        f"{ranked_story.topic_story_count} {report_word} in this cluster / "
        f'<span class="category-time {category_class}">{html.escape(story_age(story))}</span>'
    )
    st.markdown(
        f'<div class="story-meta">{meta}</div>',
        unsafe_allow_html=True,
    )

    story_title_text = html.escape(display_headline)
    desktop_size, mobile_size = expanded_headline_font_sizes(
        display_headline,
        bool(story.image_url),
    )
    title_style = (
        f"--story-title-size:{desktop_size:.3f}rem;"
        f"--story-title-mobile-size:{mobile_size:.3f}rem"
    )
    if story.image_url:
        story_title = f'<h2 class="story-title" style="{title_style}">{story_title_text}</h2>'
        title_col, image_col = st.columns([3, 1], vertical_alignment="top")
        with title_col:
            st.markdown(story_title, unsafe_allow_html=True)
        with image_col:
            image_url = html.escape(story.image_url, quote=True)
            st.markdown(f'<img class="story-image" src="{image_url}" alt="">', unsafe_allow_html=True)
    else:
        story_title = (
            f'<h2 class="story-title story-title-full" style="{title_style}">'
            f"{story_title_text}</h2>"
        )
        st.markdown(story_title, unsafe_allow_html=True)
    return False


def render_story_details(prepared_story: PreparedStory) -> None:
    ranked_story = prepared_story.ranked_story
    story = ranked_story.story
    article_story = prepared_story.article_story or story
    evidence = prepared_story.evidence
    summary = dict(prepared_story.card)
    archived = story.id in st.session_state.archived
    display_headline = summary.pop("__headline")

    rows = ""
    for label, value in summary.items():
        if label.startswith("__"):
            continue
        label_html = "" if label == "Learn More" else (f"<b>{html.escape(label)}:</b> " if label else "")
        rows += f'<div class="summary-field">{label_html}{render_summary_value(value)}</div>'
    st.markdown(f'<div class="summary-grid">{rows}</div>', unsafe_allow_html=True)

    col1, col2, col3, col4 = st.columns([1, 1, 1, 1], gap="small", vertical_alignment="top")
    with col1:
        st.link_button("Full story", evidence.url, use_container_width=True)
    with col2:
        label = "Archived" if archived else "Archive"
        if st.button(label, key=f"archive-{story.id}", icon=":material/bookmark:", use_container_width=True):
            if archived:
                st.session_state.archived.remove(story.id)
            else:
                st.session_state.archived.add(story.id)
            st.rerun()
    with col3:
        st.link_button(
            "Share",
            share_sms_url(story, evidence.url, display_headline),
            use_container_width=True,
        )
    with col4:
        if st.button("Deep analysis", key=f"deep-{story.id}", use_container_width=True):
            with st.spinner("Building the deeper read..."):
                try:
                    st.session_state.deep_analyses[story.id] = deeper_analysis(story, evidence)
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

    source = f"Source: {article_story.source}"
    st.markdown(f'<div class="story-source">{html.escape(source)}</div>', unsafe_allow_html=True)


def render_story(prepared_story: PreparedStory) -> None:
    with st.container(border=True):
        display_headline = str(prepared_story.card.get("__headline", ""))
        render_story_header(prepared_story.ranked_story, display_headline)
        render_story_details(prepared_story)


def render_headline_story(ranked_story: RankedStory, detail: int) -> None:
    story = ranked_story.story
    expanded_story_ids = set(st.session_state.expanded_story_ids)
    is_expanded = story.id in expanded_story_ids
    with st.container(border=True):
        prepared = None
        attempted_cost = 0.0
        if is_expanded:
            summary_key = f"headline-{st.session_state.batch_refresh_id}-{story.id}"
            with st.spinner("Reading the publisher article and building this brief..."):
                prepared, attempted_cost = prepare_ranked_story(ranked_story, detail, summary_key)
            if prepared:
                record_batch_ai_cost([prepared], summary_key, attempted_cost)
            expanded_headline = (
                str(prepared.card.get("__headline", ""))
                if prepared
                else clean_headline_source(story.title)
            )
            render_story_header(ranked_story, expanded_headline)
            action_pressed = st.button("Close brief", key=f"expand-{story.id}", icon=":material/expand_less:")
        else:
            action_pressed = render_story_header(
                ranked_story,
                clean_headline_source(story.title),
                compact=True,
                headline_button_key=f"expand-{story.id}",
            )

        if action_pressed:
            if is_expanded:
                expanded_story_ids.discard(story.id)
            else:
                expanded_story_ids.add(story.id)
            st.session_state.expanded_story_ids = expanded_story_ids
            st.rerun()

        if not is_expanded:
            return

        st.markdown('<div class="headline-brief-divider"></div>', unsafe_allow_html=True)
        if prepared:
            render_story_details(prepared)
            return

        if not configured_ai_provider():
            st.info("Add OPENAI_API_KEY in Streamlit secrets to generate this brief.")
        elif st.session_state.generation_issues:
            st.error("Skim could not generate this brief. Open Feed notes below for the exact reason.")
        else:
            st.warning(
                "Skim could not access enough reporting from the available publisher pages "
                "to build a grounded brief."
            )


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


def render_ai_cost_summary(target: object) -> None:
    latest_micros = int(st.session_state.get("ai_cost_latest_micros", 0))
    total_micros = int(st.session_state.get("ai_cost_total_micros", 0))
    latest_articles = int(st.session_state.get("ai_cost_latest_articles", 0))
    total_articles = int(st.session_state.get("ai_cost_total_articles", 0))
    if total_micros:
        article_word = "article" if latest_articles == 1 else "articles"
        latest_text = (
            f"<strong>Latest feed:</strong> {latest_articles} {article_word} "
            f"cost about {format_cost(latest_micros / AI_COST_SCALE)}. "
            f"Tracking {total_articles} generated cards since this counter started."
        )
    else:
        latest_text = (
            "<strong>AI cost tracking is ready.</strong> The counter starts with the next "
            "OpenAI-generated feed."
        )
    target.markdown(
        f"""
        <div class="ai-cost-strip">
            <div class="ai-cost-latest">{latest_text}</div>
            <div class="ai-cost-total">
                <div class="ai-cost-total-label">Estimated all-time AI cost</div>
                <div class="ai-cost-total-value">{format_cost(total_micros / AI_COST_SCALE)}</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def settings_signature(
    selected_topics: list[str],
    include_aggregators: bool,
    include_gdelt: bool,
    include_social: bool,
    show_archived: bool,
    keywords: tuple[str, ...],
) -> tuple:
    return (
        tuple(selected_topics),
        include_aggregators,
        include_gdelt,
        include_social,
        show_archived,
        keywords,
    )


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


def mark_batch_shown(batch: Sequence[RankedStory], refresh_key: str) -> None:
    if not batch:
        return
    now = utc_now().isoformat()
    history = dict(st.session_state.get("shown_cluster_history", {}))
    for item in batch:
        history[item.cluster_key] = now
    st.session_state.shown_cluster_history = history
    st.session_state.batch_refreshed_at = now
    st.session_state.batch_refresh_id = refresh_key


def accumulate_ai_cost(
    total_micros: int,
    last_batch_id: str,
    refresh_key: str,
    batch_cost: float,
) -> tuple[int, bool]:
    if not refresh_key or refresh_key == last_batch_id or batch_cost <= 0:
        return total_micros, False
    return total_micros + round(batch_cost * AI_COST_SCALE), True


def record_batch_ai_cost(
    batch: Sequence[PreparedStory],
    refresh_key: str,
    attempted_ai_cost: float,
) -> None:
    if configured_ai_provider() != "openai" or not batch:
        return
    batch_cost = max(0.0, attempted_ai_cost)
    total_micros, changed = accumulate_ai_cost(
        int(st.session_state.ai_cost_total_micros),
        str(st.session_state.ai_cost_last_batch_id),
        refresh_key,
        batch_cost,
    )
    if not changed:
        return
    latest_micros = round(batch_cost * AI_COST_SCALE)
    st.session_state.ai_cost_total_micros = total_micros
    st.session_state.ai_cost_latest_micros = latest_micros
    st.session_state.ai_cost_total_articles += len(batch)
    st.session_state.ai_cost_latest_articles = len(batch)
    st.session_state.ai_cost_last_batch_id = refresh_key
    persist_ai_cost_state()


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
    headline_word = "headline" if batch_size == 1 else "headlines"
    st.caption(f"{batch_size} {headline_word} refreshed: {label} · No repeats for {NO_REPEAT_HOURS} hours")


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


def prepare_ranked_story(
    item: RankedStory,
    detail: int,
    refresh_key: str,
) -> tuple[PreparedStory | None, float]:
    candidates = item.article_candidates or (item.story,)
    for candidate in candidates[:MAX_ARTICLE_CANDIDATES]:
        evidence = fetch_article_evidence(candidate.link, candidate.title)
        if not evidence:
            continue
        attempt = smart_summarize(item.story, evidence, detail, refresh_key)
        if not attempt.card:
            return None, attempt.ai_cost
        return (
            PreparedStory(
                ranked_story=item,
                evidence=evidence,
                card=attempt.card,
                article_story=candidate,
            ),
            attempt.ai_cost,
        )
    return None, 0.0


def append_prepared_story(
    batch: list[PreparedStory],
    prepared: PreparedStory,
    on_story: Callable[[PreparedStory, int], None] | None,
) -> None:
    batch.append(prepared)
    if on_story:
        on_story(prepared, len(batch))


def build_publishable_batch(
    ranked_stories: list[RankedStory],
    keyword_rankings: dict[str, list[RankedStory]],
    show_archived: bool,
    detail: int,
    on_story: Callable[[PreparedStory, int], None] | None = None,
) -> list[PreparedStory]:
    if not configured_ai_provider():
        return []

    prune_shown_cluster_history()
    shown_cluster_keys = set(st.session_state.shown_cluster_history)
    current = current_batch_from_keys(ranked_stories, keyword_rankings, show_archived)
    if current:
        refresh_key = str(st.session_state.get("batch_refresh_id", "")) or utc_now().isoformat()
        restored: list[PreparedStory] = []
        restored_ai_cost = 0.0
        for item in current:
            prepared, attempt_cost = prepare_ranked_story(item, detail, refresh_key)
            restored_ai_cost += attempt_cost
            if prepared:
                restored.append(prepared)
        if len(restored) == len(current):
            record_batch_ai_cost(restored, refresh_key, restored_ai_cost)
            if on_story:
                for index, prepared in enumerate(restored, start=1):
                    on_story(prepared, index)
            return restored
        st.session_state.current_cluster_keys = []

    refresh_key = utc_now().isoformat()
    batch: list[PreparedStory] = []
    used_cluster_keys: set[str] = set()
    attempted_ai_cost = 0.0

    for item in ranked_stories[:MAX_BASE_CANDIDATES]:
        if len(batch) >= BATCH_SIZE:
            break
        if ranked_item_is_available(item, show_archived, shown_cluster_keys | used_cluster_keys):
            prepared, attempt_cost = prepare_ranked_story(item, detail, refresh_key)
            attempted_ai_cost += attempt_cost
            if prepared:
                append_prepared_story(batch, prepared, on_story)
                used_cluster_keys.add(item.cluster_key)

    for keyword in keyword_rankings:
        for item in keyword_rankings[keyword][:MAX_KEYWORD_CANDIDATES]:
            if ranked_item_is_available(item, show_archived, shown_cluster_keys | used_cluster_keys):
                prepared, attempt_cost = prepare_ranked_story(item, detail, refresh_key)
                attempted_ai_cost += attempt_cost
                if prepared:
                    append_prepared_story(batch, prepared, on_story)
                    used_cluster_keys.add(item.cluster_key)
                    break

    ranked_batch = [prepared.ranked_story for prepared in batch]
    st.session_state.current_cluster_keys = [item.cluster_key for item in ranked_batch]
    mark_batch_shown(ranked_batch, refresh_key)
    record_batch_ai_cost(batch, refresh_key, attempted_ai_cost)
    return batch


def build_headline_batch(
    ranked_stories: list[RankedStory],
    show_archived: bool,
) -> list[RankedStory]:
    prune_shown_cluster_history()
    current = current_batch_from_keys(ranked_stories, {}, show_archived)
    if current:
        return current

    shown_cluster_keys = set(st.session_state.shown_cluster_history)
    batch: list[RankedStory] = []
    used_cluster_keys: set[str] = set()
    for item in ranked_stories:
        if len(batch) >= BATCH_SIZE:
            break
        if ranked_item_is_available(item, show_archived, shown_cluster_keys | used_cluster_keys):
            batch.append(item)
            used_cluster_keys.add(item.cluster_key)

    refresh_key = utc_now().isoformat()
    st.session_state.current_cluster_keys = [item.cluster_key for item in batch]
    mark_batch_shown(batch, refresh_key)
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
    if "expanded_story_ids" not in st.session_state:
        st.session_state.expanded_story_ids = set()
    if "generation_issues" not in st.session_state:
        st.session_state.generation_issues = []
    if "shown_cluster_history" not in st.session_state:
        legacy_seen = st.session_state.get("seen_cluster_keys", set())
        now = utc_now().isoformat()
        st.session_state.shown_cluster_history = {cluster_key: now for cluster_key in legacy_seen}
    if "batch_refresh_id" not in st.session_state:
        st.session_state.batch_refresh_id = ""
    if "batch_refreshed_at" not in st.session_state:
        st.session_state.batch_refreshed_at = ""
    st.session_state.setdefault(
        "selected_topics",
        ["World", "US", "Politics", "Business", "Tech", "Climate", "Health"],
    )
    st.session_state.setdefault("detail", 3)
    st.session_state.setdefault("include_social", False)
    st.session_state.setdefault("include_aggregators", True)
    st.session_state.setdefault("include_gdelt", False)
    st.session_state.setdefault("show_archived", False)
    initialize_keyword_state()
    initialize_ai_cost_state()

    render_header()

    batch_timestamp_slot = st.empty()
    story_stream = st.container(key="headline_feed")

    selected_topics = st.session_state.selected_topics
    detail = st.session_state.detail
    include_social = st.session_state.include_social
    include_aggregators = st.session_state.include_aggregators
    include_gdelt = st.session_state.include_gdelt
    show_archived = st.session_state.show_archived
    keywords = custom_keywords()

    current_settings = settings_signature(
        selected_topics,
        include_aggregators,
        include_gdelt,
        include_social,
        show_archived,
        keywords,
    )
    if st.session_state.last_settings != current_settings:
        st.session_state.current_cluster_keys = []
        st.session_state.last_settings = current_settings

    with st.spinner("Building a stronger story list..."):
        stories, errors = fetch_stories(
            tuple(selected_topics),
            include_aggregators,
            include_social,
            keywords,
            include_gdelt,
        )
        ranked_stories = rank_stories(stories, keywords)

    batch = build_headline_batch(ranked_stories, show_archived)
    st.session_state.generation_issues = []
    with story_stream:
        for ranked_story in batch:
            render_headline_story(ranked_story, detail)
        if batch and st.button(
            "Load 20 more",
            key="load-more-headlines",
            icon=":material/add:",
            help="Show the next unseen 20 headlines from the current discovery pool.",
            use_container_width=True,
        ):
            load_next_story_batch()
            st.rerun()

    with batch_timestamp_slot.container():
        render_batch_timestamp(len(batch))

    errors.extend(st.session_state.generation_issues)
    if not batch:
        st.info(
            f"No new headlines are available under the {NO_REPEAT_HOURS}-hour no-repeat rule. "
            "Open Customize to broaden topics or clear the history."
        )
    st.divider()

    col1, col2 = st.columns(2)
    col1.metric("Headlines", len(batch))
    col2.metric("Archived", len(st.session_state.archived))

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
            st.toggle(
                "News aggregators",
                key="include_aggregators",
                help="Include Google News and the Drudge Report discovery feed.",
            )
            st.toggle(
                "GDELT global discovery",
                key="include_gdelt",
                help="Optional free global discovery. It can be slower when the public API is busy.",
            )
            st.toggle("Show archived stories", key="show_archived")
            if st.button("Clear 24-hour history", use_container_width=True):
                st.session_state.shown_cluster_history = {}
                st.session_state.current_cluster_keys = []
                st.rerun()
        st.markdown("Keyword boosters")
        st.caption("Saved keywords influence which headlines rise into the 20-item list.")
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
            "The main briefing now favors independent outlet confirmation, fast coverage growth, "
            "and fresh consequential reporting from major newsrooms."
        )

    if st.button(
        "Refresh latest stories",
        icon=":material/sync:",
        help="Repoll every live source and build a new briefing without repeating the last 24 hours.",
        use_container_width=True,
    ):
        complete_story_refresh()
        st.rerun()

    st.markdown(
        """
        <p class="skim-footnote">
            Skim uses public RSS feeds and the
            <a href="https://www.gdeltproject.org/" target="_blank" rel="noopener noreferrer">GDELT Project</a>
            to find stories, reads the publisher article, and uses OpenAI for the headline,
            summary, and Background. Cards without enough source text or a clean grounded result
            are left out.
        </p>
        """,
        unsafe_allow_html=True,
    )
    render_ai_cost_summary(st)


if __name__ == "__main__":
    main()
