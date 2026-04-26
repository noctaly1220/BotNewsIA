SYSTEM_PROMPT = """
Tu es un analyste de veille IA francophone.
Tu écris pour quelqu’un qui a un niveau 3,5/10 en IA.
Sois clair, pédagogique, concret, mais suffisamment complet.
"""

AI_JUDGE_PROMPT = """
Tu dois juger si ces actus IA sont vraiment importantes ou si c'est du bruit.

Critères d'importance :
- annonce officielle d'un acteur majeur
- sortie de modèle
- nouvelle fonctionnalité importante
- changement API/prix/accès
- tendance forte : agents, multimodal, code, open-source, automatisation
- outil vraiment utile ou émergent
- information business importante : financement, acquisition, partenariat

Bruit à filtrer :
- top prompts génériques
- simples opinions
- rumeurs non sourcées
- petits outils gadget
- memes / posts divertissement
- articles vagues sans nouveauté

Réponds uniquement en JSON valide sous cette forme :
[
  {
    "id": "id court fourni",
    "importance": 1,
    "is_noise": true,
    "reason": "raison courte"
  }
]

Importance :
1 = inutile
2 = faible
3 = correct
4 = important
5 = majeur
"""

def build_judge_prompt(items):
    joined = "\n\n".join(
        f"ID: {item.get('short_id')}\n"
        f"Titre: {item.get('title')}\n"
        f"Source: {item.get('source')}\n"
        f"Score règles: {item.get('rule_score')}\n"
        f"Résumé: {item.get('summary', '')[:800]}"
        for item in items
    )
    return f"Voici les actus à juger :\n\n{joined}"

def build_digest_prompt(items, mode="morning"):
    max_news = "5 à 7" if mode == "morning" else "3 à 5"
    style = "résumé complet du matin" if mode == "morning" else "update court de fin de journée"

    joined = "\n\n".join(
        f"- Titre: {item.get('title')}\n"
        f"  Source: {item.get('source')}\n"
        f"  Lien: {item.get('link')}\n"
        f"  Score final: {item.get('final_score')}\n"
        f"  Importance IA: {item.get('ai_importance')}/5\n"
        f"  Raison sélection: {item.get('ai_reason', '')}\n"
        f"  Résumé brut: {item.get('summary', '')[:1200]}"
        for item in items
    )

    return f"""
Tu dois créer un {style} de veille IA en français.

Nombre d'actus à garder : {max_news}.
Ne garde que les nouvelles importantes.

Pour chaque actu :

### 🚀 1. Titre clair de l'actu

👉 Résumé :
Un résumé assez rempli, concret et compréhensible.

👉 Contexte :
Explique d'où vient cette évolution, dans quelle tendance IA elle s'inscrit,
et pourquoi elle mérite d'être suivie.

👉 Pourquoi c'est important :
- point 1
- point 2
- point 3

👉 Impact concret :
Explique les conséquences possibles.

👉 Ce que ça change pour toi :
Explique simplement en quoi c'est utile pour apprendre l'IA ou construire des bots.

👉 Niveau d'importance :
⭐⭐⭐⭐☆ ou ⭐⭐⭐⭐⭐

🔗 Source :
lien exact

À la fin, ajoute :

## ⚡ TL;DR du jour
3 à 5 bullets.

## 🧠 Tendance globale
Tendance générale qui ressort.

## 🎯 Top opportunité business
Une idée concrète inspirée des actus du jour.

## 📚 Concept IA à comprendre
Un concept IA lié aux actus.

Infos à analyser :

{joined}
"""
