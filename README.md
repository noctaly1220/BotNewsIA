# Bot veille IA → Telegram

Deux envois par jour (8h et 17h, heure de Paris) via GitHub Actions.

## Pipeline

1. Collecte RSS + Hacker News, fenêtre de 36 h, URLs normalisées, doublons supprimés.
2. Pré-filtre par mots entiers, score par règles (source, acteurs, lancement, fraîcheur, votes 👍/👎).
3. Lecture du texte complet des 15 meilleurs candidats (trafilatura).
4. Juge LLM : importance 1-5, bruit, doublons d'événement.
5. Rédaction LLM **uniquement à partir du texte lu** (JSON), rendu HTML Telegram par le code.
6. 1 message de synthèse + 1 message par actu, boutons 📖 / 👍 / 👎.

Matin : 5 actus max, importance ≥ 3. Soir : 3 max, importance ≥ 4, rien envoyé s'il n'y a rien d'important.

## Feedback

Les clics 👍/👎 sont lus au run suivant (`getUpdates`) et ajustent le score de la source (`feedback.json` → `votes`).

## Variables / secrets

`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `OPENAI_API_KEY`, `OPENAI_MODEL` (optionnels : `OPENAI_JUDGE_MODEL`, `OPENAI_WRITER_MODEL`).

## Local

```bash
pip install -r requirements.txt
python main.py --mode morning --dry-run
```

`--dry-run` : affiche le résultat sans envoyer ni sauvegarder.
