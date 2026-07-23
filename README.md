# Skim

Skim is a personal Streamlit news app built around fast headlines, compact summaries, sharing, archiving, and lightweight customization.

## Run locally

```bash
python3 -m streamlit run app.py
```

## Optional OpenAI setup

Skim runs without OpenAI, but it can use your OpenAI API account for smarter summaries:

- Luna (`gpt-5.6-luna`) writes the normal story summary, context, lesson, and learning links.
- Terra (`gpt-5.6-terra`) runs only when you click a story's Deeper analysis button.
- OpenAI responses are cached for 24 hours so normal refreshes do not rebill the same story analysis.

Locally, set the key before starting Streamlit:

```bash
export OPENAI_API_KEY="your_api_key_here"
python3 -m streamlit run app.py
```

On Streamlit Community Cloud, open the app settings, add this to Secrets, and reboot the app:

```toml
OPENAI_API_KEY = "your_api_key_here"
```

Do not put your API key in GitHub. API usage is charged to the OpenAI account/project that owns the key.

## Tokens

The news sources are still token-free and use public RSS feeds. Without `OPENAI_API_KEY`, Skim uses its local summarizer. X is listed as a future integration because useful official X API access usually requires a developer account and paid access.

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
