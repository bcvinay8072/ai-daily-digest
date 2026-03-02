#!/usr/bin/env python3
# -----------------------------------------------------------------
# Daily AI Digest – advanced version (filter, scoring, sections)
# -----------------------------------------------------------------
import os, json, hashlib, datetime, pathlib, ssl, smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import feedparser, requests, bs4
from jinja2 import Template
from dotenv import load_dotenv

load_dotenv()                                   # <- reads .env

# -----------------------------------------------------------
# Config from .env (with sensible defaults)
# -----------------------------------------------------------
MAX_ITEMS          = int(os.getenv("MAX_ITEMS", "12"))
KEYWORDS           = [k.strip().lower() for k in os.getenv("KEYWORDS", "").split(",") if k]
INCLUDE_IMAGES    = os.getenv("INCLUDE_IMAGES", "true").lower() == "true"
DEBUG_MODE        = os.getenv("DEBUG_MODE", "false").lower() == "true"
NEWSAPI_POP_FACTOR = float(os.getenv("NEWSAPI_POP_FACTOR", "0.0"))

# source weighting
raw_weights = os.getenv("SOURCE_WEIGHTS", "")
SOURCE_WEIGHTS = {}
for pair in raw_weights.split(";"):
    if "=" in pair:
        name, val = pair.split("=", 1)
        SOURCE_WEIGHTS[name.strip().lower()] = float(val)

# -----------------------------------------------------------
# RSS / API sources (add/remove here)
# -----------------------------------------------------------
FEEDS = [
    "https://techcrunch.com/tag/artificial-intelligence/feed/",
    "https://www.theverge.com/artificial-intelligence/rss/index.xml",
    "https://export.arxiv.org/rss/cs.AI",
    "https://hnrss.org/show",
    # optional NewsAPI – you must have NEWS_API_KEY set
    f"https://newsapi.org/v2/everything?q=artificial+intelligence&language=en&sortBy=publishedAt&pageSize=30&apiKey={os.getenv('NEWS_API_KEY','')}"
]

# -----------------------------------------------------------
# Helper: fetch each feed and tag it with a source name
# -----------------------------------------------------------
def fetch(src: str):
    # ----- infer a short source name -----
    if "techcrunch.com" in src:  name = "techcrunch"
    elif "theverge.com" in src: name = "verge"
    elif "export.arxiv.org" in src: name = "arxiv"
    elif "hnrss.org" in src:    name = "hn"
    elif "newsapi.org" in src: name = "newsapi"
    else:                       name = "generic"

    # ----- NewsAPI (JSON) -----
    if "newsapi.org" in src:
        data = requests.get(src).json()
        items = []
        for a in data.get("articles", []):
            items.append({
                "title": a["title"],
                "link": a["url"],
                "published": a.get("publishedAt",""),
                "summary": a.get("description",""),
                "source": name,
                "popularity": a.get("popularity",0)
            })
        return items

    # ----- RSS/Atom -----
    d = feedparser.parse(src)
    out = []
    for e in d.entries:
        out.append({
            "title": e.title,
            "link": e.link,
            "published": getattr(e, "published", ""),
            "summary": getattr(e, "summary", ""),
            "source": name,
        })
    return out

# -----------------------------------------------------------
# Optional: fetch an Open‑Graph image (if INCLUDE_IMAGES)
# -----------------------------------------------------------
def fetch_og_image(url: str) -> str | None:
    try:
        r = requests.get(url, timeout=5,
                         headers={"User-Agent":"Mozilla/5.0"})
        if r.status_code != 200:
            return None
        soup = bs4.BeautifulSoup(r.text, "html.parser")
        og = soup.find("meta", property="og:image")
        if og and og.get("content"):
            return og["content"]
    except Exception:
        pass
    return None

# -----------------------------------------------------------
# Scoring helpers
# -----------------------------------------------------------
def iso_to_ts(iso: str) -> float:
    try:
        return datetime.datetime.fromisoformat(iso.replace("Z","+00:00")).timestamp()
    except Exception:
        return 0.0

def compute_score(item: dict) -> float:
    base = SOURCE_WEIGHTS.get(item.get("source","generic").lower(), 1.0)
    now = datetime.datetime.now(datetime.timezone.utc).timestamp()
    age = now - iso_to_ts(item.get("published",""))
    recency = 1.0 / (1.0 + age/86400)          # boost for fresh items
    pop = float(item.get("popularity",0))
    pop_bonus = pop * NEWSAPI_POP_FACTOR
    txt = f"{item.get('title','')} {item.get('summary','')}".lower()
    kw_bonus = 0.5 if any(kw in txt for kw in KEYWORDS) else 0.0
    return (base*1.0) + (recency*2.0) + pop_bonus + kw_bonus

# -----------------------------------------------------------
# LLM one‑sentence teaser (optional)
# -----------------------------------------------------------
def summarize_openai_short(text: str) -> str:
    import openai
    openai.api_key = os.getenv("OPENAI_API_KEY")
    prompt = ("Summarise the following article in a single sentence, "
              "no jargon, max 25 words:\n\n" + text)
    try:
        resp = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[{"role":"user","content":prompt}],
            temperature=0.0,
            max_tokens=60,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        print("LLM error:", e)
        return ""

# -----------------------------------------------------------
# Persist sent URLs (optional)
# -----------------------------------------------------------
SENT_IDS_PATH = pathlib.Path("sent_ids.json")
def load_sent_ids() -> set:
    if SENT_IDS_PATH.is_file():
        try:
            return set(json.load(open(SENT_IDS_PATH)))
        except Exception:
            return set()
    return set()
def save_sent_ids(ids: set):
    json.dump(list(ids), open(SENT_IDS_PATH,"w"))

# -----------------------------------------------------------
# Main orchestration
# -----------------------------------------------------------
def main():
    # 1️⃣ Gather everything
    all_items = []
    for src in FEEDS:
        try:
            all_items.extend(fetch(src))
        except Exception as exc:
            print(f"⚠️ fetch error {src}: {exc}")

    # 2️⃣ Load already‑sent set and filter out duplicates
    already_sent = load_sent_ids()
    all_items = [it for it in all_items if it["link"] not in already_sent]

    # 3️⃣ Score + sort
    for it in all_items:
        it["score"] = compute_score(it)
    sorted_items = sorted(all_items, key=lambda i: i["score"], reverse=True)
    chosen = sorted_items[:MAX_ITEMS]

    # 4️⃣ Optional LLM short teaser
    if os.getenv("OPENAI_API_KEY"):
        for it in chosen:
            # Use summary if present; otherwise scrape the article text (optional)
            src_txt = it.get("summary") or ""
            it["llm_summary"] = summarize_openai_short(src_txt[:2000])

    # 5️⃣ Optional image fetching
    if INCLUDE_IMAGES:
        for it in chosen:
            if not it.get("image_url"):
                it["image_url"] = fetch_og_image(it["link"])

    # 6️⃣ Sectioning (Top 3 + per‑source buckets)
    top_items = chosen[:3]
    by_source = {}
    for itm in chosen[3:]:
        src = itm["source"]
        by_source.setdefault(src, []).append(itm)

    # 7️⃣ Build HTML
    today = datetime.date.today().isoformat()
    intro = f"Good morning! Here are the {len(chosen)} most relevant AI stories for {today}."
    html = Template(TEMPLATE_STR).render(
        date=today,
        intro_line=intro,
        top_items=top_items,
        sections=by_source,
        INCLUDE_IMAGES=INCLUDE_IMAGES,
    )

    # 8️⃣ DEBUG: dump HTML to Action log if requested
    if DEBUG_MODE:
        print("\n===== DEBUG: GENERATED HTML =====\n")
        print(html)
        print("\n===== END DEBUG HTML =====\n")

    # 9️⃣ Send e‑mail via Gmail SMTP
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🗞️ AI Digest – {today}"
    msg["From"]    = os.getenv("SMTP_EMAIL")
    msg["To"]      = os.getenv("RECIPIENT_EMAIL")
    msg.attach(MIMEText(html, "html"))

    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as srv:
        srv.login(os.getenv("SMTP_EMAIL"), os.getenv("SMTP_PASSWORD"))
        srv.send_message(msg)
    print("✅ Mail delivered")

    # 10️⃣ Persist sent URLs for next run
    new_ids = {it["link"] for it in chosen}
    save_sent_ids(already_sent | new_ids)

# -----------------------------------------------------------
if __name__ == "__main__":
    main()
