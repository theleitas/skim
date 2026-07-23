# Skim

Skim is a personal Streamlit news app built around fast headlines, compact summaries, sharing, archiving, and lightweight customization.

## Run locally

```bash
python3 -m streamlit run app.py
```

## Optional free AI setup

Skim runs without any AI API key, but the best free hosted path is Gemini:

- Gemini Flash writes the normal story summary, context, lesson, and learning links.
- Gemini Pro runs only when you click a story's Deeper analysis button.
- AI responses are cached for 24 hours so normal refreshes do not re-request the same story analysis.

Create a Gemini API key in Google AI Studio, then set it before starting Streamlit locally:

```bash
export GEMINI_API_KEY="your_gemini_key_here"
python3 -m streamlit run app.py
```

On Streamlit Community Cloud, open the app settings, add this to Secrets, and reboot the app:

```toml
GEMINI_API_KEY = "your_gemini_key_here"
```

Do not put API keys in GitHub.

Skim's auto provider order is Gemini, Groq, xAI, then OpenAI. You can force one provider with:

```toml
SKIM_AI_PROVIDER = "gemini"
```

Optional alternate keys:

```toml
GROQ_API_KEY = "your_groq_key_here"
XAI_API_KEY = "your_xai_key_here"
OPENAI_API_KEY = "your_openai_key_here"
```

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
