#!/usr/bin/env python3
# -----------------------------------------------------------------
# Weekly AI Digest – fetch, filter, score, format, email
# -----------------------------------------------------------------
import os
import json
import hashlib
import datetime
import ssl
import smtplib

import feedparser          # RSS/Atom parser
import requests            # HTTP client
import bs4                 # BeautifulSoup for OG image extraction
from jinja2 import Template
from dotenv import load_dotenv

# -------------------------------------------------------------
# Load .env (non‑secret configuration)
# -------------------------------------------------------------
load_dotenv()   # reads a .env file in the repo root (if present)

# -------------------- USER‑CONFIGURABLE OPTIONS --------------------
MAX_ITEMS          = int(os.getenv("MAX_ITEMS", "20"))          # how many items in the weekly mail
MAX_AGE_DAYS       = int(os.getenv("MAX_AGE_DAYS", "7"))        # keep only items newer than this
KEYWORDS           = [k.strip().lower()
                      for k in os.getenv("KEYWORDS", "").split(",")
                      if k]                                   # e.g. llm,gpt,reinforcement
INCLUDE_IMAGES    = os.getenv("INCLUDE_IMAGES", "true").lower() == "true"
DEBUG_MODE        = os.getenv("DEBUG_MODE", "false").lower() == "true"
NEWSAPI_POP_FACTOR = float(os.getenv("NEWSAPI_POP_FACTOR", "0.3"))
# -----------------------------------------------------------------


# -----------------------------------------------------------------
# SOURCE WEIGHTS – adjust to give more importance to preferred feeds
# -----------------------------------------------------------------
# The key must match the **source name** returned by the fetch() helper.
# (source names are lower‑case, no spaces)
SOURCE_WEIGHTS = {
    "techcrunch": 2.0,
    "verge":      1.5,
    "arxiv":      2.5,
    "hn":         0.8,
    "reddit":     0.6,
    "generic":    1.0,
}
# -----------------------------------------------------------------


# -----------------------------------------------------------------
# 1️⃣ FETCH ALL SOURCES
# -----------------------------------------------------------------
FEEDS = [
    # ----- Free RSS feeds (feel free to add more) -----
    "https://techcrunch.com/tag/artificial-intelligence/feed/",
    "https://www.theverge.com/artificial-intelligence/rss/index.xml",
    "https://export.arxiv.org/rss/cs.AI",
    "https://hnrss.org/show",
    "https://www.technologyreview.com/feed/tag/artificial-intelligence/",
    "https://intelligence.org/feed/",
    "https://www.aitrends.com/feed/",
    "https://www.deeplearning.ai/feed/",

    # ----- Optional NewsAPI source (requires NEWS_API_KEY) -----
    # If you have a NewsAPI key, uncomment the line below and add the secret.
    # f"https://newsapi.org/v2/everything?"
    # f"q=artificial+intelligence&language=en&sortBy=publishedAt&"
    # f"pageSize=30&apiKey={os.getenv('NEWS_API_KEY','')}"
]

def _infer_source_name(url: str) -> str:
    """Map a feed URL to the short source name used in SOURCE_WEIGHTS."""
    if "techcrunch.com" in url:
        return "techcrunch"
    if "theverge.com" in url:
        return "verge"
    if "arxiv.org" in url:
        return "arxiv"
    if "hnrss.org" in url:
        return "hn"
    if "newsapi.org" in url:
        return "newsapi"
    # generic fallback
    return "generic"


def fetch(src: str):
    """Return a list of dicts for the given source."""
    source_name = _infer_source_name(src)

    # ----- NewsAPI (JSON) -------------------------------------------------
    if "newsapi.org" in src:
        resp = requests.get(src, timeout=10)
        data = resp.json()
        items = []
        for a in data.get("articles", []):
            items.append({
                "title": a.get("title", ""),
                "link": a.get("url", ""),
                "published": a.get("publishedAt", ""),
                "summary": a.get("description", ""),
                "source": source_name,
                "popularity": a.get("popularity", 0)   # NewsAPI gives a popularity score
            })
        return items

    # ----- RSS / Atom ----------------------------------------------------
    d = feedparser.parse(src)
    out = []
    for e in d.entries:
        out.append({
            "title": e.title,
            "link": e.link,
            "published": getattr(e, "published", ""),
            "summary": getattr(e, "summary", ""),
            "source": source_name,
        })
    return out


# -----------------------------------------------------------------
# 2️⃣ AGE FILTER – drop anything older than MAX_AGE_DAYS
# -----------------------------------------------------------------
def _iso_to_ts(iso: str) -> float:
    """Convert an ISO‑8601 string to a Unix timestamp; return 0 on failure."""
    if not iso:
        return 0.0
    try:
        # Handles both "2024-06-29T12:34:56Z" and offset forms
        return datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except Exception:
        # Some feeds give RFC‑822 style dates – let feedparser try
        try:
            parsed = feedparser._parse_date(iso)  # internal helper
            return parsed.timestamp()
        except Exception:
            return 0.0


def is_too_old(published_str: str) -> bool:
    """True if the article is older than MAX_AGE_DAYS."""
    if not published_str:
        return False          # treat missing date as “new enough”
    ts = _iso_to_ts(published_str)
    if ts == 0.0:
        return False
    age_days = (datetime.datetime.now(datetime.timezone.utc).timestamp() - ts) / 86400
    return age_days > MAX_AGE_DAYS


# -----------------------------------------------------------------
# 3️⃣ DEDUPLICATION – keep only newest copy of the same URL
# -----------------------------------------------------------------
def dedupe(items):
    """Deduplicate by URL; keep the newest version if duplicated."""
    seen = {}
    for it in items:
        h = hashlib.sha256(it["link"].encode()).hexdigest()
        # If we already have this URL, keep the newer `published` entry
        if (h not in seen) or (it.get("published","") > seen[h].get("published","")):
            seen[h] = it
    return list(seen.values())


# -----------------------------------------------------------------
# 4️⃣ SENT‑IDS PERSISTENCE (for cross‑run duplicate avoidance)
# -----------------------------------------------------------------
SENT_IDS_PATH = "sent_ids.json"


def load_sent_ids() -> set:
    """Return a set of URLs that have already been mailed."""
    if not os.path.isfile(SENT_IDS_PATH):
        return set()
    try:
        data = json.load(open(SENT_IDS_PATH))
        # file should contain a JSON array of strings
        return set(data)
    except Exception:
        return set()


def save_sent_ids(ids: set):
    """Write the set of URLs back to sent_ids.json."""
    json.dump(list(ids), open(SENT_IDS_PATH, "w"))


# -----------------------------------------------------------------
# 5️⃣ OPTIONAL – ONE‑SENTENCE LLM TEASER
# -----------------------------------------------------------------
def summarize_openai_short(text: str) -> str:
    """Ask OpenAI (gpt‑4o‑mini) for a one‑sentence summary."""
    import openai
    openai.api_key = os.getenv("OPENAI_API_KEY")
    if not openai.api_key:
        return ""

    prompt = (
        "Summarise the following article in a single concise sentence "
        "(max 25 words, no jargon). Return only the sentence.\n\n"
        + text
    )
    try:
        resp = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=60,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        print("🔺 OpenAI error:", e)
        return ""


# -----------------------------------------------------------------
# 6️⃣ OPTIONAL – FETCH OPEN‑GRAPH IMAGE
# -----------------------------------------------------------------
def fetch_og_image(url: str) -> str | None:
    """Return the first og:image URL from the page, or None."""
    try:
        resp = requests.get(url, timeout=5,
                            headers={"User-Agent":"Mozilla/5.0"})
        if resp.status_code != 200:
            return None
        soup = bs4.BeautifulSoup(resp.text, "html.parser")
        meta = soup.find("meta", property="og:image")
        if meta and meta.get("content"):
            return meta["content"]
    except Exception:
        pass
    return None


# -----------------------------------------------------------------
# 7️⃣ SCORING – combine source weight, recency, popularity, keywords
# -----------------------------------------------------------------
def compute_score(item: dict) -> float:
    # ----- source weight -------------------------------------------------
    base = SOURCE_WEIGHTS.get(item.get("source", "generic").lower(), 1.0)

    # ----- recency – newer items get a higher boost --------------------
    now_ts = datetime.datetime.now(datetime.timezone.utc).timestamp()
    pub_ts = _iso_to_ts(item.get("published", ""))
    if pub_ts == 0.0:
        # unknown date → give a tiny boost so it stays in the list
        recency = 0.4
    else:
        # age in days; we use an exponential decay (newer = closer to 1)
        age_days = (now_ts - pub_ts) / 86400
        recency = max(0.1, 1.0 / (1.0 + age_days))   # 1‑day old ≈0.5, 7‑day ≈0.13

    # ----- popularity (NewsAPI only) ------------------------------------
    pop_bonus = float(item.get("popularity", 0)) * NEWSAPI_POP_FACTOR

    # ----- keyword boost ------------------------------------------------
    txt = f"{item.get('title','')} {item.get('summary','')}".lower()
    kw_bonus = 0.5 if any(kw in txt for kw in KEYWORDS) else 0.0

    # ----- final weighted sum (feel free to tweak) --------------------
    score = (base * 1.0) + (recency * 4.0) + pop_bonus + kw_bonus
    return score


# -----------------------------------------------------------------
# 8️⃣ HTML TEMPLATE (Jinja2)
# -----------------------------------------------------------------
TEMPLATE_STR = """
<html>
<head>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body {font-family:Arial,Helvetica,sans-serif;margin:0;padding:20px;background:#fafafa;}
    h2 {color:#0d6efd;}
    .section {margin-top:30px;}
    .item {margin-bottom:18px;padding:10px;background:#fff;border-radius:4px;box-shadow:0 1px 3px rgba(0,0,0,0.1);}
    .title {font-weight:bold;color:#0d6efd;text-decoration:none;}
    .meta {font-size:0.85em;color:#555;margin-top:2px;}
    .summary {margin-top:5px;color:#333;}
    blockquote {border-left:3px solid #ddd;margin:8px 0;padding-left:10px;color:#555;font-style:italic;}
    .img {max-width:100%;height:auto;margin-top:5px;border-radius:4px;}
  </style>
</head>
<body>
  <h2>{{ intro_line }}</h2>

  <!-- ----- Top Stories (first 3) ----- -->
  <div class="section">
    <h3>🏆 Top stories</h3>
    {% for it in top_items %}
      <div class="item">
        <a class="title" href="{{ it.link }}" target="_blank">{{ it.title }}</a>
        <div class="meta">{{ it.published[:10] if it.published else '' }} • {{ it.source|capitalize }}</div>
        {% if it.llm_summary %}
          <div class="summary">{{ it.llm_summary }}</div>
        {% elif it.summary %}
          <div class="summary">{{ it.summary }}</div>
        {% endif %}
        {% if it.image_url and INCLUDE_IMAGES %}
          <img class="img" src="{{ it.image_url }}" alt="">
        {% endif %}
      </div>
    {% endfor %}
  </div>

  <!-- ----- Other sources grouped by source name ----- -->
  {% for src, items in sections.items() %}
    <div class="section">
      <h3>📚 {{ src|capitalize }}</h3>
      {% for it in items %}
        <div class="item">
          <a class="title" href="{{ it.link }}" target="_blank">{{ it.title }}</a>
          <div class="meta">{{ it.published[:10] if it.published else '' }}</div>
          {% if it.llm_summary %}
            <div class="summary">{{ it.llm_summary }}</div>
          {% elif it.summary %}
            <div class="summary">{{ it.summary }}</div>
          {% endif %}
          {% if it.image_url and INCLUDE_IMAGES %}
            <img class="img" src="{{ it.image_url }}" alt="">
          {% endif %}
        </div>
      {% endfor %}
    </div>
  {% endfor %}

  <p style="font-size:0.8em;color:#888;">
    You are receiving this because you subscribed to the AI‑Digest runner.
    To stop, delete the repo or remove the GitHub Secrets.
  </p>
</body>
</html>
"""


# -----------------------------------------------------------------
# 9️⃣ MAIN – orchestrate everything
# -----------------------------------------------------------------
def main():
    # ---------- 1️⃣ Fetch everything ----------
    all_items = []
    for src in FEEDS:
        try:
            all_items.extend(fetch(src))
        except Exception as exc:
            print(f"⚠️  fetch error {src}: {exc}")

    # ---------- 2️⃣ Age filter ----------
    fresh_items = [it for it in all_items if not is_too_old(it.get("published", ""))]
    print(f"🔎 {len(fresh_items)} items after age filter (max {MAX_AGE_DAYS} d)")

    # ---------- 3️⃣ Remove URLs already sent ----------
    already_sent = load_sent_ids()
    fresh_items = [it for it in fresh_items if it["link"] not in already_sent]

    # ---------- 4️⃣ Deduplicate within this run ----------
    fresh_items = dedupe(fresh_items)

    # ---------- 5️⃣ Compute score & keep top N ----------
    for it in fresh_items:
        it["score"] = compute_score(it)
    chosen = sorted(fresh_items, key=lambda i: i["score"], reverse=True)[:MAX_ITEMS]

    # ---------- 6️⃣ Optional LLM one‑sentence teaser ----------
    if os.getenv("OPENAI_API_KEY"):
        for it in chosen:
            src_txt = it.get("summary", "") or ""
            it["llm_summary"] = summarize_openai_short(src_txt[:2000])

    # ---------- 7️⃣ Optional OG image ----------
    if INCLUDE_IMAGES:
        for it in chosen:
            if not it.get("image_url"):
                it["image_url"] = fetch_og_image(it["link"])

    # ---------- 8️⃣ Sectioning: top 3 + per‑source ----------
    top_items = chosen[:3]
    sections = {}
    for itm in chosen[3:]:
        src = itm["source"]
        sections.setdefault(src, []).append(itm)

    # ---------- 9️⃣ Build HTML ----------
    html = Template(TEMPLATE_STR).render(
        intro_line=f"🗞️ Weekly AI Digest – {datetime.date.today().isoformat()} (last {MAX_AGE_DAYS} d)",
        top_items=top_items,
        sections=sections,
        INCLUDE_IMAGES=INCLUDE_IMAGES,
    )

    # ---------- 10️⃣ DEBUG: dump HTML to log if requested ----------
    if DEBUG_MODE:
        print("\n===== DEBUG: GENERATED HTML START =====\n")
        print(html)
        print("\n===== DEBUG: GENERATED HTML END =====\n")

    # ---------- 11️⃣ Send e‑mail via Gmail SMTP ----------
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🗞️ Weekly AI Digest – {datetime.date.today().isoformat()}"
    msg["From"]    = os.getenv("SMTP_EMAIL")
    msg["To"]      = os.getenv("RECIPIENT_EMAIL")
    msg.attach(MIMEText(html, "html"))

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
        server.login(os.getenv("SMTP_EMAIL"), os.getenv("SMTP_PASSWORD"))
        server.send_message(msg)
    print("✅ Mail delivered")

    # ---------- 12️⃣ Update sent_ids.json & let the workflow push it ----------
    new_urls = {it["link"] for it in chosen}
    save_sent_ids(already_sent | new_urls)


if __name__ == "__main__":
    main()
