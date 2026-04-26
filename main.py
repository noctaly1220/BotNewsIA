import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

import feedparser
import requests
import yaml
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from openai import OpenAI

from prompt_templates import SYSTEM_PROMPT, build_digest_prompt

load_dotenv()

STATE_FILE = Path("seen_items.json")
MAX_SUMMARY_CHARS = 1200

IMPORTANT_KEYWORDS = [
    "openai", "chatgpt", "gpt", "anthropic", "claude", "google", "gemini",
    "deepmind", "meta", "llama", "mistral", "hugging face", "model",
    "agent", "agents", "reasoning", "multimodal", "video", "audio",
    "code", "coding", "developer", "automation", "ai", "artificial intelligence",
    "release", "launch", "benchmark", "open source", "startup", "funding",
]


def load_sources() -> Dict:
    with open("sources.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_seen() -> set:
    if not STATE_FILE.exists():
        return set()
    try:
        return set(json.loads(STATE_FILE.read_text(encoding="utf-8")))
    except Exception:
        return set()


def save_seen(seen: set):
    STATE_FILE.write_text(json.dumps(sorted(list(seen)), indent=2), encoding="utf-8")


def make_id(title: str, link: str) -> str:
    raw = f"{title}|{link}".encode("utf-8", errors="ignore")
    return hashlib.sha256(raw).hexdigest()


def clean_html(text: str) -> str:
    if not text:
        return ""
    soup = BeautifulSoup(text, "html.parser")
    return " ".join(soup.get_text(" ").split())


def is_important(item: Dict) -> bool:
    text = f"{item.get('title', '')} {item.get('summary', '')}".lower()
    return any(k in text for k in IMPORTANT_KEYWORDS)


def fetch_rss_source(source: Dict) -> List[Dict]:
    feed = feedparser.parse(source["url"])
    items = []

    for entry in feed.entries[:15]:
        title = entry.get("title", "").strip()
        link = entry.get("link", "").strip()
        summary = clean_html(entry.get("summary", ""))[:MAX_SUMMARY_CHARS]

        if not title or not link:
            continue

        items.append({
            "id": make_id(title, link),
            "title": title,
            "link": link,
            "summary": summary,
            "source": source["name"],
            "category": source.get("category", "rss"),
            "weight": source.get("weight", 3),
        })

    return items


def fetch_reddit_source(source: Dict) -> List[Dict]:
    headers = {"User-Agent": "AIWatchBot/1.0"}
    response = requests.get(source["url"], headers=headers, timeout=20)
    response.raise_for_status()
    data = response.json()

    items = []
    for child in data.get("data", {}).get("children", []):
        post = child.get("data", {})
        title = post.get("title", "").strip()
        permalink = post.get("permalink", "")
        link = "https://www.reddit.com" + permalink if permalink else post.get("url", "")
        summary = clean_html(post.get("selftext", ""))[:MAX_SUMMARY_CHARS]

        if not title or not link:
            continue

        items.append({
            "id": make_id(title, link),
            "title": title,
            "link": link,
            "summary": summary,
            "source": source["name"],
            "category": source.get("category", "reddit"),
            "weight": source.get("weight", 3),
            "score": post.get("score", 0),
        })

    return items


def fetch_hackernews(query_terms: List[str]) -> List[Dict]:
    # API Algolia Hacker News
    query = " OR ".join(query_terms)
    url = "https://hn.algolia.com/api/v1/search_by_date"
    params = {"query": query, "tags": "story", "hitsPerPage": 20}

    response = requests.get(url, params=params, timeout=20)
    response.raise_for_status()
    data = response.json()

    items = []
    for hit in data.get("hits", []):
        title = hit.get("title") or hit.get("story_title") or ""
        link = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
        summary = f"Points HN: {hit.get('points', 0)} | Commentaires: {hit.get('num_comments', 0)}"

        if not title or not link:
            continue

        items.append({
            "id": make_id(title, link),
            "title": title,
            "link": link,
            "summary": summary,
            "source": "Hacker News",
            "category": "community",
            "weight": 4,
            "score": hit.get("points", 0) or 0,
        })

    return items


def score_item(item: Dict) -> int:
    title = item.get("title", "").lower()
    summary = item.get("summary", "").lower()
    text = f"{title} {summary}"

    score = int(item.get("weight", 3)) * 10

    for keyword in IMPORTANT_KEYWORDS:
        if keyword in text:
            score += 3

    if item.get("category") in ["official", "official_open_source"]:
        score += 20

    if item.get("score"):
        score += min(int(item["score"]) // 20, 20)

    return score


def collect_items(mode: str) -> List[Dict]:
    sources = load_sources()
    seen = load_seen()
    all_items = []

    for source in sources.get("rss_sources", []):
        try:
            all_items.extend(fetch_rss_source(source))
        except Exception as e:
            print(f"[WARN] RSS failed: {source['name']} → {e}")

    for source in sources.get("reddit_sources", []):
        try:
            all_items.extend(fetch_reddit_source(source))
        except Exception as e:
            print(f"[WARN] Reddit failed: {source['name']} → {e}")

    if sources.get("hackernews", {}).get("enabled"):
        try:
            terms = sources["hackernews"].get("query_terms", [])
            all_items.extend(fetch_hackernews(terms))
        except Exception as e:
            print(f"[WARN] Hacker News failed → {e}")

    fresh_items = []
    new_seen = set(seen)

    for item in all_items:
        if item["id"] in seen:
            continue

        if not is_important(item):
            continue

        item["importance_score"] = score_item(item)
        fresh_items.append(item)
        new_seen.add(item["id"])

    fresh_items.sort(key=lambda x: x["importance_score"], reverse=True)

    max_items = int(os.getenv("MAX_ITEMS_MORNING", "7")) if mode == "morning" else int(os.getenv("MAX_ITEMS_EVENING", "5"))

    selected = fresh_items[:max_items]
    save_seen(new_seen)

    return selected


def generate_digest(items: List[Dict], mode: str) -> str:
    if not items:
        return ""

    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    prompt = build_digest_prompt(items, mode=mode)

    response = client.chat.completions.create(
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        temperature=0.4,
    )

    return response.choices[0].message.content


def send_telegram_message(text: str):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN ou TELEGRAM_CHAT_ID manquant.")

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    # Telegram limite les messages autour de 4096 caractères.
    chunks = [text[i:i + 3800] for i in range(0, len(text), 3800)]

    for chunk in chunks:
        response = requests.post(
            url,
            data={
                "chat_id": chat_id,
                "text": chunk,
                "disable_web_page_preview": False,
            },
            timeout=20,
        )
        response.raise_for_status()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["morning", "evening"], default="morning")
    parser.add_argument("--dry-run", action="store_true", help="Affiche le résumé sans envoyer Telegram.")
    args = parser.parse_args()

    items = collect_items(args.mode)

    if not items:
        message = (
            "🧠 Veille IA\n\n"
            "Pas de nouveauté suffisamment importante détectée sur cette période."
        )
    else:
        digest = generate_digest(items, args.mode)
        now = datetime.now().strftime("%d/%m/%Y %H:%M")
        title = "🌅 Veille IA du matin" if args.mode == "morning" else "🌆 Update IA du soir"
        message = f"{title} — {now}\n\n{digest}"

    if args.dry_run:
        print(message)
    else:
        send_telegram_message(message)


if __name__ == "__main__":
    main()
