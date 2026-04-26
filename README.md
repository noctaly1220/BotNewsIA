# Bot de veille IA → Telegram

Ce projet est une base prête à configurer pour recevoir une veille IA automatique sur Telegram.

## Fonctionnement

Le bot peut tourner :
- à 7h : résumé complet du matin
- à 18h : update plus court

Il récupère les actus depuis :
- sources officielles IA
- blogs/news tech
- Reddit
- Hacker News
- Product Hunt
- newsletters plus tard via Gmail dédié

## Ce que le bot fait

1. Récupère les nouveaux articles/posts.
2. Supprime les doublons.
3. Filtre les sujets importants.
4. Génère un résumé en français.
5. Ajoute les liens sources.
6. Envoie le tout sur Telegram.

## Structure

```txt
ai_veille_telegram_bot/
├── main.py
├── sources.yaml
├── prompt_templates.py
├── requirements.txt
├── .github/workflows/ai-watch.yml
├── .env.example
└── README.md
```

## Configuration Telegram plus tard

Tu devras créer un bot avec BotFather puis ajouter :

```env
TELEGRAM_BOT_TOKEN=ton_token
TELEGRAM_CHAT_ID=ton_chat_id
OPENAI_API_KEY=ta_cle_api
```

Dans GitHub Actions, il faudra mettre ces valeurs dans :

```txt
Settings → Secrets and variables → Actions → New repository secret
```

## Lancement local

```bash
pip install -r requirements.txt
python main.py --mode morning
python main.py --mode evening
```

## Modes

```bash
python main.py --mode morning
```

Résumé complet, 5 à 7 actus.

```bash
python main.py --mode evening
```

Update plus court, 3 à 5 actus importantes seulement.

## GitHub Actions

Le workflow est prévu pour tourner 2 fois par jour.

Attention : GitHub Actions utilise l’heure UTC.
Le workflow fourni utilise une exécution vers 7h et 18h heure de Paris en heure d’été.

Tu pourras ajuster plus tard selon l’hiver/été.
