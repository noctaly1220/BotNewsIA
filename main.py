import argparse
import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List

import feedparser
import requests
import yaml
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from openai import OpenAI

from prompt_templates import SYSTEM_PROMPT, AI_JUDGE_PROMPT, build_judge_prompt, build_digest_prompt

load_dotenv()

STATE_FILE = Path("seen_items.json")
HISTORY_FILE = Path("items_history.json")
FEEDBACK_FILE = Path("feedback.json")
MAX_SUMMARY_CHARS = 1200

BASE_IMPORTANT_KEYWORDS = [
    "openai", "chatgpt", "gpt-5", "gpt-4", "anthropic", "claude",
    "google", "gemini", "deepmind", "meta", "llama", "mistral",
    "hugging face", "model", "llm", "agent", "agents", "reasoning",
    "multimodal", "video", "audio", "voice", "code", "coding",
    "developer", "automation", "api", "release", "launch",
    "benchmark", "open source", "startup", "funding", "ai"
]

BASE_NOISE_KEYWORDS = [
    "top 10", "best prompts", "prompt list", "logo generator",
    "ai girlfriend", "funny", "meme", "wallpaper", "crypto trading bot",
    "make money fast", "thread", "opinion", "rumor", "leak without source"
]

HIGH_VALUE_KEYWORDS = [
    "new model", "released", "launches", "announces", "api",
    "agent", "agents", "benchmark", "open-source", "open source",
    "reasoning", "coding", "multimodal", "video generation",
    "voice", "enterprise", "pricing", "funding", "raises",
    "acquisition", "partnership", "claude code"
]


def read_json_file(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json_file(path: Path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def load_feedback() -> Dict:
    return read_json_file(FEEDBACK_FILE, {
        "noise_keywords": [],
        "important_keywords": [],
        "blocked_sources": [],
        "preferred_sources": []
    })


def add_feedback(kind: str, value: str):
    data = load_feedback()
    key = "noise_keywords" if kind == "noise" else "important_keywords"
    if value not in data[key]:
        data[key].append(value)
    write_json_file(FEEDBACK_FILE, data)
    print(f"Feedback ajouté dans {key}: {value}")


def load_sources() -> Dict:
    with open("sources.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_seen() -> set:
    return set(read_json_file(STATE_FILE, []))


def save_seen(seen: set):
    write_json_file(STATE_FILE, sorted(list(seen)))


def make_id(title: str, link: str) -> str:
    raw = f"{title}|{link}".encode("utf-8", errors="ignore")
    return hashlib.sha256(raw).hexdigest()


def clean_html(text: str) -> str:
    if not text:
        return ""
    if not ("<" in str(text) and ">" in str(text)):
        return " ".join(str(text).split())
    soup = BeautifulSoup(text, "html.parser")
    return " ".join(soup.get_text(" ").split())


def short_id(full_id: str) -> str:
    return full_id[:10]


def fetch_rss_source(source: Dict) -> List[Dict]:
    feed = feedparser.parse(source["url"])
    items = []

    for entry in feed.entries[:15]:
        title = entry.get("title", "").strip()
        link = entry.get("link", "").strip()
        summary = clean_html(entry.get("summary", ""))[:MAX_SUMMARY_CHARS]

        if not title or not link:
            continue

        item_id = make_id(title, link)
        items.append({
            "id": item_id,
            "short_id": short_id(item_id),
            "title": title,
            "link": link,
            "summary": summary,
            "source": source["name"],
            "category": source.get("category", "rss"),
            "weight": source.get("weight", 3),
        })

    return items


def fetch_hackernews(query_terms: List[str]) -> List[Dict]:
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

        item_id = make_id(title, link)
        items.append({
            "id": item_id,
            "short_id": short_id(item_id),
            "title": title,
            "link": link,
            "summary": summary,
            "source": "Hacker News",
            "category": "community",
            "weight": 4,
            "score": hit.get("points", 0) or 0,
        })

    return items


def contains_any(text: str, keywords: List[str]) -> bool:
    return any(k.lower() in text for k in keywords)


def count_matches(text: str, keywords: List[str]) -> int:
    return sum(1 for k in keywords if k.lower() in text)


def rule_score_item(item: Dict, feedback: Dict) -> int:
    title = item.get("title", "").lower()
    summary = item.get("summary", "").lower()
    source = item.get("source", "")
    category = item.get("category", "")
    text = f"{title} {summary}"

    noise_keywords = BASE_NOISE_KEYWORDS + feedback.get("noise_keywords", [])
    important_keywords = BASE_IMPORTANT_KEYWORDS + feedback.get("important_keywords", [])

    score = int(item.get("weight", 3)) * 10

    if source in feedback.get("blocked_sources", []):
        return 0

    if source in feedback.get("preferred_sources", []):
        score += 15

    if category == "official":
        score += 40

    if category == "official_open_source":
        score += 35

    major_players = [
        "openai", "chatgpt", "anthropic", "claude", "gemini",
        "deepmind", "meta", "llama", "mistral", "hugging face"
    ]
    score += count_matches(text, major_players) * 10
    score += count_matches(text, HIGH_VALUE_KEYWORDS) * 8
    score += count_matches(text, important_keywords) * 3

    launch_signals = ["launch", "released", "announces", "introduces", "rolls out", "unveils", "release"]
    if contains_any(text, launch_signals):
        score += 20

    business_signals = ["funding", "raises", "acquisition", "partnership", "enterprise", "pricing"]
    if contains_any(text, business_signals):
        score += 12

    if item.get("score"):
        score += min(int(item["score"]) // 15, 25)

    if contains_any(text, noise_keywords):
        score -= 60

    if source == "Reddit ChatGPT":
        score -= 15

    return max(score, 0)


def passes_basic_filter(item: Dict, feedback: Dict) -> bool:
    text = f"{item.get('title', '')} {item.get('summary', '')}".lower()
    source = item.get("source", "")
    noise_keywords = BASE_NOISE_KEYWORDS + feedback.get("noise_keywords", [])
    important_keywords = BASE_IMPORTANT_KEYWORDS + feedback.get("important_keywords", [])

    if source in feedback.get("blocked_sources", []):
        return False
    if contains_any(text, noise_keywords):
        return False
    if item.get("category") in ["official", "official_open_source"]:
        return True
    if source == "Reddit ChatGPT":
        return count_matches(text, HIGH_VALUE_KEYWORDS) >= 2
    return contains_any(text, important_keywords)


def ai_judge_items(items: List[Dict]) -> Dict[str, Dict]:
    if not items:
        return {}

    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    prompt = build_judge_prompt(items[:12])

    response = client.chat.completions.create(
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        messages=[
            {"role": "system", "content": AI_JUDGE_PROMPT},
            {"role": "user", "content": prompt},
        ],
        temperature=0.1,
    )

    content = response.choices[0].message.content.strip()

    try:
        data = json.loads(content)
    except Exception:
        match = re.search(r"\[.*\]", content, re.S)
        data = json.loads(match.group(0)) if match else []

    if isinstance(data, dict):
        data = data.get("items", [])

    result = {}
    for row in data:
        if isinstance(row, dict) and row.get("id"):
            result[row["id"]] = row

    return result


def collect_items(mode: str) -> List[Dict]:
    sources = load_sources()
    feedback = load_feedback()
    seen = load_seen()
    all_items = []

    for source in sources.get("rss_sources", []):
        try:
            all_items.extend(fetch_rss_source(source))
        except Exception as e:
            print(f"[WARN] RSS failed: {source['name']} → {e}")

    if sources.get("hackernews", {}).get("enabled"):
        try:
            terms = sources["hackernews"].get("query_terms", [])
            all_items.extend(fetch_hackernews(terms))
        except Exception as e:
            print(f"[WARN] Hacker News failed → {e}")

    candidates = []
    new_seen = set(seen)

    for item in all_items:
        if item["id"] in seen:
            continue
        if not passes_basic_filter(item, feedback):
            continue

        item["rule_score"] = rule_score_item(item, feedback)
        if item["rule_score"] <= 0:
            continue
        candidates.append(item)

    candidates.sort(key=lambda x: x["rule_score"], reverse=True)
    candidates = candidates[:12]

    judge = ai_judge_items(candidates)
    final_items = []

    for item in candidates:
        j = judge.get(item["short_id"], {})
        importance = int(j.get("importance", 3))
        is_noise = bool(j.get("is_noise", False))
        reason = j.get("reason", "")

        item["ai_importance"] = importance
        item["ai_reason"] = reason
        item["final_score"] = item["rule_score"] + ((importance - 3) * 25)

        if is_noise or importance <= 2:
            continue

        final_items.append(item)
        new_seen.add(item["id"])

    final_items.sort(key=lambda x: x["final_score"], reverse=True)

    max_items = int(os.getenv("MAX_ITEMS_MORNING", "7")) if mode == "morning" else int(os.getenv("MAX_ITEMS_EVENING", "5"))
    selected = final_items[:max_items]

    save_seen(new_seen)
    append_history(selected, mode)

    return selected


def append_history(items: List[Dict], mode: str):
    history = read_json_file(HISTORY_FILE, [])
    now = datetime.now().isoformat(timespec="seconds")

    for item in items:
        history.append({
            "date": now,
            "mode": mode,
            "title": item.get("title"),
            "source": item.get("source"),
            "link": item.get("link"),
            "rule_score": item.get("rule_score"),
            "final_score": item.get("final_score"),
            "ai_importance": item.get("ai_importance"),
            "ai_reason": item.get("ai_reason"),
        })

    history = history[-500:]
    write_json_file(HISTORY_FILE, history)


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
        temperature=0.35,
    )

    return response.choices[0].message.content


def send_telegram_message(text: str):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN ou TELEGRAM_CHAT_ID manquant.")

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    chunks = [text[i:i + 3800] for i in range(0, len(text), 3800)]

    for chunk in chunks:
        response = requests.post(
            url,
            data={"chat_id": chat_id, "text": chunk, "disable_web_page_preview": False},
            timeout=20,
        )
        response.raise_for_status()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["morning", "evening"], default="morning")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--feedback-noise", type=str)
    parser.add_argument("--feedback-important", type=str)
    args = parser.parse_args()

    if args.feedback_noise:
        add_feedback("noise", args.feedback_noise)
        return

    if args.feedback_important:
        add_feedback("important", args.feedback_important)
        return

    items = collect_items(args.mode)

    if not items:
        message = "🧠 Veille IA\n\nPas de nouveauté suffisamment importante détectée sur cette période."
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
