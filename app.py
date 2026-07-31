from __future__ import annotations

import hashlib
import html
import json
import os
import re
import textwrap
import threading
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, Iterable, Sequence

import streamlit as st
import streamlit.components.v1 as components


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
MIN_FEED_EVIDENCE_WORDS = 35
MIN_FEED_EVIDENCE_SENTENCES = 2
MAX_ARTICLE_CANDIDATES = 12
MAX_BRIEFING_SEARCH_RESULTS = 8
MAX_BRIEFING_SEARCH_CANDIDATES = 12
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
AI_SUMMARY_PROMPT_VERSION = "grounded-article-v4-multi-source-retrieval"
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
AI_COST_QUERY_EVENTS = "aiCostEvents"
AI_COST_QUERY_UPDATED = "aiCostUpdated"
AI_COST_QUERY_HISTORY = "aiCostHistory"
AI_COST_MAX_RECORDED_EVENTS = 128
AI_COST_HISTORY_LIMIT = 10
AI_COST_LEDGER_PATH = Path(__file__).with_name(".skim_ai_cost_ledger.json")
AI_COST_BROWSER_STORAGE_KEY = "skim-ai-cost-ledger-v2"
AI_COST_LEDGER_LOCK = threading.Lock()
ADMIN_PASSWORD = "0102"
EASTERN_STANDARD_TIME = timezone(timedelta(hours=-5), "EST")
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
                --skim-section-gap: 0.22rem;
            }

            *,
            *::before,
            *::after {
                box-sizing: border-box;
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

            .headline-legend {
                width: 100%;
                margin: 0 0 0.72rem;
            }

            .headline-updated {
                color: #8d96a3;
                font-size: 0.68rem;
                line-height: 1.2;
                margin-bottom: 0.42rem;
                text-align: right;
            }

            .category-legend {
                display: flex;
                flex-wrap: wrap;
                gap: 0.3rem;
                width: 100%;
            }

            .category-legend-pill {
                display: inline-flex;
                align-items: center;
                min-height: 1.35rem;
                border-radius: 999px;
                background: var(--legend-color);
                box-shadow: 0 0 8px color-mix(in srgb, var(--legend-color) 45%, transparent);
                color: #000000;
                font-size: 0.62rem;
                font-weight: 850;
                line-height: 1;
                padding: 0.22rem 0.5rem;
                text-transform: uppercase;
                white-space: nowrap;
            }

            .ai-cost-strip {
                display: grid;
                grid-template-columns: minmax(0, 1fr) auto;
                align-items: center;
                gap: 1rem;
                border-bottom: 1px solid #2f2b25;
                padding: 0 0 0.85rem;
                margin: 0 0 0.85rem;
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
            > div[data-testid="stVerticalBlock"] {
                gap: 0.28rem !important;
                padding-top: 0.4rem;
                padding-bottom: 0.45rem;
            }

            .st-key-headline_feed > [data-testid="stLayoutWrapper"]:has(.story-meta)
            > div[data-testid="stVerticalBlock"]::before {
                top: 0.4rem;
                bottom: 0.45rem;
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

            .compact-headline-meta {
                white-space: nowrap;
                overflow: hidden;
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
                flex-wrap: nowrap;
                gap: 0.45rem;
                color: var(--skim-muted);
                font-size: 0.72rem;
                text-transform: uppercase;
                letter-spacing: 0;
                margin: 0 0 0.42rem;
                white-space: nowrap;
                overflow: hidden;
            }

            .story-meta > span {
                flex: 0 0 auto;
            }

            .story-meta .story-meta-outlets {
                flex: 0 1 auto;
                min-width: 0;
                overflow: hidden;
                text-overflow: clip;
            }

            .story-title {
                font-size: var(--story-title-size, 1.575rem) !important;
                line-height: 1.14 !important;
                margin: 0 !important;
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
                margin-bottom: 0.15rem !important;
            }

            .expanded-story-header {
                display: grid;
                grid-template-columns: minmax(0, 3fr) minmax(5.5rem, 1fr);
                align-items: start;
                gap: 0.75rem;
                width: 100%;
            }

            .story-image {
                display: block;
                width: 100%;
                aspect-ratio: 4 / 3;
                max-height: 7.5rem;
                object-fit: cover;
                border: 0;
                border-radius: 12px;
            }

            .headline-brief-divider {
                border-top: 1px solid #332f29;
                margin: 0.22rem 0 0.32rem;
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
                margin: 0;
                max-width: 100%;
                overflow: hidden;
                width: 100%;
            }

            .summary-grid b {
                color: var(--skim-ink);
            }

            .st-key-headline_feed [class*="st-key-summary_section_"] {
                margin: 0 0 var(--skim-section-gap);
                max-width: 100%;
                width: 100%;
            }

            .st-key-headline_feed [class*="st-key-summary_section_"]
            > div[data-testid="stVerticalBlock"] {
                gap: 0 !important;
            }

            .summary-field {
                border-top: 1px solid #332f29;
                min-width: 0;
                overflow-wrap: anywhere;
                padding-top: 0.62rem;
            }

            .summary-field:first-child {
                border-top: 0;
                padding-top: 0;
            }

            .st-key-headline_feed [class*="st-key-deep_analysis_"] {
                color: #ebe5da;
                font-size: 0.95rem;
                line-height: 1.5;
                background: #171512;
                border: 1px solid #373229;
                border-radius: 8px;
                box-sizing: border-box;
                padding: 0.85rem 0.9rem;
                margin: 0 0 var(--skim-section-gap);
                max-width: 100%;
                width: 100%;
                overflow: hidden;
            }

            .st-key-headline_feed [class*="st-key-deep_analysis_"]
            > div[data-testid="stVerticalBlock"] {
                gap: 0 !important;
            }

            .st-key-headline_feed [class*="st-key-deep_analysis_"]
            [data-testid="stElementContainer"] {
                height: auto !important;
                min-height: 0 !important;
            }

            .ai-working-box {
                display: flex;
                align-items: center;
                gap: 0.72rem;
                min-height: 3.25rem;
                background: #080909;
                border: 1px solid color-mix(
                    in srgb,
                    var(--skim-category, var(--skim-accent)) 58%,
                    #30353a
                );
                border-radius: 7px;
                box-shadow: 0 0 16px color-mix(
                    in srgb,
                    var(--skim-category, var(--skim-accent)) 22%,
                    transparent
                );
                color: #eeeae3;
                margin: 0;
                padding: 0.62rem 0.75rem;
            }

            .ai-working-icon {
                position: relative;
                flex: 0 0 1.75rem;
                width: 1.75rem;
                height: 1.75rem;
                border: 1px solid var(--skim-category, var(--skim-accent));
                border-radius: 50%;
                box-shadow: 0 0 10px color-mix(
                    in srgb,
                    var(--skim-category, var(--skim-accent)) 46%,
                    transparent
                );
                animation: skim-working-orbit 1.5s linear infinite;
            }

            .ai-working-icon span {
                position: absolute;
                inset: 0;
                display: grid;
                place-items: center;
                font-size: 0.92rem;
                line-height: 1;
            }

            .ai-working-newspaper {
                animation: skim-working-newspaper 2.4s ease-in-out infinite;
            }

            .ai-working-lightbulb {
                animation: skim-working-lightbulb 2.4s ease-in-out infinite;
            }

            .ai-working-copy {
                font-size: 0.88rem;
                font-weight: 600;
                line-height: 1.35;
            }

            @keyframes skim-working-orbit {
                to { transform: rotate(360deg); }
            }

            @keyframes skim-working-newspaper {
                0%, 42% { opacity: 1; transform: scale(1); }
                50%, 92% { opacity: 0; transform: scale(0.55); }
                100% { opacity: 1; transform: scale(1); }
            }

            @keyframes skim-working-lightbulb {
                0%, 42% { opacity: 0; transform: scale(0.55); }
                50%, 92% { opacity: 1; transform: scale(1); }
                100% { opacity: 0; transform: scale(0.55); }
            }

            .deep-summary-field {
                border-top: 1px solid #332f29;
                padding-top: 0.62rem;
                margin-top: 0.62rem;
                overflow-wrap: anywhere;
            }

            .deep-summary-field-first {
                border-top: 0;
                padding-top: 0;
                margin-top: 0;
            }

            .deep-summary-field b {
                color: var(--skim-ink);
            }

            .deep-learn-more {
                padding-bottom: 0.2rem;
            }

            .deep-learn-more .learn-more-row {
                margin-top: 0;
                max-width: 100%;
            }

            .st-key-headline_feed [class*="st-key-research_row_"] {
                border-top: 1px solid #332f29;
                display: block !important;
                line-height: 1.5;
                margin-top: 0.62rem;
                padding-top: 0.62rem;
            }

            .st-key-headline_feed [class*="st-key-research_row_"]
            [data-testid="stElementContainer"],
            .st-key-headline_feed [class*="st-key-research_row_"] .stMarkdown,
            .st-key-headline_feed [class*="st-key-research_row_"] [data-testid="stMarkdownContainer"],
            .st-key-headline_feed [class*="st-key-research_row_"] p {
                display: inline !important;
                width: auto !important;
                margin: 0 !important;
            }

            .research-trail-copy {
                overflow-wrap: anywhere;
            }

            .st-key-headline_feed [class*="st-key-research_row_"] .stButton {
                display: inline !important;
                width: auto !important;
            }

            .st-key-headline_feed [class*="st-key-research_row_"]
            [data-testid="stBaseButton-tertiary"],
            .st-key-headline_feed [class*="st-key-research_row_"]
            [data-testid="stBaseButton-tertiary"]:visited {
                background: transparent !important;
                border: 0 !important;
                border-radius: 0;
                box-shadow: none !important;
                color: var(--skim-category, var(--skim-accent)) !important;
                display: inline !important;
                height: auto;
                min-height: 0;
                padding: 0 !important;
                font-size: inherit;
                font-style: italic;
                font-weight: 500;
                line-height: inherit;
                margin-left: 0.18em;
                text-decoration: none !important;
                vertical-align: baseline;
                width: auto;
            }

            .st-key-headline_feed [class*="st-key-research_row_"]
            [data-testid="stBaseButton-tertiary"] *,
            .st-key-headline_feed [class*="st-key-research_row_"]
            [data-testid="stBaseButton-tertiary"]:hover,
            .st-key-headline_feed [class*="st-key-research_row_"]
            [data-testid="stBaseButton-tertiary"]:focus {
                color: var(--skim-category, var(--skim-accent)) !important;
                font-size: inherit !important;
                font-style: italic !important;
                line-height: inherit !important;
                text-decoration: none !important;
            }

            .st-key-headline_feed [class*="st-key-research_row_"]
            [data-testid="stBaseButton-tertiary"]:hover {
                text-shadow: 0 0 9px color-mix(
                    in srgb,
                    var(--skim-category, var(--skim-accent)) 55%,
                    transparent
                );
            }

            .lesson-link {
                display: inline-flex;
                align-items: center;
                border: 1px solid color-mix(
                    in srgb,
                    var(--skim-category, var(--skim-accent)) 72%,
                    #383838
                );
                border-radius: 999px;
                background: #030303;
                color: var(--skim-category, var(--skim-accent)) !important;
                padding: 0.1rem 0.38rem;
                margin: 0.08rem 0.12rem 0.08rem 0;
                font-size: 0.72rem;
                line-height: 1.15;
                max-width: 100%;
                overflow-wrap: anywhere;
                text-align: center;
                text-decoration: none !important;
                white-space: normal;
                box-shadow: 0 0 7px color-mix(
                    in srgb,
                    var(--skim-category, var(--skim-accent)) 28%,
                    transparent
                );
            }

            .learn-more-row {
                display: flex;
                align-items: center;
                flex-wrap: wrap;
                gap: 0.16rem;
                margin-top: 0.46rem;
                max-width: 100%;
                min-width: 0;
            }

            .learn-more-label {
                color: var(--skim-muted);
                font-size: 0.72rem;
                font-weight: 700;
                margin-right: 0.18rem;
                text-transform: uppercase;
            }

            .lesson-link:hover {
                border-color: var(--skim-category, var(--skim-accent));
                background: #090909;
                color: var(--skim-category, var(--skim-accent)) !important;
                text-decoration: none !important;
            }

            .st-key-headline_feed [class*="st-key-story_actions_"]
            [data-testid="stHorizontalBlock"] {
                display: grid !important;
                grid-template-columns: repeat(3, minmax(0, 1fr));
                gap: 0.34rem !important;
                margin-top: 0;
            }

            .st-key-headline_feed [class*="st-key-story_actions_"] {
                margin: 0 0 var(--skim-section-gap);
                max-width: 100%;
                width: 100%;
            }

            .st-key-headline_feed [class*="st-key-story_actions_"]
            [data-testid="stHorizontalBlock"]
            > [data-testid="stColumn"] {
                width: 100% !important;
                min-width: 0 !important;
                flex: none !important;
            }

            .st-key-headline_feed [class*="st-key-story_actions_"]
            [data-testid="stHorizontalBlock"]
            .stButton > button,
            .st-key-headline_feed [class*="st-key-story_actions_"]
            [data-testid="stHorizontalBlock"]
            .stLinkButton > a {
                min-height: 1.92rem;
                height: 1.92rem;
                padding: 0.18rem 0.35rem;
                font-size: 0.72rem;
                box-shadow: 0 0 5px color-mix(
                    in srgb,
                    var(--skim-category, var(--skim-accent)) 28%,
                    transparent
                ) !important;
            }

            .st-key-headline_feed [class*="st-key-close_brief_"] {
                margin: 0.08rem 0 0;
            }

            .st-key-headline_feed [class*="st-key-close_brief_"] button {
                background: var(--skim-category, var(--skim-accent)) !important;
                border-color: var(--skim-category, var(--skim-accent)) !important;
                box-shadow: 0 0 5px color-mix(
                    in srgb,
                    var(--skim-category, var(--skim-accent)) 42%,
                    transparent
                ) !important;
                color: #000000 !important;
                font-weight: 800;
                width: 100%;
            }

            .st-key-headline_feed [class*="st-key-close_brief_"] button:hover,
            .st-key-headline_feed [class*="st-key-close_brief_"] button:focus {
                filter: brightness(1.08);
                color: #000000 !important;
            }

            .st-key-headline_feed [class*="st-key-story_questions_"] {
                background: #0b0c0d;
                border: 1px solid #30353a;
                border-radius: 8px;
                margin: 0 0 var(--skim-section-gap);
                padding: 0.7rem 0.75rem 0.1rem;
            }

            .story-question-heading {
                color: var(--skim-category, var(--skim-accent));
                font-size: 0.72rem;
                font-weight: 750;
                margin-bottom: 0.42rem;
                text-transform: uppercase;
            }

            .story-question {
                color: #f0ede7;
                font-size: 0.82rem;
                font-weight: 700;
                margin: 0.3rem 0 0.16rem;
            }

            .story-answer {
                color: #cfcac1;
                border-bottom: 1px solid #292d31;
                font-size: 0.86rem;
                line-height: 1.46;
                margin-bottom: 0.55rem;
                padding-bottom: 0.55rem;
            }

            .st-key-headline_feed [class*="st-key-story_questions_"] input {
                min-height: 2.8rem !important;
                background: #020303;
                border-color: color-mix(
                    in srgb,
                    var(--skim-category, var(--skim-accent)) 58%,
                    #454545
                );
                color: #f0f0f0;
            }

            .st-key-headline_feed [class*="st-key-story_questions_"] [data-testid="stForm"] {
                border: 0;
                padding: 0;
            }

            .st-key-headline_feed [class*="st-key-story_questions_"]
            [data-testid="stFormSubmitButton"] {
                display: none !important;
            }

            .st-key-headline_feed > [data-testid="stLayoutWrapper"]:has(.st-key-load-more-headlines) {
                margin: 0.3rem 0 1.5rem;
            }

            .st-key-load-more-headlines button {
                min-height: 2.6rem;
                height: 2.6rem;
                border: 2px solid transparent !important;
                background:
                    linear-gradient(#020303, #020303) padding-box,
                    linear-gradient(
                        90deg,
                        #39ff14,
                        #ff4f81,
                        #00e5ff,
                        #ccff00,
                        #ff7a00,
                        #bb86fc,
                        #ffe600
                    ) border-box !important;
                box-shadow:
                    0 0 9px rgba(57, 255, 20, 0.22),
                    0 0 13px rgba(0, 229, 255, 0.18),
                    0 0 17px rgba(255, 79, 129, 0.16) !important;
                color: #f4f4f4 !important;
                font-size: 0.82rem;
                font-weight: 750;
            }

            .st-key-load-more-headlines button:hover {
                background:
                    linear-gradient(#0b0c0d, #0b0c0d) padding-box,
                    linear-gradient(
                        90deg,
                        #39ff14,
                        #ff4f81,
                        #00e5ff,
                        #ccff00,
                        #ff7a00,
                        #bb86fc,
                        #ffe600
                    ) border-box !important;
                color: #ffffff !important;
                filter: brightness(1.1);
            }

            .admin-lock-copy {
                color: var(--skim-muted);
                font-size: 0.82rem;
                margin-bottom: 0.55rem;
            }

            [class*="st-key-keyword_slot_"] {
                min-height: 2.3rem;
                border: 1px solid #353a42;
                border-radius: 6px;
                background: #050607;
                box-sizing: border-box;
                padding: 0.18rem 0.24rem;
            }

            [class*="st-key-keyword_slot_"]:has(.keyword-chip-text) {
                --keyword-color: #39ff14;
                border-color: color-mix(in srgb, var(--keyword-color) 72%, #353a42);
                background: color-mix(in srgb, var(--keyword-color) 10%, #050607);
                box-shadow: 0 0 9px color-mix(in srgb, var(--keyword-color) 24%, transparent);
            }

            [class*="st-key-keyword_slot_"]:has(.keyword-color-1),
            [class*="st-key-keyword_slot_"]:has(.keyword-color-8) { --keyword-color: #ff4f81; }
            [class*="st-key-keyword_slot_"]:has(.keyword-color-2) { --keyword-color: #00e5ff; }
            [class*="st-key-keyword_slot_"]:has(.keyword-color-3) { --keyword-color: #ccff00; }
            [class*="st-key-keyword_slot_"]:has(.keyword-color-4) { --keyword-color: #ff7a00; }
            [class*="st-key-keyword_slot_"]:has(.keyword-color-5) { --keyword-color: #bb86fc; }
            [class*="st-key-keyword_slot_"]:has(.keyword-color-6) { --keyword-color: #ffe600; }
            [class*="st-key-keyword_slot_"]:has(.keyword-color-7) { --keyword-color: #39ff14; }

            [class*="st-key-keyword_slot_"] > div[data-testid="stVerticalBlock"] {
                gap: 0 !important;
            }

            [class*="st-key-keyword_slot_"] input {
                min-height: 1.85rem !important;
                height: 1.85rem !important;
                border: 0 !important;
                background: transparent !important;
                font-size: 0.72rem !important;
            }

            .keyword-chip-text {
                color: var(--keyword-color);
                font-size: 0.72rem;
                font-weight: 750;
                line-height: 1.2;
                overflow: hidden;
                padding-left: 0.22rem;
                text-overflow: ellipsis;
                white-space: nowrap;
            }

            [class*="st-key-keyword_slot_"] [class*="st-key-clear_keyword_"] button {
                width: 1.55rem;
                min-width: 1.55rem;
                height: 1.55rem;
                min-height: 1.55rem;
                border: 0;
                background: transparent;
                box-shadow: none;
                color: var(--keyword-color);
                font-size: 1rem;
                padding: 0;
            }

            [class*="st-key-keyword_slot_"] [data-testid="stHorizontalBlock"] {
                align-items: center;
                gap: 0.12rem !important;
            }

            .ai-call-table-wrap {
                width: 100%;
                overflow-x: auto;
                margin: 0.25rem 0 0.85rem;
            }

            .ai-call-table {
                width: 100%;
                border-collapse: collapse;
                color: #d9d4cb;
                font-size: 0.72rem;
            }

            .ai-call-table th {
                color: #8f98a5;
                font-size: 0.62rem;
                font-weight: 750;
                text-align: left;
                text-transform: uppercase;
            }

            .ai-call-table th,
            .ai-call-table td {
                border-bottom: 1px solid #292d31;
                padding: 0.38rem 0.42rem;
                white-space: nowrap;
            }

            .ai-call-table th:last-child,
            .ai-call-table td:last-child {
                color: var(--skim-accent);
                text-align: right;
            }

            .story-ai-cost {
                color: #8f887e;
                font-size: 0.7rem;
                line-height: 1.3;
                margin-top: 0;
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
            .stLinkButton > a,
            .stFormSubmitButton > button {
                background: #020303;
                border: 1px solid color-mix(
                    in srgb,
                    var(--skim-category, var(--skim-accent)) 68%,
                    #454545
                );
                color: #f0f0f0;
                border-radius: 6px;
                min-height: 2.15rem;
                height: 2.15rem;
                line-height: 1;
                font-size: 0.78rem;
                display: inline-flex;
                align-items: center;
                justify-content: center;
                box-shadow: 0 0 11px color-mix(
                    in srgb,
                    var(--skim-category, var(--skim-accent)) 34%,
                    transparent
                );
                white-space: nowrap;
            }

            .stButton > button:hover,
            .stLinkButton > a:hover,
            .stFormSubmitButton > button:hover {
                background: #080909;
                border-color: var(--skim-category, var(--skim-accent));
                color: var(--skim-category, var(--skim-accent));
                box-shadow: 0 0 15px color-mix(
                    in srgb,
                    var(--skim-category, var(--skim-accent)) 52%,
                    transparent
                );
            }

            [data-testid="stExpander"] {
                background: #0e0d0c;
                border: 1px solid #2c2823;
                border-radius: 8px;
            }

            @media (max-width: 640px) {
                .headline-updated {
                    font-size: 0.62rem;
                }

                .category-legend {
                    gap: 0.22rem;
                }

                .category-legend-pill {
                    min-height: 1.2rem;
                    font-size: 0.55rem;
                    padding: 0.2rem 0.38rem;
                }

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

                .expanded-story-header {
                    grid-template-columns: minmax(0, 2.5fr) minmax(4.5rem, 1fr);
                    gap: 0.55rem;
                }

                .story-image {
                    max-height: 6.2rem;
                    border-radius: 8px;
                }

                .story-meta {
                    gap: 0.24rem;
                    font-size: 0.61rem;
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


def feed_entry_summary(parent: ET.Element) -> str:
    summary_tags = {"description", "summary", "content", "encoded"}
    parts: list[str] = []
    seen_parts: set[str] = set()
    for child in parent:
        if local_name(child.tag) not in summary_tags or not child.text:
            continue
        part = clean_text(child.text)
        normalized = normalized_story_text(part)
        if not part or not normalized or normalized in seen_parts:
            continue
        parts.append(part)
        seen_parts.add(normalized)
    return sanitize_article_text(" ".join(parts), max_words=240)


class GoogleNewsClusterParser(HTMLParser):
    """Extract the publisher links Google News nests inside one RSS item."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str, str]] = []
        self.in_item = False
        self.in_link = False
        self.in_source = False
        self.href = ""
        self.title_parts: list[str] = []
        self.source_parts: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        attributes = dict(attrs)
        if tag == "li":
            self.in_item = True
            self.href = ""
            self.title_parts = []
            self.source_parts = []
        elif self.in_item and tag == "a":
            self.in_link = True
            self.href = str(attributes.get("href") or "").strip()
        elif self.in_item and tag == "font":
            self.in_source = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            self.in_link = False
        elif tag == "font":
            self.in_source = False
        elif tag == "li":
            title = clean_text(" ".join(self.title_parts))
            source = clean_text(" ".join(self.source_parts))
            if title and self.href.startswith(("https://", "http://")):
                self.links.append((title, self.href, source))
            self.in_item = False
            self.in_link = False
            self.in_source = False

    def handle_data(self, data: str) -> None:
        if self.in_link:
            self.title_parts.append(data)
        elif self.in_source:
            self.source_parts.append(data)


def google_news_cluster_links(summary_html: str) -> tuple[tuple[str, str, str], ...]:
    if not summary_html:
        return ()
    parser = GoogleNewsClusterParser()
    try:
        parser.feed(summary_html)
        parser.close()
    except (ValueError, TypeError):
        return ()

    unique_links: list[tuple[str, str, str]] = []
    seen_urls: set[str] = set()
    for title, url, source in parser.links:
        url_key = normalized_story_url(url)
        if not url_key or url_key in seen_urls:
            continue
        seen_urls.add(url_key)
        unique_links.append((title, url, source))
    return tuple(unique_links)


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
        combined_summary = feed_entry_summary(entry)
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
        summary = "" if google_news_item or drudge_item else combined_summary
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


def google_news_publisher_name(value: str) -> str:
    publisher = clean_text(value)
    if not publisher:
        return "Google News publisher"
    if " " not in publisher and "." in publisher:
        return source_name_from_domain(publisher)
    return publisher


def briefing_candidate_relevance(target_title: str, candidate_title: str) -> tuple[int, int, float]:
    target_normalized = normalized_story_text(clean_headline_source(target_title))
    candidate_normalized = normalized_story_text(clean_headline_source(candidate_title))
    exact_match = int(target_normalized == candidate_normalized)
    target_terms = set(significant_words(target_title))
    candidate_terms = set(significant_words(candidate_title))
    shared_terms = target_terms.intersection(candidate_terms)
    smaller_title_size = max(1, min(len(target_terms), len(candidate_terms)))
    return exact_match, len(shared_terms), len(shared_terms) / smaller_title_size


def briefing_candidate_is_relevant(target_title: str, candidate_title: str) -> bool:
    exact_match, shared_count, smaller_title_coverage = briefing_candidate_relevance(
        target_title,
        candidate_title,
    )
    return bool(exact_match or (shared_count >= 2 and smaller_title_coverage >= 0.3))


def briefing_search_phrases(title: str) -> tuple[str, ...]:
    clean_title = clean_headline_source(title)
    phrases = [f'"{clean_title}"']

    question_clause = clean_text(clean_title.split("?", 1)[0])
    question_subject = re.sub(
        r"(?i)^(?:what|who|where|when|why|how)\s+"
        r"(?:(?:is|are|was|were|does|do|did|can|will)\s+)?"
        r"(?:(?:the|a|an)\s+)?",
        "",
        question_clause,
    ).strip()
    if (
        len(significant_words(question_subject)) >= 2
        and normalized_story_text(question_subject) != normalized_story_text(clean_title)
    ):
        phrases.append(f'"{question_subject}"')

    meaningful_words = [
        word
        for word in re.findall(r"[A-Za-z0-9']+", clean_title)
        if word.lower() not in STOPWORDS
    ]
    broader_subject = " ".join(meaningful_words[:8])
    if broader_subject and normalized_story_text(broader_subject) != normalized_story_text(clean_title):
        phrases.append(broader_subject)
    return tuple(dict.fromkeys(phrases))


@st.cache_data(ttl=300, show_spinner=False)
def fetch_google_news_briefing_candidates(
    title: str,
    topics: tuple[str, ...],
) -> tuple[Story, ...]:
    clean_title = clean_headline_source(title)
    candidates: list[Story] = []
    for search_phrase in briefing_search_phrases(clean_title):
        query = urllib.parse.urlencode(
            {
                "q": search_phrase,
                "hl": "en-US",
                "gl": "US",
                "ceid": "US:en",
            }
        )
        request = urllib.request.Request(
            f"https://news.google.com/rss/search?{query}",
            headers=REQUEST_HEADERS,
        )
        try:
            with urllib.request.urlopen(request, timeout=FEED_TIMEOUT_SECONDS) as response:
                root = ET.fromstring(response.read())
        except (
            ET.ParseError,
            urllib.error.URLError,
            TimeoutError,
            ValueError,
            OSError,
        ):
            continue

        entries = [node for node in root.iter() if local_name(node.tag) in {"item", "entry"}]
        for entry in entries[:MAX_BRIEFING_SEARCH_RESULTS]:
            published = parse_date(
                child_text(entry, ("pubDate", "published", "updated", "date"))
            )
            summary_html = child_raw_text(
                entry,
                ("description", "summary", "content", "encoded"),
            )
            nested_links = google_news_cluster_links(summary_html)
            if nested_links:
                entry_candidates = nested_links
            else:
                entry_candidates = (
                    (
                        child_text(entry, ("title",)),
                        child_link(entry),
                        child_text(entry, ("source",)),
                    ),
                )

            for candidate_title, candidate_link, publisher in entry_candidates:
                if not candidate_title or not candidate_link:
                    continue
                if not briefing_candidate_is_relevant(clean_title, candidate_title):
                    continue
                source_name = google_news_publisher_name(publisher)
                if outlet_identity(source_name) in {
                    "facebook",
                    "instagram",
                    "tiktok",
                    "x",
                    "youtube",
                }:
                    continue
                candidates.append(
                    Story(
                        id=stable_id(source_name, candidate_title, candidate_link),
                        source=source_name,
                        group="Aggregator",
                        title=candidate_title,
                        link=candidate_link,
                        summary_text="",
                        published=published,
                        topics=topics,
                    )
                )

    candidates = deduplicate_stories(candidates)
    candidates.sort(
        key=lambda candidate: (
            briefing_candidate_relevance(clean_title, candidate.title),
            -story_age_hours(candidate),
            int(is_major_outlet(candidate)),
        ),
        reverse=True,
    )
    return tuple(candidates[:MAX_BRIEFING_SEARCH_CANDIDATES])


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
        value = str(st.session_state.get(f"saved_keyword_{index}", "")).strip()
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
        saved_key = f"saved_keyword_{index}"
        draft_key = f"keyword_draft_{index}"
        query_key = f"kw{index + 1}"
        if first_load:
            legacy_value = str(st.session_state.get(f"custom_keyword_{index}", "")).strip()
            st.session_state.setdefault(
                saved_key,
                query_param_text(query_key) or legacy_value,
            )
        else:
            st.session_state.setdefault(saved_key, "")
        st.session_state.setdefault(draft_key, "")
    st.session_state.keyword_state_initialized = True


def persist_keywords_to_query_params() -> None:
    for index in range(9):
        query_key = f"kw{index + 1}"
        value = str(st.session_state.get(f"saved_keyword_{index}", "")).strip()
        if value:
            st.query_params[query_key] = value
        elif query_key in st.query_params:
            del st.query_params[query_key]


def lock_keyword_slot(index: int) -> None:
    value = clean_text(str(st.session_state.get(f"keyword_draft_{index}", "")))
    if not value:
        return
    st.session_state[f"saved_keyword_{index}"] = value
    st.session_state["last_settings"] = None
    st.query_params[f"kw{index + 1}"] = value


def clear_keyword_slot(index: int) -> None:
    st.session_state[f"saved_keyword_{index}"] = ""
    st.session_state[f"keyword_draft_{index}"] = ""
    st.session_state["last_settings"] = None
    query_key = f"kw{index + 1}"
    if query_key in st.query_params:
        del st.query_params[query_key]


def initialize_ai_cost_state() -> None:
    if st.session_state.get("ai_cost_state_initialized", False):
        return
    file_ledger = read_ai_cost_ledger()
    query_has_ledger = AI_COST_QUERY_TOTAL in st.query_params
    query_ledger = {
        "total_micros": query_param_nonnegative_int(AI_COST_QUERY_TOTAL),
        "latest_micros": query_param_nonnegative_int(AI_COST_QUERY_LATEST),
        "total_articles": query_param_nonnegative_int(AI_COST_QUERY_TOTAL_ARTICLES),
        "latest_articles": query_param_nonnegative_int(AI_COST_QUERY_LATEST_ARTICLES),
        "updated_at": query_param_nonnegative_int(AI_COST_QUERY_UPDATED),
        "events": [
            token
            for token in query_param_text(AI_COST_QUERY_EVENTS).split(",")
            if re.fullmatch(r"[a-f0-9]{16}", token)
        ],
        "history": parse_ai_cost_history(query_param_text(AI_COST_QUERY_HISTORY)),
    }
    legacy_batch_id = query_param_text(AI_COST_QUERY_LAST_BATCH)
    if legacy_batch_id:
        query_ledger["events"].append(ai_cost_event_token(legacy_batch_id))

    query_is_newer = query_ledger["updated_at"] > file_ledger["updated_at"]
    query_is_migration = (
        query_ledger["updated_at"] == file_ledger["updated_at"]
        and (
            query_ledger["total_micros"] > file_ledger["total_micros"]
            or (
                query_ledger["total_micros"] == file_ledger["total_micros"]
                and len(query_ledger["history"]) >= len(file_ledger["history"])
            )
        )
    )
    if query_has_ledger and (query_is_newer or query_is_migration):
        ledger = query_ledger
    else:
        ledger = file_ledger

    st.session_state.ai_cost_total_micros = ledger["total_micros"]
    st.session_state.ai_cost_latest_micros = ledger["latest_micros"]
    st.session_state.ai_cost_total_articles = ledger["total_articles"]
    st.session_state.ai_cost_latest_articles = ledger["latest_articles"]
    st.session_state.ai_cost_updated_at = ledger["updated_at"]
    st.session_state.ai_cost_recorded_events = list(dict.fromkeys(ledger["events"]))[
        -AI_COST_MAX_RECORDED_EVENTS:
    ]
    st.session_state.ai_cost_history = list(ledger["history"])[-AI_COST_HISTORY_LIMIT:]
    st.session_state.ai_cost_state_initialized = True
    persist_ai_cost_state()


def empty_ai_cost_ledger() -> dict[str, object]:
    return {
        "total_micros": 0,
        "latest_micros": 0,
        "total_articles": 0,
        "latest_articles": 0,
        "updated_at": 0,
        "events": [],
        "history": [],
    }


def normalize_ai_cost_history(raw: object) -> list[dict[str, object]]:
    if not isinstance(raw, list):
        return []
    normalized: list[dict[str, object]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        token = str(entry.get("token", ""))
        if not re.fullmatch(r"[a-f0-9]{16}", token):
            continue
        try:
            occurred_at = max(0, int(entry.get("at", 0)))
            cost_micros = max(0, int(entry.get("cost_micros", 0)))
            articles = max(1, int(entry.get("articles", 1)))
        except (TypeError, ValueError):
            continue
        label = re.sub(r"\s+", " ", str(entry.get("label", "AI call"))).strip()[:80]
        model = re.sub(r"\s+", " ", str(entry.get("model", "OpenAI"))).strip()[:80]
        normalized.append(
            {
                "token": token,
                "at": occurred_at,
                "cost_micros": cost_micros,
                "articles": articles,
                "label": label or "AI call",
                "model": model or "OpenAI",
            }
        )
    deduplicated: dict[str, dict[str, object]] = {}
    for entry in normalized:
        token = str(entry["token"])
        deduplicated.pop(token, None)
        deduplicated[token] = entry
    return list(deduplicated.values())[-AI_COST_HISTORY_LIMIT:]


def parse_ai_cost_history(value: str) -> list[dict[str, object]]:
    if not value:
        return []
    try:
        raw = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []
    return normalize_ai_cost_history(raw)


def normalize_ai_cost_ledger(raw: object) -> dict[str, object]:
    ledger = empty_ai_cost_ledger()
    if not isinstance(raw, dict):
        return ledger
    for key in (
        "total_micros",
        "latest_micros",
        "total_articles",
        "latest_articles",
        "updated_at",
    ):
        try:
            ledger[key] = max(0, int(raw.get(key, 0)))
        except (TypeError, ValueError):
            ledger[key] = 0
    events = raw.get("events", [])
    if isinstance(events, list):
        ledger["events"] = [
            str(token)
            for token in events
            if re.fullmatch(r"[a-f0-9]{16}", str(token))
        ][-AI_COST_MAX_RECORDED_EVENTS:]
    ledger["history"] = normalize_ai_cost_history(raw.get("history", []))
    return ledger


def read_ai_cost_ledger() -> dict[str, object]:
    try:
        with AI_COST_LEDGER_LOCK:
            raw = json.loads(AI_COST_LEDGER_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty_ai_cost_ledger()
    return normalize_ai_cost_ledger(raw)


def current_ai_cost_ledger() -> dict[str, object]:
    return {
        "total_micros": int(st.session_state.get("ai_cost_total_micros", 0)),
        "latest_micros": int(st.session_state.get("ai_cost_latest_micros", 0)),
        "total_articles": int(st.session_state.get("ai_cost_total_articles", 0)),
        "latest_articles": int(st.session_state.get("ai_cost_latest_articles", 0)),
        "updated_at": int(st.session_state.get("ai_cost_updated_at", 0)),
        "events": list(st.session_state.get("ai_cost_recorded_events", []))[
            -AI_COST_MAX_RECORDED_EVENTS:
        ],
        "history": normalize_ai_cost_history(
            st.session_state.get("ai_cost_history", [])
        ),
    }


def write_ai_cost_ledger(ledger: dict[str, object]) -> None:
    temporary_path = AI_COST_LEDGER_PATH.with_suffix(".tmp")
    try:
        with AI_COST_LEDGER_LOCK:
            temporary_path.write_text(
                json.dumps(normalize_ai_cost_ledger(ledger), separators=(",", ":")),
                encoding="utf-8",
            )
            os.replace(temporary_path, AI_COST_LEDGER_PATH)
    except OSError:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass


def persist_ai_cost_state() -> None:
    ledger = current_ai_cost_ledger()
    write_ai_cost_ledger(ledger)
    values = {
        AI_COST_QUERY_TOTAL: ledger["total_micros"],
        AI_COST_QUERY_LATEST: ledger["latest_micros"],
        AI_COST_QUERY_TOTAL_ARTICLES: ledger["total_articles"],
        AI_COST_QUERY_LATEST_ARTICLES: ledger["latest_articles"],
        AI_COST_QUERY_EVENTS: ",".join(ledger["events"]),
        AI_COST_QUERY_UPDATED: ledger["updated_at"],
        AI_COST_QUERY_HISTORY: json.dumps(ledger["history"], separators=(",", ":")),
    }
    for key, value in values.items():
        st.query_params[key] = str(value)
    if AI_COST_QUERY_LAST_BATCH in st.query_params:
        del st.query_params[AI_COST_QUERY_LAST_BATCH]


def sync_ai_cost_browser_storage(query_had_ledger: bool) -> None:
    ledger_json = json.dumps(current_ai_cost_ledger(), separators=(",", ":"))
    query_names_json = json.dumps(
        {
            "total_micros": AI_COST_QUERY_TOTAL,
            "latest_micros": AI_COST_QUERY_LATEST,
            "total_articles": AI_COST_QUERY_TOTAL_ARTICLES,
            "latest_articles": AI_COST_QUERY_LATEST_ARTICLES,
            "events": AI_COST_QUERY_EVENTS,
            "updated_at": AI_COST_QUERY_UPDATED,
            "history": AI_COST_QUERY_HISTORY,
        }
    )
    components.html(
        f"""
        <script>
        (() => {{
          try {{
            const storageKey = {json.dumps(AI_COST_BROWSER_STORAGE_KEY)};
            const serverLedger = {ledger_json};
            const names = {query_names_json};
            const stored = JSON.parse(window.parent.localStorage.getItem(storageKey) || "null");
            const storedTotal = Number(stored && stored.total_micros) || 0;
            const serverTotal = Number(serverLedger.total_micros) || 0;
            const storedUpdated = Number(stored && stored.updated_at) || 0;
            const serverUpdated = Number(serverLedger.updated_at) || 0;
            const storedIsNewer = storedUpdated > serverUpdated
              || (storedUpdated === serverUpdated && storedTotal > serverTotal);
            if (!{str(query_had_ledger).lower()} && storedIsNewer) {{
              const url = new URL(window.parent.location.href);
              for (const [field, queryName] of Object.entries(names)) {{
                const value = field === "events"
                  ? (Array.isArray(stored[field]) ? stored[field].join(",") : "")
                  : field === "history"
                  ? JSON.stringify(Array.isArray(stored[field]) ? stored[field] : [])
                  : String(Math.max(0, Number(stored[field]) || 0));
                url.searchParams.set(queryName, value);
              }}
              window.parent.location.replace(url.toString());
              return;
            }}
            window.parent.localStorage.setItem(storageKey, JSON.stringify(serverLedger));
          }} catch (error) {{
            // The app-file ledger remains the source of truth if browser storage is unavailable.
          }}
        }})();
        </script>
        """,
        height=0,
        width=0,
    )


def set_ai_cost_total(total_dollars: float, reset_history: bool = False) -> None:
    st.session_state.ai_cost_total_micros = max(
        0,
        round(float(total_dollars) * AI_COST_SCALE),
    )
    if reset_history:
        st.session_state.ai_cost_latest_micros = 0
        st.session_state.ai_cost_total_articles = 0
        st.session_state.ai_cost_latest_articles = 0
        st.session_state.ai_cost_recorded_events = []
        st.session_state.ai_cost_history = []
    st.session_state.ai_cost_updated_at = round(datetime.now(timezone.utc).timestamp() * 1000)
    persist_ai_cost_state()


def complete_story_refresh() -> None:
    fetch_source.clear()
    fetch_google_news_briefing_candidates.clear()
    resolve_article_url.clear()
    fetch_article_evidence.clear()
    st.session_state.current_cluster_keys = []
    st.session_state.last_settings = None
    st.session_state.deep_analyses = {}
    st.session_state.research_briefs = {}
    st.session_state.story_questions = {}
    st.session_state.prepared_summary_results = {}
    st.session_state.extracting_story_id = ""
    st.session_state.deep_analysis_loading_story_id = ""
    st.session_state.pinned_story_id = ""
    st.session_state.expanded_story_ids = set()
    st.session_state.scroll_to_top_pending = True


def load_next_story_batch() -> None:
    st.session_state.current_cluster_keys = []
    st.session_state.deep_analyses = {}
    st.session_state.research_briefs = {}
    st.session_state.story_questions = {}
    st.session_state.prepared_summary_results = {}
    st.session_state.extracting_story_id = ""
    st.session_state.deep_analysis_loading_story_id = ""
    st.session_state.pinned_story_id = ""
    st.session_state.expanded_story_ids = set()
    st.session_state.scroll_to_top_pending = True


def scroll_page_to_top() -> None:
    components.html(
        """
        <script>
        const scroller = window.parent.document.querySelector("section.stMain");
        if (scroller) {
          scroller.scrollTo({top: 0, left: 0, behavior: "instant"});
        } else {
          window.parent.scrollTo({top: 0, left: 0, behavior: "instant"});
        }
        </script>
        """,
        height=0,
        width=0,
    )


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
    reference = next(iter(reference_links(story, topics)), google_news_search_link(story))
    wiki_link = wikipedia_links(story, topics)[0]
    if reference[1] == wiki_link[1]:
        reference = google_news_search_link(story)
    return reference, wiki_link


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


def feed_story_evidence(story: Story) -> ArticleEvidence | None:
    feed_text = sanitize_article_text(story.summary_text, max_words=240)
    if not has_enough_reported_material(story.title, feed_text):
        return None
    word_count = len(feed_text.split())
    if word_count < MIN_FEED_EVIDENCE_WORDS:
        return None
    if sentence_count(feed_text) < MIN_FEED_EVIDENCE_SENTENCES:
        return None
    return ArticleEvidence(
        url=story.link,
        title=clean_headline_source(story.title),
        text=feed_text,
        word_count=word_count,
    )


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

RESEARCH_BRIEF_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "brief": {"type": "string"},
    },
    "required": ["brief"],
    "additionalProperties": False,
}

STORY_QUESTION_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
    },
    "required": ["answer"],
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


def summary_readability_guidance(plain_language: bool) -> str:
    if not plain_language:
        return (
            "Use polished general-audience news prose. Explain specialist terms when they are "
            "necessary to understand the event."
        )
    return (
        "Write about 20% more simply than standard news coverage for an intelligent reader who "
        "is new to the topic. Prefer familiar, concrete words and active voice. Aim for most "
        "sentences to be 12-22 words, and split long chains of causes or consequences into "
        "separate sentences. Explain unavoidable jargon immediately in plain language. Preserve "
        "important names, numbers, dates, uncertainty, and nuance. Never sound childish, chatty, "
        "patronizing, or less precise."
    )


@st.cache_data(ttl=86400, show_spinner=False)
def ai_summary_cached(
    provider: str,
    model: str,
    prompt_version: str,
    refresh_key: str,
    story_id: str,
    selected_title: str,
    publisher_title: str,
    source: str,
    group: str,
    rss_summary: str,
    article_url: str,
    article_text: str,
    topics: tuple[str, ...],
    detail: int,
    plain_language: bool,
) -> dict:
    prompt = textwrap.dedent(
        f"""
        PUBLISHER: {source}
        SOURCE TYPE: {group}
        TOPICS: {", ".join(topics)}
        SELECTED HEADLINE: {clean_headline_source(selected_title)}
        EVIDENCE PUBLISHER HEADLINE: {clean_headline_source(publisher_title)}
        RSS DESCRIPTION: {clean_text(rss_summary) or "Not available."}
        PUBLISHER URL: {article_url}
        DESIRED DETAIL: {detail}/5

        <ARTICLE_BODY>
        {article_text}
        </ARTICLE_BODY>
        """
    ).strip()
    instructions = textwrap.dedent(
        f"""
        You are the editor of Skim. Read the supplied publisher article body closely before
        writing. The current event facts must come from that body. You may use reliable
        general knowledge only to explain established background or cautious implications,
        never to add unreported current-event facts.

        Center the brief on SELECTED HEADLINE, which is the subject the reader opened. When the
        evidence comes from another publisher with a different headline, use only the facts and
        background that directly illuminate the selected subject. Do not merge separate events
        or imply the evidence publisher reported a detail that is absent from ARTICLE_BODY.

        READABILITY:
        {summary_readability_guidance(plain_language)}

        Return the required JSON fields:
        - headline: 5-10 words and no more than 78 characters. State the central development
          as a complete, natural thought. Keep names, places, and stakes that make it
          meaningful. No ellipses, label, teaser, clickbait, dangling preposition, or abrupt
          truncation.
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
    ).strip()
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
    selected_title: str,
    publisher_title: str,
    source: str,
    article_text: str,
    draft_json: str,
    quality_errors: tuple[str, ...],
    plain_language: bool,
) -> dict:
    prompt = textwrap.dedent(
        f"""
        PUBLISHER: {source}
        SELECTED HEADLINE: {clean_headline_source(selected_title)}
        EVIDENCE PUBLISHER HEADLINE: {clean_headline_source(publisher_title)}

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
    instructions = textwrap.dedent(
        f"""
        Rewrite the rejected Skim card so every listed quality failure is fixed. Ground all
        current facts in ARTICLE_BODY. Return only the required JSON fields.

        READABILITY:
        {summary_readability_guidance(plain_language)}

        The headline is 5-10 words, no more than 78 characters, and a complete thought. The
        summary is 3-4 cohesive sentences and 65-150 words. The background is 2-3 specific
        sentences and 45-125 words. Do not mention an article, story, headline, feed, coverage,
        newsletter, missing details, reading, or clicking. Remove promotional fragments and
        generic analysis. Do not repeat sentences.
        """
    ).strip()
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


@st.cache_data(ttl=86400, show_spinner=False)
def ai_research_brief_cached(
    provider: str,
    model: str,
    story_id: str,
    title: str,
    research_topic: str,
    deep_analysis: str,
    article_text: str,
    topics: tuple[str, ...],
) -> dict:
    prompt = textwrap.dedent(
        f"""
        NEWS EVENT: {clean_headline_source(title)}
        CATEGORY TOPICS: {", ".join(topics)}
        RESEARCH TOPIC: {research_topic}

        <DEEP_ANALYSIS>
        {deep_analysis}
        </DEEP_ANALYSIS>

        <ARTICLE_BODY>
        {article_text}
        </ARTICLE_BODY>
        """
    ).strip()
    instructions = textwrap.dedent(
        f"""
        You are Skim's plain-language teacher. Explain the named RESEARCH TOPIC, using reliable
        established knowledge and the supplied event only to show why the topic is relevant.
        Return a single brief of 3-5 cohesive sentences. Start by defining the topic in direct
        language. Then teach the key mechanism, history, institution, or distinction a reader
        should know. End by connecting that knowledge to the news event without repeating the
        event summary.

        {summary_readability_guidance(True)}

        Keep the brief between 65 and 140 words. Every sentence must teach something concrete.
        Do not mention an article, story, headline, research trail, prompt, or AI. Do not give
        advice about what to read or click. Do not invent current-event facts.
        """
    ).strip()
    return ai_json(
        provider,
        model,
        instructions,
        prompt,
        effort="high",
        max_output_tokens=1800,
        schema_name="skim_research_brief",
        schema=RESEARCH_BRIEF_RESPONSE_SCHEMA,
    )


@st.cache_data(ttl=86400, show_spinner=False)
def ai_story_question_cached(
    provider: str,
    model: str,
    story_id: str,
    title: str,
    source: str,
    article_text: str,
    summary_text: str,
    deep_context: str,
    question: str,
) -> dict:
    prompt = textwrap.dedent(
        f"""
        NEWS EVENT: {clean_headline_source(title)}
        PUBLISHER: {source}
        READER QUESTION: {question}

        <SKIM_BRIEF>
        {summary_text}
        </SKIM_BRIEF>

        <DEEP_ANALYSIS>
        {deep_context or "Not requested."}
        </DEEP_ANALYSIS>

        <ARTICLE_BODY>
        {article_text}
        </ARTICLE_BODY>
        """
    ).strip()
    instructions = textwrap.dedent(
        f"""
        Answer the reader's question directly in 3-4 cohesive sentences. Use the supplied source
        material for current-event facts and reliable established knowledge for explanation.
        Teach the key idea in plain language, preserve uncertainty, and distinguish a fact from a
        reasonable inference. If the source material cannot establish the answer, say what is
        known and what remains uncertain without inventing details.

        {summary_readability_guidance(True)}

        Keep the answer between 45 and 120 words. Do not mention an article, story, prompt, model,
        AI, or reading process. Do not add links, headings, bullets, or advice about what to click.
        """
    ).strip()
    return ai_json(
        provider,
        model,
        instructions,
        prompt,
        effort="high",
        max_output_tokens=1400,
        schema_name="skim_story_question",
        schema=STORY_QUESTION_RESPONSE_SCHEMA,
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
    plain_language: bool = True,
    article_story: Story | None = None,
) -> SummaryAttempt:
    provider = configured_ai_provider()
    if not provider:
        return SummaryAttempt(card=None, ai_cost=0.0)

    source_story = article_story or story
    topics = tuple(dict.fromkeys((*infer_topics(story), *infer_topics(source_story))))
    fallback_links = story_learning_links(story, topics)
    model = ai_model(provider, deep=False)
    rss_summary = sanitize_article_text(source_story.summary_text, max_words=180)
    if not has_enough_reported_material(source_story.title, rss_summary):
        rss_summary = ""
    try:
        ai_result = ai_summary_cached(
            provider,
            model,
            AI_SUMMARY_PROMPT_VERSION,
            refresh_key,
            story.id,
            story.title,
            source_story.title,
            source_story.source,
            source_story.group,
            rss_summary,
            evidence.url,
            evidence.text,
            topics,
            detail,
            plain_language,
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
                source_story.title,
                source_story.source,
                evidence.text,
                json.dumps(card, ensure_ascii=True, sort_keys=True),
                errors,
                plain_language,
            )
        except Exception as exc:
            record_generation_issue(ai_failure_message(provider, model, exc))
            return SummaryAttempt(card=None, ai_cost=ai_cost)
        if provider == "openai":
            repair_cost = result_openai_cost(repaired, model)
            if repair_cost is None:
                estimated_repair_input = estimated_token_count(
                    source_story.title,
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
            "Research trail": "Choose a focused historical, institutional, or technical topic.",
            "Learn More": f"Learn more: {learning_links_text(fallback_links)}",
        }

    model = ai_model(provider, deep=True)
    ai_result = ai_deep_analysis_cached(
        provider,
        model,
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
    result["Research trail"] = research
    result["Learn More"] = f"Learn more: {learning_links_text(fallback_links)}"
    result["__research_topic"] = research
    if provider == "openai":
        ai_cost = result_openai_cost(ai_result, model)
        if ai_cost is None:
            estimated_input = estimated_token_count(
                story.title,
                story.summary_text,
                evidence.text,
                overhead_tokens=520,
            )
            ai_cost = openai_cost(model, estimated_input, 1_500)
        result["__ai_cost"] = f"{ai_cost or 0.0:.8f}"
    return result


def research_topic_from_analysis(analysis: dict[str, str]) -> str:
    hidden_topic = clean_text(str(analysis.get("__research_topic", "")))
    if hidden_topic:
        return hidden_topic
    research_trail = str(analysis.get("Research trail", ""))
    topic_text = re.split(r"\s*Learn more:\s*", research_trail, maxsplit=1, flags=re.IGNORECASE)[0]
    return clean_text(strip_markdown_links(topic_text))


def research_topic_brief(
    story: Story,
    evidence: ArticleEvidence,
    analysis: dict[str, str],
) -> dict[str, str]:
    provider = configured_ai_provider()
    if not provider:
        raise ValueError("Add an AI API key in Streamlit secrets to build this research brief.")

    research_topic = research_topic_from_analysis(analysis)
    if not research_topic:
        raise ValueError("The deeper analysis did not identify a focused research topic.")

    deep_context = " ".join(
        clean_text(str(analysis.get(label, "")))
        for label in ("Deeper analysis", "Watch next")
        if analysis.get(label)
    )
    topics = infer_topics(story)
    model = ai_model(provider, deep=True)
    ai_result = ai_research_brief_cached(
        provider,
        model,
        story.id,
        story.title,
        research_topic,
        deep_context,
        evidence.text,
        topics,
    )
    brief = clean_text(strip_markdown_links(str(ai_result.get("brief", ""))))
    brief_sentence_count = sentence_count(brief)
    if (
        not 3 <= brief_sentence_count <= 5
        or len(brief.split()) < 50
        or prose_has_forbidden_language(brief)
    ):
        raise ValueError("The AI provider did not return a clear 3-5 sentence research brief.")

    result = {"Research brief": brief}
    if provider == "openai":
        ai_cost = result_openai_cost(ai_result, model)
        if ai_cost is None:
            estimated_input = estimated_token_count(
                story.title,
                research_topic,
                deep_context,
                evidence.text,
                overhead_tokens=500,
            )
            ai_cost = openai_cost(model, estimated_input, 650)
        result["__ai_cost"] = f"{ai_cost or 0.0:.8f}"
    return result


def answer_story_question(
    prepared_story: PreparedStory,
    question: str,
) -> dict[str, str]:
    provider = configured_ai_provider()
    if not provider:
        raise ValueError("Add an AI API key in Streamlit secrets to answer questions.")

    story = prepared_story.ranked_story.story
    analysis = st.session_state.deep_analyses.get(story.id, {})
    summary_text = " ".join(
        clean_text(str(value))
        for label, value in prepared_story.card.items()
        if not label.startswith("__") and label != "Learn More"
    )
    deep_context = " ".join(
        clean_text(str(analysis.get(label, "")))
        for label in ("Deeper analysis", "Watch next", "Research trail")
        if analysis.get(label)
    )
    model = ai_model(provider, deep=True)
    ai_result = ai_story_question_cached(
        provider,
        model,
        story.id,
        story.title,
        story.source,
        prepared_story.evidence.text,
        summary_text,
        deep_context,
        clean_text(question),
    )
    answer = clean_text(strip_markdown_links(str(ai_result.get("answer", ""))))
    if (
        not 3 <= sentence_count(answer) <= 4
        or not 35 <= len(answer.split()) <= 135
        or prose_has_forbidden_language(answer)
    ):
        raise ValueError("The AI provider did not return a clear 3-4 sentence answer.")

    result = {"answer": answer}
    if provider == "openai":
        ai_cost = result_openai_cost(ai_result, model)
        if ai_cost is None:
            estimated_input = estimated_token_count(
                story.title,
                prepared_story.evidence.text,
                summary_text,
                deep_context,
                question,
                overhead_tokens=420,
            )
            ai_cost = openai_cost(model, estimated_input, 500)
        result["__ai_cost"] = f"{ai_cost or 0.0:.8f}"
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


def ranked_story_published_timestamp(ranked_story: RankedStory) -> float:
    published = ranked_story.story.published
    if not published:
        return float("-inf")
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    return published.astimezone(timezone.utc).timestamp()


def sort_headlines_by_age(ranked_stories: Sequence[RankedStory]) -> list[RankedStory]:
    return sorted(ranked_stories, key=ranked_story_published_timestamp, reverse=True)


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
    if not ranked_story.outlets:
        outlet_word = "outlet" if ranked_story.references == 1 else "outlets"
        return f"{ranked_story.references} {outlet_word}"

    label = ""
    for outlet in ranked_story.outlets:
        candidate = clean_text(re.sub(r"\s+via\s+Drudge(?:\s+Report)?$", "", outlet, flags=re.IGNORECASE))
        if candidate.startswith(("©", "(c)")):
            credit_tail = candidate.rsplit(",", 1)[-1].strip()
            if credit_tail.lower() in {"afp", "ap", "associated press", "reuters"}:
                candidate = credit_tail
            else:
                continue
        if candidate:
            label = candidate
            break
    label = label or clean_text(ranked_story.outlets[0])
    remaining = max(0, ranked_story.references - 1)
    if remaining:
        outlet_word = "outlet" if remaining == 1 else "outlets"
        label += f" · {remaining} more {outlet_word}"
    return label


def expanded_headline_font_sizes(display_headline: str, has_image: bool) -> tuple[float, float]:
    headline_length = max(1, len(clean_text(display_headline)))
    desktop_capacity = 62 if has_image else 92
    desktop_size = max(1.0, min(1.575, 1.575 * desktop_capacity / headline_length))
    mobile_capacity = 36 if has_image else 58
    mobile_size = max(0.72, min(1.2, 1.2 * mobile_capacity / headline_length))
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
            f"{ranked_story.topic_story_count} {report_word}"
            f'</div>'
            f'<div class="compact-headline-time category-time {category_class}">'
            f"{html.escape(story_age(story))}"
            f'</div>',
            unsafe_allow_html=True,
        )
        return pressed

    meta = (
        f'<span class="headline-category {category_class}">{html.escape(category)}</span>'
        f"<span>·</span>"
        f'<span class="story-meta-outlets">{html.escape(coverage_outlet_text(ranked_story))}</span>'
        f"<span>·</span>"
        f"<span>{ranked_story.topic_story_count} {report_word}</span>"
        f"<span>·</span>"
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
        image_url = html.escape(story.image_url, quote=True)
        st.markdown(
            '<div class="expanded-story-header">'
            f'<h2 class="story-title" style="{title_style}">{story_title_text}</h2>'
            f'<img class="story-image" src="{image_url}" alt="">'
            "</div>",
            unsafe_allow_html=True,
        )
    else:
        story_title = (
            f'<h2 class="story-title story-title-full" style="{title_style}">'
            f"{story_title_text}</h2>"
        )
        st.markdown(story_title, unsafe_allow_html=True)
    return False


def ai_working_markup(message: str) -> str:
    return (
        '<div class="ai-working-box" role="status">'
        '<div class="ai-working-icon" aria-hidden="true">'
        '<span class="ai-working-newspaper">📰</span>'
        '<span class="ai-working-lightbulb">💡</span>'
        "</div>"
        f'<div class="ai-working-copy">{html.escape(message)}</div>'
        "</div>"
    )


def split_legacy_research_links(analysis: dict[str, str]) -> dict[str, str]:
    normalized = dict(analysis)
    research_trail = str(normalized.get("Research trail", ""))
    split_trail = re.split(
        r"\s*Learn more:\s*",
        research_trail,
        maxsplit=1,
        flags=re.IGNORECASE,
    )
    if len(split_trail) == 2:
        normalized["Research trail"] = clean_text(split_trail[0])
        normalized.setdefault("Learn More", f"Learn more: {split_trail[1].strip()}")
    return normalized


def learn_more_placement(
    summary: dict[str, str],
    analysis: dict[str, str] | None,
) -> tuple[str, str]:
    summary_value = str(summary.get("Learn More", ""))
    if analysis:
        return "analysis", str(analysis.get("Learn More") or summary_value)
    return "summary", summary_value


def render_deep_analysis(
    prepared_story: PreparedStory,
    learn_more: str = "",
) -> None:
    story = prepared_story.ranked_story.story
    stored_analysis = st.session_state.deep_analyses.get(story.id)
    analysis = split_legacy_research_links(stored_analysis) if stored_analysis else None
    if not analysis:
        return

    research_topic = research_topic_from_analysis(analysis)
    with st.container(key=f"deep_analysis_{story.id}"):
        leading_fields: list[str] = []
        for index, label in enumerate(("Deeper analysis", "Watch next")):
            value = analysis.get(label)
            if not value:
                continue
            field_classes = "deep-summary-field"
            if not leading_fields:
                field_classes += " deep-summary-field-first"
            leading_fields.append(
                f'<div class="{field_classes}"><b>{html.escape(label)}:</b> '
                f"{render_summary_value(value)}</div>"
            )
        if leading_fields:
            st.markdown("".join(leading_fields), unsafe_allow_html=True)

        if research_topic:
            research_value = str(analysis.get("Research trail", ""))
            rendered_research = render_summary_value(research_value)
            if clean_text(research_value)[-1:] not in ".!?":
                rendered_research += "."
            with st.container(key=f"research_row_{story.id}"):
                st.markdown(
                    '<span class="research-trail-copy"><b>Research trail:</b> '
                    f"{rendered_research}&nbsp;</span>",
                    unsafe_allow_html=True,
                )
                summarize_research = st.button(
                    "Summarize this research with AI.",
                    key=f"research-brief-{story.id}",
                    type="tertiary",
                )

            if summarize_research:
                provider = configured_ai_provider()
                model = ai_model(provider, deep=True) if provider else "not configured"
                loading_slot = st.empty()
                loading_slot.markdown(
                    ai_working_markup(f"Using AI ({model}) to explain this research topic..."),
                    unsafe_allow_html=True,
                )
                try:
                    brief = research_topic_brief(story, prepared_story.evidence, analysis)
                    st.session_state.research_briefs[story.id] = brief
                    topic_token = hashlib.sha256(research_topic.encode("utf-8")).hexdigest()[:12]
                    record_batch_ai_cost(
                        [],
                        f"research-{st.session_state.batch_refresh_id}-{story.id}-{topic_token}",
                        card_ai_cost(brief),
                        attempted_articles=1,
                    )
                except Exception as exc:
                    st.session_state.research_briefs[story.id] = {
                        "Research brief": f"The AI provider could not complete this request: {exc}"
                    }
                finally:
                    loading_slot.empty()

            research_brief = st.session_state.research_briefs.get(story.id)
            if research_brief:
                brief_text = html.escape(str(research_brief.get("Research brief", "")))
                st.markdown(
                    '<div class="deep-summary-field"><b>Research brief:</b> '
                    f"{brief_text}</div>",
                    unsafe_allow_html=True,
                )

        if learn_more:
            st.markdown(
                '<div class="deep-summary-field deep-learn-more">'
                f"{render_summary_value(learn_more)}</div>",
                unsafe_allow_html=True,
            )


def render_story_questions(prepared_story: PreparedStory) -> None:
    story = prepared_story.ranked_story.story
    if story.id not in st.session_state.deep_analyses:
        return

    questions = list(st.session_state.story_questions.get(story.id, []))
    with st.container(key=f"story_questions_{story.id}"):
        st.markdown(
            '<div class="story-question-heading">Ask about this story</div>',
            unsafe_allow_html=True,
        )
        for exchange in questions:
            st.markdown(
                f'<div class="story-question">{html.escape(exchange["question"])}</div>'
                f'<div class="story-answer">{html.escape(exchange["answer"])}</div>',
                unsafe_allow_html=True,
            )

        with st.form(
            f"question_form_{story.id}_{len(questions)}",
            clear_on_submit=True,
            border=False,
        ):
            question = st.text_input(
                "Ask a question",
                key=f"question_input_{story.id}_{len(questions)}",
                placeholder="Type a question here",
                label_visibility="collapsed",
            )
            submitted = st.form_submit_button("Submit question")

        if submitted and clean_text(question):
            provider = configured_ai_provider()
            model = ai_model(provider, deep=True) if provider else "not configured"
            loading_slot = st.empty()
            loading_slot.markdown(
                ai_working_markup(f"Using AI ({model}) to answer your question..."),
                unsafe_allow_html=True,
            )
            try:
                result = answer_story_question(prepared_story, question)
                questions.append(
                    {
                        "question": clean_text(question),
                        "answer": result["answer"],
                    }
                )
                st.session_state.story_questions[story.id] = questions
                question_token = hashlib.sha256(
                    clean_text(question).lower().encode("utf-8")
                ).hexdigest()[:12]
                record_batch_ai_cost(
                    [],
                    (
                        f"question-{st.session_state.batch_refresh_id}-"
                        f"{story.id}-{question_token}"
                    ),
                    card_ai_cost(result),
                    attempted_articles=1,
                )
            except Exception as exc:
                issue = f"Skim could not answer that question: {clean_text(str(exc))[:180]}"
                record_generation_issue(issue)
                questions.append(
                    {
                        "question": clean_text(question),
                        "answer": issue,
                    }
                )
                st.session_state.story_questions[story.id] = questions
            finally:
                loading_slot.empty()
            st.rerun()


def render_story_details(prepared_story: PreparedStory) -> None:
    ranked_story = prepared_story.ranked_story
    story = ranked_story.story
    evidence = prepared_story.evidence
    summary = dict(prepared_story.card)
    display_headline = summary.pop("__headline")

    if st.session_state.deep_analysis_loading_story_id == story.id:
        loading_slot = st.empty()
        loading_slot.markdown(
            ai_working_markup("Building the deeper read..."),
            unsafe_allow_html=True,
        )
        try:
            analysis = deeper_analysis(story, evidence)
            st.session_state.deep_analyses[story.id] = analysis
            st.session_state.research_briefs.pop(story.id, None)
            st.session_state.story_questions.pop(story.id, None)
            record_batch_ai_cost(
                [],
                f"deep-{st.session_state.batch_refresh_id}-{story.id}",
                card_ai_cost(analysis),
                attempted_articles=1,
            )
        except Exception as exc:
            st.session_state.deep_analyses[story.id] = {
                "Deeper analysis": f"The AI provider could not complete this request: {exc}"
            }
        finally:
            st.session_state.deep_analysis_loading_story_id = ""
            loading_slot.empty()

    stored_analysis = st.session_state.deep_analyses.get(story.id)
    analysis = split_legacy_research_links(stored_analysis) if stored_analysis else None
    learn_more_owner, learn_more = learn_more_placement(summary, analysis)

    rows = ""
    for label, value in summary.items():
        if label.startswith("__") or label == "Learn More":
            continue
        label_html = f"<b>{html.escape(label)}:</b> " if label else ""
        rows += f'<div class="summary-field">{label_html}{render_summary_value(value)}</div>'
    if learn_more_owner == "summary" and learn_more:
        rows += (
            '<div class="summary-field">'
            f"{render_summary_value(learn_more)}</div>"
        )
    with st.container(key=f"summary_section_{story.id}"):
        st.markdown(f'<div class="summary-grid">{rows}</div>', unsafe_allow_html=True)

    render_deep_analysis(prepared_story, learn_more if learn_more_owner == "analysis" else "")
    render_story_questions(prepared_story)

    with st.container(key=f"story_actions_{story.id}"):
        col1, col2, col3 = st.columns([1, 1, 1], gap="small", vertical_alignment="top")
        with col1:
            st.link_button("Full story", evidence.url, use_container_width=True)
        with col2:
            if st.button("Deep analysis", key=f"deep-{story.id}", use_container_width=True):
                st.session_state.deep_analysis_loading_story_id = story.id
                st.rerun()
        with col3:
            st.link_button(
                "Share",
                share_sms_url(story, evidence.url, display_headline),
                use_container_width=True,
            )


def render_story(prepared_story: PreparedStory) -> None:
    with st.container(border=True):
        display_headline = str(prepared_story.card.get("__headline", ""))
        render_story_header(prepared_story.ranked_story, display_headline)
        render_story_details(prepared_story)


def render_headline_story(
    ranked_story: RankedStory,
    detail: int,
    plain_language: bool = True,
) -> None:
    story = ranked_story.story
    expanded_story_ids = set(st.session_state.expanded_story_ids)
    is_expanded = story.id in expanded_story_ids
    is_extracting = st.session_state.extracting_story_id == story.id
    with st.container(border=True):
        prepared = None
        attempted_cost = 0.0
        if is_expanded:
            readability_key = "plain" if plain_language else "standard"
            summary_key = (
                f"headline-{AI_SUMMARY_PROMPT_VERSION}-{st.session_state.batch_refresh_id}-"
                f"{story.id}-{readability_key}"
            )
            provider = configured_ai_provider()
            summary_model = ai_model(provider, deep=False) if provider else "not configured"
            cached_result = st.session_state.prepared_summary_results.get(summary_key)

            if is_extracting and cached_result is not None:
                st.session_state.extracting_story_id = ""
                st.rerun()

            if is_extracting and cached_result is None:
                render_story_header(ranked_story, clean_headline_source(story.title))
                loading_slot = st.empty()
                loading_slot.markdown(
                    ai_working_markup(
                        f"Using AI ({summary_model}) to extract and summarize this story..."
                    ),
                    unsafe_allow_html=True,
                )
                try:
                    prepared, attempted_cost = prepare_ranked_story(
                        ranked_story,
                        detail,
                        summary_key,
                        plain_language,
                    )
                except Exception as exc:
                    record_generation_issue(
                        f"Skim could not prepare the selected story: {clean_text(str(exc))[:180]}"
                    )
                finally:
                    loading_slot.empty()
                st.session_state.prepared_summary_results[summary_key] = (
                    prepared,
                    attempted_cost,
                )
                record_batch_ai_cost(
                    [prepared] if prepared else [],
                    summary_key,
                    attempted_cost,
                    attempted_articles=1 if attempted_cost > 0 else 0,
                )
                st.session_state.extracting_story_id = ""
                st.rerun()

            if cached_result is not None:
                prepared, attempted_cost = cached_result
            else:
                loading_slot = st.empty()
                loading_slot.markdown(
                    ai_working_markup(
                        f"Using AI ({summary_model}) to extract and summarize this story..."
                    ),
                    unsafe_allow_html=True,
                )
                try:
                    prepared, attempted_cost = prepare_ranked_story(
                        ranked_story,
                        detail,
                        summary_key,
                        plain_language,
                    )
                finally:
                    loading_slot.empty()
                st.session_state.prepared_summary_results[summary_key] = (
                    prepared,
                    attempted_cost,
                )
                record_batch_ai_cost(
                    [prepared] if prepared else [],
                    summary_key,
                    attempted_cost,
                    attempted_articles=1 if attempted_cost > 0 else 0,
                )
            expanded_headline = (
                str(prepared.card.get("__headline", ""))
                if prepared
                else clean_headline_source(story.title)
            )
            render_story_header(ranked_story, expanded_headline)
        else:
            action_pressed = render_story_header(
                ranked_story,
                clean_headline_source(story.title),
                compact=True,
                headline_button_key=f"expand-{story.id}",
            )

        if not is_expanded and action_pressed:
            expanded_story_ids.add(story.id)
            st.session_state.expanded_story_ids = expanded_story_ids
            st.session_state.extracting_story_id = story.id
            st.session_state.pinned_story_id = story.id
            st.session_state.scroll_to_top_pending = True
            st.rerun()

        if not is_expanded:
            return

        st.markdown('<div class="headline-brief-divider"></div>', unsafe_allow_html=True)
        if prepared:
            render_story_details(prepared)
        else:
            if not configured_ai_provider():
                st.info("Add OPENAI_API_KEY in Streamlit secrets to generate this brief.")
            elif st.session_state.generation_issues:
                st.error("Skim could not generate this brief. Open Feed notes below for the exact reason.")
            else:
                st.warning(
                    "Skim could not retrieve enough article or publisher-feed text to build a "
                    "grounded brief."
                )

        if st.button(
            "Close brief",
            key=f"close_brief_{story.id}",
            icon=":material/expand_less:",
            use_container_width=True,
        ):
            expanded_story_ids.discard(story.id)
            st.session_state.expanded_story_ids = expanded_story_ids
            if st.session_state.extracting_story_id == story.id:
                st.session_state.extracting_story_id = ""
            if st.session_state.pinned_story_id == story.id:
                st.session_state.pinned_story_id = ""
            st.rerun()


def render_header() -> None:
    st.markdown(
        f"""
        <div class="skim-header">
            <div>
                <div class="skim-brand">{APP_NAME}</div>
                <div class="skim-tagline">Fresh &amp; fast AI news briefs.</div>
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
        article_word = "story" if latest_articles == 1 else "stories"
        latest_text = (
            f"<strong>Latest AI use:</strong> {latest_articles} {article_word} "
            f"cost about {format_cost(latest_micros / AI_COST_SCALE)}. "
            f"Tracking {total_articles} AI-generated briefs and analyses since this counter started."
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
                <div class="ai-cost-total-label">Cumulative estimated AI cost</div>
                <div class="ai-cost-total-value">{format_cost(total_micros / AI_COST_SCALE)}</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_ai_cost_history(target: object) -> None:
    history = normalize_ai_cost_history(st.session_state.get("ai_cost_history", []))
    if not history:
        target.caption("The last 10 AI calls will appear here as Skim creates them.")
        return

    rows = []
    for entry in reversed(history):
        occurred_at = datetime.fromtimestamp(
            int(entry["at"]) / 1000,
            tz=timezone.utc,
        ).astimezone(EASTERN_STANDARD_TIME)
        rows.append(
            "<tr>"
            f"<td>{html.escape(occurred_at.strftime('%b %d · %I:%M %p EST').replace(' 0', ' '))}</td>"
            f"<td>{html.escape(str(entry['label']))}</td>"
            f"<td>{html.escape(str(entry['model']))}</td>"
            f"<td>{format_cost(int(entry['cost_micros']) / AI_COST_SCALE)}</td>"
            "</tr>"
        )
    target.markdown(
        '<div class="ai-call-table-wrap"><table class="ai-call-table">'
        "<thead><tr><th>Time</th><th>AI call</th><th>Model</th><th>Cost</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>",
        unsafe_allow_html=True,
    )


def settings_signature(
    selected_topics: list[str],
    include_aggregators: bool,
    include_gdelt: bool,
    include_social: bool,
    keywords: tuple[str, ...],
) -> tuple:
    return (
        tuple(selected_topics),
        include_aggregators,
        include_gdelt,
        include_social,
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


def ai_cost_event_token(event_id: str) -> str:
    return hashlib.sha256(event_id.encode("utf-8")).hexdigest()[:16]


def ai_cost_event_details(event_id: str, article_count: int) -> tuple[str, str]:
    normalized = event_id.lower()
    if normalized.startswith("question-"):
        return "Story question", OPENAI_DEEP_MODEL
    if normalized.startswith("research-"):
        return "Research explainer", OPENAI_DEEP_MODEL
    if normalized.startswith("deep-"):
        return "Deep analysis", OPENAI_DEEP_MODEL
    if normalized.startswith("headline-"):
        return "Story brief", OPENAI_SUMMARY_MODEL
    if article_count > 1:
        return f"{article_count} story briefs", OPENAI_SUMMARY_MODEL
    return "Story brief", OPENAI_SUMMARY_MODEL


def merge_ai_cost_history(*histories: object) -> list[dict[str, object]]:
    merged: dict[str, dict[str, object]] = {}
    for history in histories:
        for entry in normalize_ai_cost_history(history):
            token = str(entry["token"])
            merged.pop(token, None)
            merged[token] = entry
    return list(merged.values())[-AI_COST_HISTORY_LIMIT:]


def accumulate_ai_cost(
    total_micros: int,
    recorded_event_tokens: Iterable[str],
    event_id: str,
    event_cost: float,
) -> tuple[int, bool]:
    token = ai_cost_event_token(event_id) if event_id else ""
    if not token or token in set(recorded_event_tokens) or event_cost <= 0:
        return total_micros, False
    return total_micros + round(event_cost * AI_COST_SCALE), True


def record_batch_ai_cost(
    batch: Sequence[PreparedStory],
    refresh_key: str,
    attempted_ai_cost: float,
    attempted_articles: int | None = None,
) -> None:
    if configured_ai_provider() != "openai":
        return
    batch_cost = max(0.0, attempted_ai_cost)
    stored_ledger = read_ai_cost_ledger()
    recorded_events = list(
        dict.fromkeys(
            [
                *stored_ledger["events"],
                *st.session_state.ai_cost_recorded_events,
            ]
        )
    )
    starting_total = max(
        int(stored_ledger["total_micros"]),
        int(st.session_state.ai_cost_total_micros),
    )
    total_micros, changed = accumulate_ai_cost(
        starting_total,
        recorded_events,
        refresh_key,
        batch_cost,
    )
    if not changed:
        return
    article_count = attempted_articles if attempted_articles is not None else len(batch)
    article_count = max(1, int(article_count))
    latest_micros = round(batch_cost * AI_COST_SCALE)
    st.session_state.ai_cost_total_micros = total_micros
    st.session_state.ai_cost_latest_micros = latest_micros
    st.session_state.ai_cost_total_articles = (
        max(
            int(stored_ledger["total_articles"]),
            int(st.session_state.ai_cost_total_articles),
        )
        + article_count
    )
    st.session_state.ai_cost_latest_articles = article_count
    st.session_state.ai_cost_updated_at = round(datetime.now(timezone.utc).timestamp() * 1000)
    event_token = ai_cost_event_token(refresh_key)
    recorded_events.append(event_token)
    st.session_state.ai_cost_recorded_events = list(dict.fromkeys(recorded_events))[
        -AI_COST_MAX_RECORDED_EVENTS:
    ]
    event_label, event_model = ai_cost_event_details(refresh_key, article_count)
    history_entry = {
        "token": event_token,
        "at": st.session_state.ai_cost_updated_at,
        "cost_micros": latest_micros,
        "articles": article_count,
        "label": event_label,
        "model": event_model,
    }
    st.session_state.ai_cost_history = merge_ai_cost_history(
        stored_ledger.get("history", []),
        st.session_state.get("ai_cost_history", []),
        [history_entry],
    )
    persist_ai_cost_state()


def batch_refreshed_label() -> str:
    refreshed_at = parse_iso_datetime(str(st.session_state.get("batch_refreshed_at", "")))
    if not refreshed_at:
        return ""
    eastern_time = refreshed_at.astimezone(EASTERN_STANDARD_TIME)
    formatted = eastern_time.strftime("%b %d, %Y at %I:%M %p %Z")
    return formatted.replace(" 0", " ").replace(" at 0", " at ")


def render_headline_legend() -> None:
    label = batch_refreshed_label()
    updated_text = f"Headlines updated as of {label}" if label else "Headlines updating now"
    pills = "".join(
        '<span class="category-legend-pill" '
        f'style="--legend-color:{html.escape(color, quote=True)}">'
        f"{html.escape(category)}</span>"
        for category, color in CATEGORY_COLORS.items()
    )
    st.markdown(
        '<div class="headline-legend">'
        f'<div class="headline-updated">{html.escape(updated_text)}</div>'
        f'<div class="category-legend">{pills}</div>'
        "</div>",
        unsafe_allow_html=True,
    )


def ranked_item_is_available(item: RankedStory, blocked_cluster_keys: set[str]) -> bool:
    return item.cluster_key not in blocked_cluster_keys


def current_batch_from_keys(
    ranked_stories: list[RankedStory],
    keyword_rankings: dict[str, list[RankedStory]],
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
        if item and ranked_item_is_available(item, set()):
            current.append(item)
    return current


def prepare_ranked_story(
    item: RankedStory,
    detail: int,
    refresh_key: str,
    plain_language: bool = True,
) -> tuple[PreparedStory | None, float]:
    stored_candidates = list(item.article_candidates or (item.story,))
    primary_candidates: list[Story] = [item.story]
    primary_candidates.extend(
        candidate for candidate in stored_candidates if candidate.id != item.story.id
    )
    primary_candidates = deduplicate_stories(primary_candidates)
    total_ai_cost = 0.0

    def try_candidate(candidate: Story) -> PreparedStory | None:
        nonlocal total_ai_cost
        evidence = fetch_article_evidence(candidate.link, candidate.title)
        if not evidence:
            evidence = feed_story_evidence(candidate)
        if not evidence:
            return None
        attempt = smart_summarize(
            item.story,
            evidence,
            detail,
            refresh_key,
            plain_language,
            candidate,
        )
        total_ai_cost += attempt.ai_cost
        if not attempt.card:
            return None
        return PreparedStory(
            ranked_story=item,
            evidence=evidence,
            card=attempt.card,
            article_story=candidate,
        )

    # Try the selected publisher and two strong cluster alternatives first. If those
    # pages are blocked, fan out through the publishers hidden in Google News.
    first_pass = primary_candidates[:3]
    for candidate in first_pass:
        prepared = try_candidate(candidate)
        if prepared:
            return prepared, total_ai_cost

    discovered_candidates = fetch_google_news_briefing_candidates(
        item.story.title,
        item.story.topics,
    )
    fallback_candidates = deduplicate_stories(
        [*discovered_candidates, *primary_candidates[3:]]
    )
    attempted_urls = {normalized_story_url(candidate.link) for candidate in first_pass}
    for candidate in fallback_candidates:
        if normalized_story_url(candidate.link) in attempted_urls:
            continue
        attempted_urls.add(normalized_story_url(candidate.link))
        prepared = try_candidate(candidate)
        if prepared:
            return prepared, total_ai_cost
    return None, total_ai_cost


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
    detail: int,
    on_story: Callable[[PreparedStory, int], None] | None = None,
) -> list[PreparedStory]:
    if not configured_ai_provider():
        return []

    prune_shown_cluster_history()
    shown_cluster_keys = set(st.session_state.shown_cluster_history)
    current = current_batch_from_keys(ranked_stories, keyword_rankings)
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
        if ranked_item_is_available(item, shown_cluster_keys | used_cluster_keys):
            prepared, attempt_cost = prepare_ranked_story(item, detail, refresh_key)
            attempted_ai_cost += attempt_cost
            if prepared:
                append_prepared_story(batch, prepared, on_story)
                used_cluster_keys.add(item.cluster_key)

    for keyword in keyword_rankings:
        for item in keyword_rankings[keyword][:MAX_KEYWORD_CANDIDATES]:
            if ranked_item_is_available(item, shown_cluster_keys | used_cluster_keys):
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


def append_keyword_headlines(
    batch: list[RankedStory],
    keyword_rankings: dict[str, list[RankedStory]],
    blocked_cluster_keys: set[str],
) -> list[RankedStory]:
    combined = list(batch)
    used_cluster_keys = {item.cluster_key for item in combined}
    for keyword_items in keyword_rankings.values():
        for item in keyword_items:
            if item.cluster_key in blocked_cluster_keys | used_cluster_keys:
                continue
            combined.append(item)
            used_cluster_keys.add(item.cluster_key)
            break
    return combined


def headline_popularity_key(item: RankedStory) -> tuple[float, int, int, float]:
    return (
        item.score,
        item.references,
        item.topic_story_count,
        ranked_story_published_timestamp(item),
    )


def select_balanced_headlines(
    ranked_stories: Sequence[RankedStory],
    blocked_cluster_keys: set[str],
    limit: int = BATCH_SIZE,
) -> list[RankedStory]:
    if limit <= 0:
        return []
    eligible = [
        item
        for item in ranked_stories
        if item.story.group != "Custom" and item.cluster_key not in blocked_cluster_keys
    ]
    selected: list[RankedStory] = []
    selected_cluster_keys: set[str] = set()

    for category in CATEGORY_COLORS:
        category_candidates = [
            item
            for item in eligible
            if item.cluster_key not in selected_cluster_keys
            and story_category(item.story) == category
        ]
        if not category_candidates:
            continue
        strongest = max(category_candidates, key=headline_popularity_key)
        selected.append(strongest)
        selected_cluster_keys.add(strongest.cluster_key)
        if len(selected) >= limit:
            return selected

    for item in sorted(eligible, key=headline_popularity_key, reverse=True):
        if len(selected) >= limit:
            break
        if item.cluster_key in selected_cluster_keys:
            continue
        selected.append(item)
        selected_cluster_keys.add(item.cluster_key)
    return selected


def build_headline_batch(
    ranked_stories: list[RankedStory],
    keyword_rankings: dict[str, list[RankedStory]] | None = None,
) -> list[RankedStory]:
    keyword_rankings = keyword_rankings or {}
    prune_shown_cluster_history()
    current = current_batch_from_keys(ranked_stories, keyword_rankings)
    if current:
        return sort_headlines_by_age(current)

    shown_cluster_keys = set(st.session_state.shown_cluster_history)
    batch = select_balanced_headlines(ranked_stories, shown_cluster_keys)
    batch = append_keyword_headlines(batch, keyword_rankings, shown_cluster_keys)
    batch = sort_headlines_by_age(batch)
    refresh_key = utc_now().isoformat()
    st.session_state.current_cluster_keys = [item.cluster_key for item in batch]
    mark_batch_shown(batch, refresh_key)
    return batch


def render_keyword_boosters() -> None:
    st.markdown("**Keyword boosters**")
    st.caption(
        "Each saved keyword adds its strongest current story and stays locked until you remove it."
    )
    for row_index in range(3):
        cols = st.columns(3, gap="small")
        for col_index, col in enumerate(cols):
            keyword_index = (row_index * 3) + col_index
            saved_keyword = clean_text(
                str(st.session_state.get(f"saved_keyword_{keyword_index}", ""))
            )
            with col:
                with st.container(key=f"keyword_slot_{keyword_index}"):
                    if saved_keyword:
                        keyword_col, clear_col = st.columns(
                            [5, 1],
                            gap="small",
                            vertical_alignment="center",
                        )
                        with keyword_col:
                            st.markdown(
                                '<div class="keyword-chip-text '
                                f'keyword-color-{keyword_index}">'
                                f"{html.escape(saved_keyword)}</div>",
                                unsafe_allow_html=True,
                            )
                        with clear_col:
                            st.button(
                                "×",
                                key=f"clear_keyword_{keyword_index}",
                                help=f"Remove {saved_keyword}",
                                on_click=clear_keyword_slot,
                                args=(keyword_index,),
                            )
                    else:
                        st.text_input(
                            f"Keyword {keyword_index + 1}",
                            key=f"keyword_draft_{keyword_index}",
                            placeholder="Add keyword",
                            label_visibility="collapsed",
                            on_change=lock_keyword_slot,
                            args=(keyword_index,),
                        )
    persist_keywords_to_query_params()


def render_admin(errors: Sequence[str], batch_size: int) -> None:
    with st.expander("Admin", expanded=False):
        password = st.text_input(
            "Admin password",
            type="password",
            key="admin_password",
            placeholder="Password",
        )
        if password != ADMIN_PASSWORD:
            st.markdown(
                '<div class="admin-lock-copy">Enter the Admin password to manage Skim.</div>',
                unsafe_allow_html=True,
            )
            if password:
                st.error("Incorrect password.")
            return

        st.metric("Headlines", batch_size)

        if errors:
            st.markdown("**Feed notes**")
            for error in errors[:12]:
                st.write(error)

        st.markdown("### Customize")
        st.multiselect(
            "Topics",
            options=list(TOPICS.keys()),
            key="selected_topics",
        )
        col1, col2 = st.columns(2)
        with col1:
            st.slider("Summary depth", min_value=1, max_value=5, step=1, key="detail")
            st.toggle(
                "Plain-language summaries",
                key="plain_language_summaries",
                help=(
                    "On writes about 20% more simply, using shorter sentences, familiar words, "
                    "and quick explanations for necessary jargon."
                ),
            )
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
            if st.button("Clear 24-hour history", use_container_width=True):
                st.session_state.shown_cluster_history = {}
                st.session_state.current_cluster_keys = []
                st.rerun()

        render_keyword_boosters()
        st.caption(
            "The main briefing favors independent outlet confirmation, fast coverage growth, "
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
                to find stories, reads the publisher article, and uses OpenAI for its briefings.
            </p>
            """,
            unsafe_allow_html=True,
        )
        render_ai_cost_summary(st)
        st.markdown("**Last 10 AI calls**")
        render_ai_cost_history(st)

        st.markdown("**All-time AI cost ledger**")
        st.caption(
            "This is Skim's cumulative estimate from API usage returned by OpenAI. "
            "It is stored in the app and backed up in this browser."
        )
        current_dollars = int(st.session_state.ai_cost_total_micros) / AI_COST_SCALE
        st.session_state.setdefault("admin_ai_cost_dollars", current_dollars)
        st.number_input(
            "Set cumulative cost ($)",
            min_value=0.0,
            step=0.01,
            format="%.4f",
            key="admin_ai_cost_dollars",
        )
        set_col, reset_col = st.columns(2)
        with set_col:
            if st.button("Set total", use_container_width=True):
                set_ai_cost_total(float(st.session_state.admin_ai_cost_dollars))
                st.rerun()
        with reset_col:
            if st.button("Reset to $0", use_container_width=True):
                set_ai_cost_total(0.0, reset_history=True)
                st.session_state.admin_ai_cost_dollars = 0.0
                st.rerun()


def main() -> None:
    st.set_page_config(page_title=APP_NAME, page_icon="S", layout="centered")
    page_style()

    if "current_cluster_keys" not in st.session_state:
        st.session_state.current_cluster_keys = []
    if "last_settings" not in st.session_state:
        st.session_state.last_settings = None
    if "deep_analyses" not in st.session_state:
        st.session_state.deep_analyses = {}
    if "research_briefs" not in st.session_state:
        st.session_state.research_briefs = {}
    if "story_questions" not in st.session_state:
        st.session_state.story_questions = {}
    if "prepared_summary_results" not in st.session_state:
        st.session_state.prepared_summary_results = {}
    if "extracting_story_id" not in st.session_state:
        st.session_state.extracting_story_id = ""
    if "deep_analysis_loading_story_id" not in st.session_state:
        st.session_state.deep_analysis_loading_story_id = ""
    if "pinned_story_id" not in st.session_state:
        st.session_state.pinned_story_id = ""
    if "scroll_to_top_pending" not in st.session_state:
        st.session_state.scroll_to_top_pending = False
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
    st.session_state.setdefault("plain_language_summaries", True)
    st.session_state.setdefault("include_social", False)
    st.session_state.setdefault("include_aggregators", True)
    st.session_state.setdefault("include_gdelt", False)
    initialize_keyword_state()
    query_had_cost_ledger = AI_COST_QUERY_TOTAL in st.query_params
    initialize_ai_cost_state()
    sync_ai_cost_browser_storage(query_had_cost_ledger)

    render_header()
    if st.session_state.scroll_to_top_pending:
        st.session_state.scroll_to_top_pending = False
        scroll_page_to_top()

    batch_timestamp_slot = st.empty()
    story_stream = st.container(key="headline_feed")

    selected_topics = st.session_state.selected_topics
    detail = st.session_state.detail
    plain_language = bool(st.session_state.plain_language_summaries)
    include_social = st.session_state.include_social
    include_aggregators = st.session_state.include_aggregators
    include_gdelt = st.session_state.include_gdelt
    keywords = custom_keywords()

    current_settings = settings_signature(
        selected_topics,
        include_aggregators,
        include_gdelt,
        include_social,
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
        keyword_rankings, keyword_errors = fetch_keyword_rankings(keywords)
        errors.extend(keyword_errors)

    batch = build_headline_batch(ranked_stories, keyword_rankings)
    pinned_story_id = st.session_state.pinned_story_id
    pinned_items = [item for item in batch if item.story.id == pinned_story_id]
    if pinned_story_id and not pinned_items:
        st.session_state.pinned_story_id = ""
        st.session_state.extracting_story_id = ""
    batch_to_render = [
        *pinned_items,
        *(item for item in batch if item.story.id != pinned_story_id),
    ]
    st.session_state.generation_issues = []
    with story_stream:
        for ranked_story in batch_to_render:
            render_headline_story(ranked_story, detail, plain_language)
        if batch and st.button(
            "Load 20 More Headlines",
            key="load-more-headlines",
            icon=":material/add:",
            help="Show the next unseen 20 headlines from the current discovery pool.",
            use_container_width=True,
        ):
            load_next_story_batch()
            st.rerun()

    with batch_timestamp_slot.container():
        render_headline_legend()

    errors.extend(st.session_state.generation_issues)
    if not batch:
        st.info(
            f"No new headlines are available under the {NO_REPEAT_HOURS}-hour no-repeat rule. "
            "Open Admin to broaden topics or clear the history."
        )
    render_admin(errors, len(batch))


if __name__ == "__main__":
    main()
