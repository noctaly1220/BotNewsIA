import argparse
import difflib
import hashlib
import html
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import feedparser
import requests
import trafilatura
import yaml
from dotenv import load_dotenv
from openai import OpenAI

from prompt_templates import JUDGE_SYSTEM, WRITER_SYSTEM, build_judge_prompt, build_writer_prompt

load_dotenv()

PARIS = ZoneInfo("Europe/Paris")
UTC = timezone.utc

SEEN_FILE = Path("seen_items.json")
HISTORY_FILE = Path("items_history.json")
FEEDBACK_FILE = Path("feedback.json")
STATE_FILE = Path("bot_state.json")

WINDOW_HOURS = 36          # on ignore les articles plus vieux
SEEN_TTL_DAYS = 45         # purge de la mémoire des articles vus
MAX_CANDIDATES = 15        # articles lus en entier puis jugés
MAX_ARTICLE_CHARS = 3500
MAX_ITEMS = {"morning": 5, "evening": 3}
EXTRA_ITEMS = 2  # actus bonus, uniquement si importance 5 (majeur)
MIN_IMPORTANCE = {"morning": 3, "evening": 4}

UA = {"User-Agent": "Mozilla/5.0 (compatible; BotNewsIA/2.0)"}

TAGS = {
    "model": "🧠 Modèle",
    "tool": "🛠 Outil",
    "business": "💼 Business",
    "research": "🔬 Recherche",
    "policy": "⚖️ Régulation",
    "other": "📰 Actu",
}
DAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MONTHS = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]


# ---------------------------------------------------------------- utilitaires

def _regex(words: List[str]) -> re.Pattern:
    return re.compile(r"\b(?:" + "|".join(re.escape(w) for w in words) + r")\b", re.I)


CORE_RE = _regex([
    "openai", "chatgpt", "gpt", "anthropic", "claude", "gemini", "deepmind", "llama", "mistral",
    "hugging face", "llm", "llms", "language model", "language models", "agent", "agents", "agentic",
    "machine learning", "copilot", "nvidia", "deepseek", "qwen", "grok", "xai", "midjourney", "sora",
    "reasoning", "multimodal", "open-source model", "open source model", "diffusion", "transformer",
    "artificial intelligence", "generative", "genai", "fine-tuning", "rag", "mcp",
])
AI_TOKEN_RE = re.compile(r"\bAI\b")  # sensible à la casse : évite "said", "ai" français, etc.
MAJOR_RE = _regex(["openai", "chatgpt", "anthropic", "claude", "gemini", "deepmind", "llama", "mistral", "hugging face", "deepseek", "qwen"])
HIGH_VALUE_RE = _regex([
    "launches", "launched", "released", "releases", "announces", "announced", "introduces", "unveils",
    "rolls out", "new model", "api", "pricing", "benchmark", "open-source", "open source", "funding",
    "raises", "acquires", "acquisition", "partnership", "claude code", "agents", "reasoning",
])
NOISE_RE = _regex([
    "top 10", "best prompts", "prompt list", "logo generator", "ai girlfriend", "meme", "wallpaper",
    "crypto trading bot", "make money fast", "giveaway", "discount code", "sponsored",
])


def read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def esc(text: str) -> str:
    return html.escape(str(text or ""), quote=False)


def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query)
             if not (k.lower().startswith("utm_") or k.lower() in {"fbclid", "gclid", "ref", "ref_src", "source"})]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme, parts.netloc.lower(), path, urlencode(query), ""))


def make_id(url: str) -> str:
    return hashlib.sha256(canonical_url(url).encode("utf-8", errors="ignore")).hexdigest()


def clean_html(text: str) -> str:
    if not text:
        return ""
    return " ".join(re.sub(r"<[^>]+>", " ", html.unescape(str(text))).split())


def ago_label(dt: Optional[datetime]) -> str:
    if not dt:
        return "date inconnue"
    minutes = int((datetime.now(UTC) - dt).total_seconds() // 60)
    if minutes < 60:
        return "il y a moins d'1 h"
    if minutes < 60 * 24:
        return f"il y a {minutes // 60} h"
    return "hier" if minutes < 60 * 48 else f"il y a {minutes // 1440} j"


def parse_dt(entry) -> Optional[datetime]:
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            return datetime(*t[:6], tzinfo=UTC)
    return None


# ---------------------------------------------------------------- état

def load_feedback() -> Dict:
    fb = read_json(FEEDBACK_FILE, {})
    fb.setdefault("noise_keywords", [])
    fb.setdefault("important_keywords", [])
    fb.setdefault("blocked_sources", [])
    fb.setdefault("preferred_sources", [])
    fb.setdefault("votes", {})  # {source: score}
    return fb


def load_seen() -> Dict[str, str]:
    raw = read_json(SEEN_FILE, {})
    now = datetime.now(UTC).isoformat()
    if isinstance(raw, list):  # ancien format : liste de hash
        raw = {h: now for h in raw}
    cutoff = datetime.now(UTC) - timedelta(days=SEEN_TTL_DAYS)
    return {k: v for k, v in raw.items() if datetime.fromisoformat(v) > cutoff}


def load_state() -> Dict:
    state = read_json(STATE_FILE, {})
    state.setdefault("done_slots", [])
    state.setdefault("tg_offset", 0)
    return state


def resolve_mode(arg: str, state: Dict) -> Optional[str]:
    """auto = déduit matin/soir de l'heure de Paris et évite les doubles envois."""
    if arg != "auto":
        return arg
    now = datetime.now(PARIS)
    if 8 <= now.hour < 15:
        mode = "morning"
    elif 17 <= now.hour < 23:
        mode = "evening"
    else:
        print(f"[SKIP] {now:%H:%M} Paris : hors créneau.")
        return None
    if f"{now.date()}-{mode}" in state["done_slots"]:
        print(f"[SKIP] créneau {mode} déjà envoyé aujourd'hui.")
        return None
    return mode


# ---------------------------------------------------------------- collecte

def fetch_rss(source: Dict, cutoff: datetime) -> List[Dict]:
    resp = requests.get(source["url"], headers=UA, timeout=20)
    resp.raise_for_status()
    feed = feedparser.parse(resp.content)
    items = []
    for entry in feed.entries[:25]:
        title = clean_html(entry.get("title", ""))
        link = entry.get("link", "").strip()
        if not title or not link:
            continue
        published = parse_dt(entry)
        if published and published < cutoff:
            continue
        link = canonical_url(link)
        items.append({
            "id": make_id(link), "short_id": make_id(link)[:10], "title": title, "link": link,
            "summary": clean_html(entry.get("summary", ""))[:1200], "source": source["name"],
            "category": source.get("category", "news"), "weight": source.get("weight", 3),
            "published": published,
        })
    return items


def fetch_hackernews(terms: List[str], cutoff: datetime, min_points: int) -> List[Dict]:
    items, seen_ids = [], set()
    for term in terms[:8]:
        resp = requests.get(
            "https://hn.algolia.com/api/v1/search_by_date",
            params={"query": term, "tags": "story", "hitsPerPage": 15,
                    "numericFilters": f"created_at_i>{int(cutoff.timestamp())},points>={min_points}"},
            timeout=20,
        )
        resp.raise_for_status()
        for hit in resp.json().get("hits", []):
            title = hit.get("title") or ""
            link = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
            if not title or hit.get("objectID") in seen_ids:
                continue
            seen_ids.add(hit.get("objectID"))
            link = canonical_url(link)
            items.append({
                "id": make_id(link), "short_id": make_id(link)[:10], "title": title, "link": link,
                "summary": "", "source": "Hacker News", "category": "community", "weight": 4,
                "published": datetime.fromtimestamp(hit.get("created_at_i", 0), UTC),
                "points": hit.get("points") or 0,
            })
    return items


def fetch_article_text(url: str) -> str:
    try:
        resp = requests.get(url, headers=UA, timeout=12)
        if resp.status_code != 200:
            return ""
        text = trafilatura.extract(resp.text, include_comments=False, include_tables=False) or ""
        return " ".join(text.split())[:MAX_ARTICLE_CHARS]
    except Exception:
        return ""


def passes_prefilter(item: Dict, feedback: Dict) -> bool:
    text = f"{item['title']} {item['summary']}"
    if item["source"] in feedback["blocked_sources"]:
        return False
    if NOISE_RE.search(text) or any(k.lower() in text.lower() for k in feedback["noise_keywords"]):
        return False
    if item["category"] in ("official", "official_open_source"):
        return True
    if item["source"].startswith("Reddit") and len(HIGH_VALUE_RE.findall(text)) < 1:
        return False
    return bool(CORE_RE.search(text) or AI_TOKEN_RE.search(text))


def rule_score(item: Dict, feedback: Dict) -> int:
    text = f"{item['title']} {item['summary']}"
    score = int(item["weight"]) * 10
    if item["category"] == "official":
        score += 35
    elif item["category"] == "official_open_source":
        score += 30
    score += min(len(MAJOR_RE.findall(text)), 3) * 8
    score += min(len(HIGH_VALUE_RE.findall(text)), 3) * 6
    score += sum(4 for k in feedback["important_keywords"] if k.lower() in text.lower())
    if item.get("published"):
        age_h = (datetime.now(UTC) - item["published"]).total_seconds() / 3600
        score += 15 if age_h < 6 else 8 if age_h < 18 else 0
    score += min(int(item.get("points", 0)) // 20, 25)
    score += max(-30, min(30, int(feedback["votes"].get(item["source"], 0)) * 5))
    if item["source"] in feedback["preferred_sources"]:
        score += 10
    return max(score, 0)


def drop_similar_titles(items: List[Dict]) -> List[Dict]:
    kept = []
    for it in items:  # déjà triés par score décroissant
        title = it["title"].lower()
        if any(difflib.SequenceMatcher(None, title, k["title"].lower()).ratio() > 0.8 for k in kept):
            continue
        kept.append(it)
    return kept


def collect_candidates(seen: Dict[str, str], feedback: Dict) -> List[Dict]:
    sources = yaml.safe_load(open("sources.yaml", encoding="utf-8"))
    cutoff = datetime.now(UTC) - timedelta(hours=WINDOW_HOURS)
    all_items, failed, ok = [], [], 0

    for src in sources.get("rss_sources", []):
        try:
            got = fetch_rss(src, cutoff)
            ok += 1
            print(f"[OK] {src['name']}: {len(got)} récents")
            all_items.extend(got)
        except Exception as e:
            failed.append(src["name"])
            print(f"[ECHEC] {src['name']}: {e}")

    hn = sources.get("hackernews", {})
    if hn.get("enabled"):
        try:
            got = fetch_hackernews(hn.get("query_terms", []), cutoff, int(hn.get("min_points", 30)))
            print(f"[OK] Hacker News: {len(got)} récents")
            all_items.extend(got)
        except Exception as e:
            failed.append("Hacker News")
            print(f"[ECHEC] Hacker News: {e}")

    unique = {}
    for it in all_items:
        if it["id"] not in seen and it["id"] not in unique and passes_prefilter(it, feedback):
            it["rule_score"] = rule_score(it, feedback)
            unique[it["id"]] = it

    ranked = sorted(unique.values(), key=lambda x: x["rule_score"], reverse=True)
    candidates = drop_similar_titles(ranked)[:MAX_CANDIDATES]
    for it in candidates:
        it["text"] = fetch_article_text(it["link"]) or it["summary"]
        it["published_label"] = ago_label(it.get("published"))
    print(f"[INFO] {len(candidates)} candidats, {len(failed)} sources en échec")
    collect_candidates.failed = failed
    return candidates


collect_candidates.failed = []


# ---------------------------------------------------------------- LLM

def llm_json(system: str, user: str, model: str, temperature: float) -> Dict:
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    resp = client.chat.completions.create(
        model=model, temperature=temperature, response_format={"type": "json_object"},
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
    )
    content = (resp.choices[0].message.content or "").strip()
    try:
        return json.loads(content)
    except Exception:
        match = re.search(r"\{.*\}", content, re.S)
        return json.loads(match.group(0)) if match else {}


def judge(items: List[Dict]) -> Dict[str, Dict]:
    model = os.getenv("OPENAI_JUDGE_MODEL") or os.getenv("OPENAI_MODEL") or "gpt-4o-mini"
    try:
        data = llm_json(JUDGE_SYSTEM, build_judge_prompt(items), model, 0.1)
    except Exception as e:
        print(f"[WARN] juge LLM indisponible, repli sur les règles: {e}")
        return {}
    return {r["id"]: r for r in data.get("items", []) if isinstance(r, dict) and r.get("id")}


def select_items(candidates: List[Dict], mode: str):
    verdicts = judge(candidates)
    to_mark_seen, selected = set(), []
    for it in candidates:
        v = verdicts.get(it["short_id"], {})
        imp = int(v.get("importance", 3) or 3)
        it["importance"] = imp
        it["category_llm"] = v.get("category", "other")
        if v.get("is_noise") or v.get("duplicate_of") or imp <= 2:
            to_mark_seen.add(it["id"])
            continue
        if imp >= MIN_IMPORTANCE[mode]:
            selected.append(it)
    selected.sort(key=lambda x: (x["importance"], x["rule_score"]), reverse=True)
    base = MAX_ITEMS[mode]
    extra = [it for it in selected[base:] if it["importance"] >= 5][:EXTRA_ITEMS]
    selected = selected[:base] + extra
    to_mark_seen.update(it["id"] for it in selected)
    return selected, to_mark_seen


def write_digest(items: List[Dict], mode: str) -> Dict:
    model = os.getenv("OPENAI_WRITER_MODEL") or os.getenv("OPENAI_MODEL") or "gpt-4o-mini"
    data = llm_json(WRITER_SYSTEM, build_writer_prompt(items, mode), model, 0.2)
    by_id = {r.get("id"): r for r in data.get("items", []) if isinstance(r, dict)}
    out = []
    for it in items:
        r = by_id.get(it["short_id"], {})
        out.append({
            "item": it,
            "title": r.get("title_fr") or it["title"],
            "what": r.get("what") or (it["summary"][:450] or "Résumé indisponible."),
            "context": r.get("context") or "",
            "why": r.get("why") or "",
            "for_you": r.get("for_you") or "",
            "tag": r.get("tag") if r.get("tag") in TAGS else it.get("category_llm", "other"),
        })
    return {"tldr": [t for t in data.get("tldr", []) if isinstance(t, str)][:5], "items": out}


# ---------------------------------------------------------------- Telegram

def tg(method: str, payload: Dict, retries: int = 3) -> Dict:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN manquant.")
    url = f"https://api.telegram.org/bot{token}/{method}"
    for attempt in range(retries):
        resp = requests.post(url, json=payload, timeout=25)
        if resp.status_code == 429:
            time.sleep(resp.json().get("parameters", {}).get("retry_after", 3) + 1)
            continue
        if resp.status_code >= 500 and attempt < retries - 1:
            time.sleep(2)
            continue
        resp.raise_for_status()
        return resp.json()
    raise RuntimeError(f"Telegram {method} a échoué après {retries} essais.")


def send(text: str, buttons: Optional[List[List[Dict]]] = None):
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not chat_id:
        raise RuntimeError("TELEGRAM_CHAT_ID manquant.")
    payload = {"chat_id": chat_id, "text": text[:4000], "parse_mode": "HTML",
               "link_preview_options": {"is_disabled": True}}
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}
    tg("sendMessage", payload)
    time.sleep(0.6)


def render_header(digest: Dict, mode: str, failed: List[str]) -> str:
    now = datetime.now(PARIS)
    icon, label = ("🌅", "Veille IA") if mode == "morning" else ("🌆", "Update IA du soir")
    lines = [f"{icon} <b>{label}</b> — {DAYS[now.weekday()]} {now.day} {MONTHS[now.month - 1]}", ""]
    if digest["tldr"]:
        lines.append("⚡ <b>L'essentiel</b>")
        lines += [f"• {esc(t)}" for t in digest["tldr"]]
        lines.append("")
    n = len(digest["items"])
    lines.append(f"👇 {n} actu{'s' if n > 1 else ''} détaillée{'s' if n > 1 else ''} ci-dessous")
    if len(failed) >= 3:
        lines.append(f"\n⚠️ {len(failed)} sources injoignables aujourd'hui")
    return "\n".join(lines)


def render_item(entry: Dict) -> (str, List[List[Dict]]):
    it = entry["item"]
    badge = {5: "🔴", 4: "🟠"}.get(it["importance"], "🟡")
    lines = [
        f"{badge} <b>{esc(entry['title'])}</b>",
        f"<i>{TAGS[entry['tag']]} · {esc(it['source'])} · {esc(it['published_label'])}</i>",
        "",
        esc(entry["what"]),
    ]
    if entry.get("context"):
        lines += ["", f"📚 <b>Contexte :</b> {esc(entry['context'])}"]
    if entry["why"]:
        lines += ["", f"💡 <b>Pourquoi ça compte :</b> {esc(entry['why'])}"]
    if entry["for_you"]:
        lines += ["", f"🎯 <b>Pour toi :</b> {esc(entry['for_you'])}"]
    buttons = [[
        {"text": "📖 Lire", "url": it["link"]},
        {"text": "👍", "callback_data": f"up:{it['short_id']}"},
        {"text": "👎", "callback_data": f"down:{it['short_id']}"},
    ]]
    return "\n".join(lines), buttons


def process_feedback(state: Dict, feedback: Dict) -> bool:
    """Lit les clics 👍/👎 reçus depuis le dernier run et met à jour les votes par source."""
    try:
        data = tg("getUpdates", {"offset": state["tg_offset"], "timeout": 0,
                                 "allowed_updates": ["callback_query"]})
    except Exception as e:
        print(f"[WARN] feedback ignoré: {e}")
        return False
    history = read_json(HISTORY_FILE, [])
    by_short = {h["short_id"]: h for h in history if "short_id" in h}
    changed = False
    for upd in data.get("result", []):
        state["tg_offset"] = upd["update_id"] + 1
        cb = upd.get("callback_query")
        if not cb:
            continue
        action, _, short = cb.get("data", "").partition(":")
        h = by_short.get(short)
        if h and action in ("up", "down"):
            delta = 1 if action == "up" else -1
            feedback["votes"][h["source"]] = feedback["votes"].get(h["source"], 0) + delta
            changed = True
            print(f"[FEEDBACK] {action} sur {h['source']} — {h['title'][:60]}")
        try:
            tg("answerCallbackQuery", {"callback_query_id": cb["id"], "text": "Noté ✅"}, retries=1)
        except Exception:
            pass  # la requête expire vite : normal, le vote est déjà pris en compte
    return changed


def append_history(entries: List[Dict], mode: str):
    history = read_json(HISTORY_FILE, [])
    now = datetime.now(PARIS).isoformat(timespec="seconds")
    for e in entries:
        it = e["item"]
        history.append({"date": now, "mode": mode, "short_id": it["short_id"], "title": e["title"],
                        "source": it["source"], "link": it["link"], "importance": it["importance"]})
    write_json(HISTORY_FILE, history[-400:])


# ---------------------------------------------------------------- main

def run(mode_arg: str, dry_run: bool):
    state, feedback, seen = load_state(), load_feedback(), load_seen()

    if not dry_run and process_feedback(state, feedback):
        write_json(FEEDBACK_FILE, feedback)

    mode = resolve_mode(mode_arg, state)
    if not mode:
        if not dry_run:
            write_json(STATE_FILE, state)
        return

    candidates = collect_candidates(seen, feedback)
    selected, to_mark_seen = select_items(candidates, mode)

    now_iso = datetime.now(UTC).isoformat()
    for item_id in to_mark_seen:
        seen[item_id] = now_iso

    if not selected:
        print("[INFO] rien d'assez important.")
        if mode == "morning" and not dry_run:
            send("🌅 <b>Veille IA</b>\n\nRien de vraiment important depuis hier. Bonne journée 👌")
        digest = None
    else:
        digest = write_digest(selected, mode)
        header = render_header(digest, mode, collect_candidates.failed)
        if dry_run:
            print(header)
            for entry in digest["items"]:
                print("\n" + "-" * 40 + "\n" + render_item(entry)[0])
        else:
            send(header)
            for entry in digest["items"]:
                text, buttons = render_item(entry)
                send(text, buttons)

    if dry_run:
        return
    if digest:
        append_history(digest["items"], mode)
    write_json(SEEN_FILE, seen)
    slot = f"{datetime.now(PARIS).date()}-{mode}"
    state["done_slots"] = (state["done_slots"] + [slot])[-10:]
    write_json(STATE_FILE, state)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["auto", "morning", "evening"], default="auto")
    parser.add_argument("--dry-run", action="store_true", help="affiche sans envoyer ni sauvegarder")
    args = parser.parse_args()
    try:
        run(args.mode, args.dry_run)
    except Exception as e:
        print(f"[ERREUR] {e}", file=sys.stderr)
        if not args.dry_run:
            try:
                send(f"⚠️ <b>Bot veille IA en erreur</b>\n<code>{esc(type(e).__name__)}: {esc(str(e))[:300]}</code>")
            except Exception:
                pass
        raise


if __name__ == "__main__":
    main()
