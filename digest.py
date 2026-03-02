#!/usr/bin/env python3
# --------------------------------------------------------------
# Daily AI News Digest – runs on GitHub Actions (or locally)
# --------------------------------------------------------------
import os, ssl, smtplib, datetime, hashlib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import feedparser          # RSS/Atom parser
import requests            # for NewsAPI JSON
from jinja2 import Template
from dotenv import load_dotenv
load_dotenv()               # pulls environment variables from GitHub Secrets

# -------------------------- CONFIG ----------------------------
# Add / remove any RSS/JSON source you like.
FEEDS = [
    "https://techcrunch.com/tag/artificial-intelligence/feed/",
    "https://www.theverge.com/artificial-intelligence/rss/index.xml",
    "https://export.arxiv.org/rss/cs.AI",
    "https://hnrss.org/show",
    "https://www.reddit.com/r/MachineLearning/.rss"
]

# OPTIONAL – NewsAPI (free tier, 500 req/day).  Set NEWS_API_KEY in Secrets.
NEWS_API_KEY = os.getenv("NEWS_API_KEY", "")
if NEWS_API_KEY:
    FEEDS.append(
        f"https://newsapi.org/v2/everything?"
        f"q=artificial+intelligence&language=en&sortBy=publishedAt&"
        f"pageSize=30&apiKey={NEWS_API_KEY}"
    )

MAX_ITEMS = 25               # how many headlines in the e‑mail
# -------------------------------------------------------------

def fetch(src: str):
    """Return a list of dicts: title, link, published, summary."""
    if "newsapi.org" in src:          # JSON endpoint
        data = requests.get(src).json()
        return [{
            "title": a["title"],
            "link": a["url"],
            "published": a.get("publishedAt", ""),
            "summary": a.get("description", "")
        } for a in data.get("articles", [])]

    # RSS / Atom
    d = feedparser.parse(src)
    out = []
    for e in d.entries:
        out.append({
            "title": e.title,
            "link": e.link,
            "published": getattr(e, "published", ""),
            "summary": getattr(e, "summary", "")
        })
    return out


def dedupe(items):
    """Keep only one copy per URL (newest wins)."""
    seen = {}
    for it in items:
        h = hashlib.sha256(it["link"].encode()).hexdigest()
        if h not in seen or it["published"] > seen[h]["published"]:
            seen[h] = it
    return list(seen.values())


def summarize_openai(text: str) -> str:
    """2‑sentence summary using OpenAI GPT‑4o‑mini (cheap)."""
    import openai
    openai.api_key = os.getenv("OPENAI_API_KEY")
    if not openai.api_key:
        return ""                     # no key → skip summarising
    try:
        resp = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[{
                "role": "user",
                "content": f"Summarise in two concise sentences:\n\n{text}"
            }],
            temperature=0.0,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        print("🔺 LLM error:", e)
        return ""


# ------------------------------------------------------------
def build_html(items):
    """Render a clean, mobile‑friendly HTML e‑mail via Jinja2."""
    tmpl = """
    <html>
    <head>
      <style>
        body {font-family:Arial,Helvetica,sans-serif; margin:0; padding:20px;}
        h2 {color:#0d6efd;}
        ul {list-style:none; padding:0;}
        li {margin-bottom:15px;}
        a {color:#0d6efd; text-decoration:none; font-weight:bold;}
        .meta {font-size:0.85em; color:#555;}
        .summary {margin-top:5px;}
        blockquote {border-left:3px solid #ddd; margin:8px 0; padding-left:10px; color:#555;}
      </style>
    </head>
    <body>
      <h2>🧠 Daily AI Digest – {{date}}</h2>
      <ul>
        {% for it in items %}
        <li>
          <a href="{{ it.link }}" target="_blank">{{ it.title }}</a>
          {% if it.published %}<div class="meta">{{ it.published[:10] }}</div>{% endif %}
          {% if it.summary %}<div class="summary">{{ it.summary|safe }}</div>{% endif %}
          {% if it.llm_summary %}<blockquote>{{ it.llm_summary }}</blockquote>{% endif %}
        </li>
        {% endfor %}
      </ul>
      <p style="font-size:0.8em; color:#888;">
        Sent by an automated GitHub Actions job. Edit <code>.env</code> (or GitHub Secrets) to stop.
      </p>
    </body>
    </html>
    """
    return Template(tmpl).render(date=datetime.date.today().isoformat(),
                               items=items)


def send_email(html_body: str):
    """SMTP via Gmail (or any provider that supports SSL)."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🗞️ AI Digest – {datetime.date.today().isoformat()}"
    msg["From"] = os.getenv("SMTP_EMAIL")
    msg["To"] = os.getenv("RECIPIENT_EMAIL")
    msg.attach(MIMEText(html_body, "html"))

    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as srv:
        srv.login(os.getenv("SMTP_EMAIL"), os.getenv("SMTP_PASSWORD"))
        srv.send_message(msg)
    print("✅ Mail delivered")


def main():
    # 1️⃣ Gather all items from every source
    raw = []
    for src in FEEDS:
        try:
            raw.extend(fetch(src))
        except Exception as exc:
            print(f"⚠️  problem fetching {src}: {exc}")

    # 2️⃣ De‑duplicate + newest first
    uniq = dedupe(raw)
    uniq.sort(key=lambda x: x.get("published", ""), reverse=True)
    chosen = uniq[:MAX_ITEMS]

    # 3️⃣ Optional LLM summarisation (cost‑effective)
    if os.getenv("OPENAI_API_KEY"):
        for it in chosen:
            if it["summary"]:
                it["llm_summary"] = summarize_openai(it["summary"][:2000])
            else:
                it["llm_summary"] = ""

    # 4️⃣ Build HTML & send
    html = build_html(chosen)
    send_email(html)


if __name__ == "__main__":
    main()
