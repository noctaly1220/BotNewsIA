SYSTEM_PROMPT = """
Tu es un analyste de veille IA francophone.
Tu écris pour quelqu’un qui a un niveau 3,5/10 en IA :
- clair
- pédagogique
- concret
- pas trop technique
- mais suffisamment complet

Tu dois éviter les buzzwords inutiles.
Tu dois expliquer pourquoi l’information compte vraiment.
"""

def build_digest_prompt(items, mode="morning"):
    max_news = "5 à 7" if mode == "morning" else "3 à 5"
    style = "résumé complet du matin" if mode == "morning" else "update court de fin de journée"

    joined = "\n\n".join(
        f"- Titre: {item.get('title')}\n"
        f"  Source: {item.get('source')}\n"
        f"  Lien: {item.get('link')}\n"
        f"  Résumé brut: {item.get('summary', '')[:1200]}"
        for item in items
    )

    return f"""
Tu dois créer un {style} de veille IA en français.

Nombre d'actus à garder : {max_news}.
Ne garde que les nouvelles importantes.
Ignore les doublons et les articles faibles.

Pour chaque actu, utilise EXACTEMENT ce format :

### 🚀 1. Titre clair de l'actu

👉 Résumé :
Un résumé assez rempli, concret et compréhensible.
Il doit expliquer ce qui s'est passé, qui est concerné, et ce qui est nouveau.

👉 Contexte :
Explique d'où vient cette évolution, dans quelle tendance IA elle s'inscrit,
et pourquoi elle mérite d'être suivie. Le contexte doit être plus fourni que 2 lignes.

👉 Pourquoi c'est important :
- point 1
- point 2
- point 3

👉 Impact concret :
Explique les conséquences possibles pour les utilisateurs, développeurs,
entreprises, créateurs, ou personnes qui veulent automatiser des choses.

👉 Ce que ça change pour toi :
Explique simplement en quoi c'est utile ou intéressant pour une personne
qui veut apprendre l'IA et construire des bots/automatisations.

👉 Niveau d'importance :
⭐⭐⭐⭐☆ ou ⭐⭐⭐⭐⭐

🔗 Source :
lien exact

À la fin, ajoute :

## ⚡ TL;DR du jour
3 à 5 bullets clairs.

## 🧠 Tendance globale
Explique la tendance générale qui ressort des actus du jour.

## 🎯 À surveiller
1 à 3 choses à surveiller dans les prochains jours.

Voici les informations à analyser :

{joined}
"""
