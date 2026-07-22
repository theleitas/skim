# Skim

Skim is a personal Streamlit news app built around fast headlines, compact summaries, sharing, archiving, and lightweight customization.

## Run locally

```bash
python3 -m streamlit run app.py
```

## Tokens

This first version is token-free:

- No OpenAI API key.
- No paid news API key.
- No Reddit API token.
- No X API token.

It uses public RSS feeds and a local summarizer. X is listed as a future integration because useful official X API access usually requires a developer account and paid access.

## Free sources in this version

- BBC RSS
- NPR RSS
- The Guardian RSS
- Al Jazeera RSS
- New York Times RSS
- CNN RSS
- ABC News RSS
- CBS News RSS
- Google News RSS
- Reddit RSS
- Hacker News RSS

## GitHub setup

The local GitHub CLI currently needs a fresh login:

```bash
gh auth login -h github.com
```

After that, the app can be committed, pushed, and connected to a GitHub repo.
