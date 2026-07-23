# Skim

Skim is a personal Streamlit news app built around fast headlines, compact summaries, sharing, archiving, and lightweight customization.

## Run locally

```bash
python3 -m streamlit run app.py
```

## OpenAI setup

Skim runs without any AI API key, but OpenAI is the preferred hosted AI path:

- GPT-5.6 Luna writes the normal story summary, Background, lesson, and learning links.
- GPT-5.6 Terra runs only when you click a story's Deep analysis button.
- Article summaries regenerate for each new 20-story refresh so Background stays fresh; repeated reruns of the same batch reuse cached results.

Create an OpenAI API key, then set it before starting Streamlit locally:

```bash
export OPENAI_API_KEY="your_openai_key_here"
python3 -m streamlit run app.py
```

On Streamlit Community Cloud, open the app settings, add this to Secrets, and reboot the app:

```toml
OPENAI_API_KEY = "your_openai_key_here"
SKIM_AI_PROVIDER = "openai"
```

Do not put API keys in GitHub.

Skim's auto provider order is OpenAI, Gemini, Groq, then xAI. You can force OpenAI with:

```toml
SKIM_AI_PROVIDER = "openai"
```

Optional fallback keys:

```toml
GEMINI_API_KEY = "your_gemini_key_here"
GROQ_API_KEY = "your_groq_key_here"
XAI_API_KEY = "your_xai_key_here"
```

OpenAI cost estimates are shown on each article card when OpenAI is active. They are estimates, not exact billing records.

## Tokens

The news sources are token-free and use public RSS feeds. Without an AI key, Skim uses its local summarizer. X is listed as a future integration because useful official X API access usually requires a developer account and paid access.

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
