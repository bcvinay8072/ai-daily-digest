#!/usr/bin/env python3
# -----------------------------------------------------------------
# Weekly AI Digest – OpenAI Agents SDK version
# Two-agent pipeline: Researcher (WebSearchTool) → Curator (LLM)
# -----------------------------------------------------------------
import os
import json
import hashlib
import datetime
import ssl
import smtplib

from pydantic import BaseModel, Field
from agents import Agent, Runner, WebSearchTool
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from jinja2 import Template
from dotenv import load_dotenv

# -----------------------------------------------------------------
# Load .env (non-secret configuration)
# -----------------------------------------------------------------
load_dotenv()

# -------------------- CONFIGURABLE OPTIONS -----------------------
MAX_ITEMS    = int(os.getenv("MAX_ITEMS", "5"))
MAX_AGE_DAYS = int(os.getenv("MAX_AGE_DAYS", "7"))
DEBUG_MODE   = os.getenv("DEBUG_MODE", "false").lower() == "true"
# -----------------------------------------------------------------

# =================================================================
# STRUCTURED OUTPUT SCHEMAS (Pydantic)
# =================================================================

class ResearchItem(BaseModel):
    """A single news item found by the Researcher agent."""
    title: str = Field(description="Headline of the news item")
    url: str = Field(description="Direct URL to the article or announcement")
    source: str = Field(description="Publication or platform name (e.g. 'TechCrunch', 'GitHub', 'arXiv')")
    one_liner: str = Field(description="One-sentence factual summary of the news (max 20 words)")


class ResearchResult(BaseModel):
    """The full output from the Researcher agent."""
    items: list[ResearchItem] = Field(
        description="List of 12-15 AI news items found from the past week"
    )


class DigestItem(BaseModel):
    """A single curated item for the final email digest."""
    title: str = Field(description="Clean, concise headline")
    url: str = Field(description="Direct URL to the article or announcement")
    source: str = Field(description="Publication or platform name")
    summary: str = Field(description="2-3 sentence summary: what happened and why it matters for an early-career AI engineer")
    quick_take: str = Field(description="One actionable sentence starting with an emoji — what the reader should do or know")


class DigestResult(BaseModel):
    """The full output from the Curator agent."""
    week_tldr: str = Field(description="One sentence summarizing the entire week in AI (max 25 words)")
    items: list[DigestItem] = Field(description=f"Top {MAX_ITEMS} curated items, ranked by importance")


# =================================================================
# AGENT DEFINITIONS
# =================================================================

today_str = datetime.date.today().isoformat()
week_ago = (datetime.date.today() - datetime.timedelta(days=MAX_AGE_DAYS)).isoformat()

researcher_agent = Agent(
    name="AI News Researcher",
    instructions=f"""You are an AI news researcher. Your job is to find the most important
AI developments from the past week ({week_ago} to {today_str}).

Search for news across these categories:
1. **New LLM releases & updates** — new models, benchmarks, capabilities
2. **Open-source tools & frameworks** — new libraries, major releases, useful dev tools
3. **Major industry moves** — acquisitions, partnerships, policy changes that matter
4. **Research breakthroughs** — important papers, new techniques

Search strategy:
- Run multiple searches covering different angles (e.g. "new LLM release this week",
  "open source AI tools November 2026", "AI research breakthroughs this week")
- Focus on REAL, SPECIFIC news — not evergreen articles or listicles
- Prefer primary sources (official blogs, GitHub repos, arXiv) over secondary coverage
- Find 12-15 items so the Curator has enough to choose from
- Every item MUST have a real, working URL

Do NOT include:
- Opinion pieces or editorials
- "What is AI?" style explainers
- News older than {MAX_AGE_DAYS} days
- Duplicate stories (same news from different outlets — pick the best source)""",
    tools=[WebSearchTool(search_context_size="high")],
    output_type=ResearchResult,
    model="gpt-4o-mini",
)

curator_agent = Agent(
    name="AI Digest Curator",
    instructions=f"""You are an AI digest curator writing for an early-career AI/ML engineer.

You will receive a list of AI news items found by a researcher. Your job:

1. **Select the top {MAX_ITEMS} most important items** — prioritize:
   - New LLM releases (GPT, Claude, Gemini, Llama, Mistral, open-weight models)
   - Genuinely useful open-source tools and frameworks
   - Breakthroughs that change how people build with AI
   - Things an early-career engineer should know about to stay competitive

2. **Write a crisp summary** for each (2-3 sentences):
   - What happened (factual)
   - Why it matters for someone starting their AI career
   - Keep it jargon-free; explain technical terms briefly if needed

3. **Add a Quick Take** for each — one actionable sentence:
   - Start with a relevant emoji
   - Tell the reader what to DO: try it, read the paper, watch the demo, bookmark it
   - Example: "🔧 Try it out — pip install and build your first agent in 10 minutes"

4. **Write a week_tldr** — one sentence capturing the week's biggest theme

IMPORTANT:
- Be concise. No fluff, no hype.
- If an item isn't genuinely useful to an early-career engineer, drop it.
- Rank by importance (most important first).
- Preserve the exact URLs from the research — do NOT modify or make up URLs.""",
    output_type=DigestResult,
    model="gpt-4o-mini",
)

# =================================================================
# SENT-IDS PERSISTENCE (cross-run dedup)
# =================================================================
SENT_IDS_PATH = "sent_ids.json"
MAX_SENT_IDS = 2000  # auto-prune to prevent unbounded growth


def load_sent_ids() -> set:
    """Return a set of URLs that have already been mailed."""
    if not os.path.isfile(SENT_IDS_PATH):
        return set()
    try:
        return set(json.load(open(SENT_IDS_PATH)))
    except Exception:
        return set()


def save_sent_ids(ids: set):
    """Write the set of URLs back to sent_ids.json, pruning if needed."""
    id_list = list(ids)
    # Keep only the most recent MAX_SENT_IDS entries
    if len(id_list) > MAX_SENT_IDS:
        id_list = id_list[-MAX_SENT_IDS:]
    json.dump(id_list, open(SENT_IDS_PATH, "w"))


# =================================================================
# HTML EMAIL TEMPLATE
# =================================================================
TEMPLATE_STR = """\
<html>
<head>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body {
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      margin: 0; padding: 0; background: #f4f4f5;
    }
    .container {
      max-width: 600px; margin: 0 auto; padding: 24px 16px;
    }
    .header {
      background: linear-gradient(135deg, #1e3a5f 0%, #0d6efd 100%);
      color: white; padding: 24px; border-radius: 12px 12px 0 0;
      text-align: center;
    }
    .header h1 { margin: 0; font-size: 22px; }
    .header .date { font-size: 13px; opacity: 0.85; margin-top: 4px; }
    .tldr {
      background: #e8f0fe; padding: 14px 18px; border-radius: 0 0 12px 12px;
      font-size: 15px; color: #1a1a1a; line-height: 1.5;
      border-left: 4px solid #0d6efd; margin-bottom: 20px;
    }
    .tldr strong { color: #0d6efd; }
    .item {
      background: white; border-radius: 10px; padding: 16px 18px;
      margin-bottom: 12px; box-shadow: 0 1px 3px rgba(0,0,0,0.06);
    }
    .item-number {
      display: inline-block; background: #0d6efd; color: white;
      width: 24px; height: 24px; border-radius: 50%; text-align: center;
      line-height: 24px; font-size: 13px; font-weight: bold; margin-right: 8px;
    }
    .item-title {
      font-weight: 600; font-size: 16px; color: #1a1a1a;
      text-decoration: none;
    }
    .item-title:hover { color: #0d6efd; }
    .item-source {
      font-size: 12px; color: #888; margin-top: 3px;
    }
    .item-summary {
      font-size: 14px; color: #333; line-height: 1.55; margin-top: 8px;
    }
    .quick-take {
      font-size: 13px; color: #0d6efd; margin-top: 8px;
      padding: 8px 12px; background: #f0f7ff; border-radius: 6px;
      font-weight: 500;
    }
    .footer {
      text-align: center; font-size: 12px; color: #999;
      margin-top: 20px; padding: 12px;
    }
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <h1>🗞️ Weekly AI Digest</h1>
      <div class="date">{{ date }} · Top {{ items|length }} stories</div>
    </div>

    <div class="tldr">
      <strong>TL;DR:</strong> {{ week_tldr }}
    </div>

    {% for it in items %}
    <div class="item">
      <span class="item-number">{{ loop.index }}</span>
      <a class="item-title" href="{{ it.url }}" target="_blank">{{ it.title }}</a>
      <div class="item-source">{{ it.source }}</div>
      <div class="item-summary">{{ it.summary }}</div>
      <div class="quick-take">{{ it.quick_take }}</div>
    </div>
    {% endfor %}

    <div class="footer">
      You're receiving this because you subscribed to AI Digest.<br>
      Powered by OpenAI Agents SDK · Built with ❤️
    </div>
  </div>
</body>
</html>
"""


# =================================================================
# MAIN PIPELINE
# =================================================================
def main():
    print(f"📅 Running Weekly AI Digest for {today_str}")
    print(f"   Looking back {MAX_AGE_DAYS} days, selecting top {MAX_ITEMS} items\n")

    # ----- 1️⃣ Research: Agent searches the web -----
    print("🔍 Step 1: Researcher agent searching the web...")
    research_result = Runner.run_sync(
        researcher_agent,
        f"Find the most important AI news from {week_ago} to {today_str}. "
        f"Focus on: new LLM releases, useful open-source tools & frameworks, "
        f"and major breakthroughs. Find 12-15 items.",
    )
    raw_items: ResearchResult = research_result.final_output
    print(f"   Found {len(raw_items.items)} items\n")

    if DEBUG_MODE:
        print("   === DEBUG: Raw research items ===")
        for item in raw_items.items:
            print(f"   - {item.title} ({item.source})")
        print()

    # ----- 2️⃣ Dedup: remove already-sent URLs -----
    already_sent = load_sent_ids()
    fresh_items = [it for it in raw_items.items if it.url not in already_sent]
    print(f"🧹 Step 2: {len(fresh_items)} items after dedup ({len(raw_items.items) - len(fresh_items)} already sent)\n")

    if not fresh_items:
        print("⚠️  No new items to send — skipping this week.")
        return

    # ----- 3️⃣ Curate: Agent selects and writes summaries -----
    print("✍️  Step 3: Curator agent writing digest...")
    items_text = "\n".join(
        f"- Title: {it.title}\n  URL: {it.url}\n  Source: {it.source}\n  Summary: {it.one_liner}"
        for it in fresh_items
    )
    curate_result = Runner.run_sync(
        curator_agent,
        f"Here are the AI news items from this week. Select the top {MAX_ITEMS} "
        f"and write the digest:\n\n{items_text}",
    )
    digest: DigestResult = curate_result.final_output
    print(f"   Curated {len(digest.items)} items")
    print(f"   TL;DR: {digest.week_tldr}\n")

    # ----- 4️⃣ Build HTML email -----
    print("📧 Step 4: Building email...")
    html = Template(TEMPLATE_STR).render(
        date=today_str,
        week_tldr=digest.week_tldr,
        items=[item.model_dump() for item in digest.items],
    )

    if DEBUG_MODE:
        print("\n===== DEBUG: GENERATED HTML START =====\n")
        print(html)
        print("\n===== DEBUG: GENERATED HTML END =====\n")

    # ----- 5️⃣ Send email via Gmail SMTP -----
    print("📬 Step 5: Sending email...")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🗞️ Weekly AI Digest – {today_str}"
    msg["From"]    = os.getenv("SMTP_EMAIL")
    msg["To"]      = os.getenv("RECIPIENT_EMAIL")
    msg.attach(MIMEText(html, "html"))

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
        server.login(os.getenv("SMTP_EMAIL"), os.getenv("SMTP_PASSWORD"))
        server.send_message(msg)
    print("✅ Mail delivered!\n")

    # ----- 6️⃣ Update sent_ids.json -----
    new_ids = {item.url for item in digest.items}
    save_sent_ids(already_sent | new_ids)
    print(f"💾 Updated sent_ids.json (+{len(new_ids)} URLs)")


if __name__ == "__main__":
    main()
