# Skim

Skim is a personal Streamlit news app built around grounded AI summaries, clean headlines, sharing, archiving, and lightweight customization.

## Run locally

```bash
python3 -m streamlit run app.py
```

## OpenAI setup

Skim can discover feeds without an AI key, but publishing cards requires an AI provider. OpenAI is the preferred path:

- Skim decodes aggregator links, opens the publisher page, and extracts the main article body before asking OpenAI to write anything.
- GPT-5.6 Terra writes the display headline, story summary, and Background from that article text.
- A strict quality check rejects promotional fragments, meta commentary, abrupt headlines, generic Background text, and incomplete prose. One AI repair is attempted before the candidate is skipped.
- GPT-5.6 Terra runs only when you click a story's Deep analysis button.
- A refresh contains 15 main stories plus one additional story for every saved keyword.
- Stories do not repeat for 24 hours. Repeated reruns of the same batch reuse cached extraction and AI results.

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

Skim's auto provider order is OpenAI, Gemini, Groq, then xAI. OpenAI is recommended and can be forced with:

```toml
SKIM_AI_PROVIDER = "openai"
```

Optional fallback keys:

```toml
GEMINI_API_KEY = "your_gemini_key_here"
GROQ_API_KEY = "your_groq_key_here"
XAI_API_KEY = "your_xai_key_here"
```

When OpenAI is active, Skim uses the token usage returned by the API and the current
model prices to show:

- The estimated AI cost of the latest feed, including repair attempts and candidates that fail the quality gate.
- A persistent estimated all-time feed cost, starting with the first batch generated after the counter is enabled.
- The estimated cost of each published card and the likely cost of optional Deep analysis.

The cumulative counter is stored in the app URL alongside persistent keywords, so browser
refreshes and ordinary Streamlit reruns do not erase it or count the same batch twice. It
is an app-side estimate, not a replacement for the OpenAI billing dashboard.

## Tokens

The news discovery sources and article extractor are token-free. An AI key is required to publish cards because Skim no longer falls back to canned local prose. X is listed as a future integration because useful official X API access usually requires a developer account and paid access.

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
