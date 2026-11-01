# 🗞️ AI Daily Digest

**Weekly AI news digest delivered to your Gmail — powered by OpenAI Agents SDK.**

An AI agent searches the web for the week's most important AI developments, curates the top 5 stories, and writes career-relevant summaries — all delivered as a clean HTML email every Monday.

## How it works

```
🔍 Researcher Agent (WebSearchTool)  →  searches the web for AI news
        ↓
✍️ Curator Agent (structured output)  →  picks top 5, writes summaries
        ↓
📧 Gmail SMTP                         →  sends a beautiful HTML digest
```

### Two-Agent Pipeline

1. **Researcher Agent** — equipped with OpenAI's hosted `WebSearchTool`, it runs multiple searches to find 12-15 AI news items from the past week (new LLMs, open-source tools, frameworks, breakthroughs)
2. **Curator Agent** — receives the raw findings and selects the top 5 most important items, writing crisp summaries tailored for early-career AI engineers. Each item includes a "Quick Take" with an actionable suggestion.

## Setup

### 1. GitHub Secrets (required)

| Secret | Description |
|--------|-------------|
| `OPENAI_API_KEY` | Your OpenAI API key (needs access to `gpt-4o-mini` and web search) |
| `SMTP_EMAIL` | Your Gmail address |
| `SMTP_PASSWORD` | Gmail App Password ([create one here](https://myaccount.google.com/apppasswords)) |
| `RECIPIENT_EMAIL` | Where to send the digest |

### 2. Optional Secrets

| Secret | Description |
|--------|-------------|
| `OPENAI_BASE_URL` | Custom API endpoint (for proxies like AIPipe, Azure, etc.) |
| `GIT_PAT` | Personal access token for pushing `sent_ids.json` updates |

### 3. Configuration (environment variables)

| Variable | Default | Description |
|----------|---------|-------------|
| `MAX_ITEMS` | `5` | Number of items in each digest |
| `MAX_AGE_DAYS` | `7` | How far back to search |
| `DEBUG_MODE` | `false` | Dump HTML to the Action log |

## Schedule

Runs every **Monday at 8:00 AM UTC** via GitHub Actions. You can also trigger manually from the Actions tab.

## Dependencies

```
openai-agents    # OpenAI Agents SDK (includes WebSearchTool)
pydantic         # Structured output schemas
jinja2           # HTML email templating
python-dotenv    # Environment variable loading
```

## License

Personal project — use however you like.
