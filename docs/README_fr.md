# Mnemo — Deuxième Cerveau IA

> Un bot Telegram qui pense avec vous, se souvient de tout et organise votre vie en un graphe de connaissances vivant.

**Autres langues :** [English](../README.md) · [中文](README_zh.md) · [Español](README_es.md) · [Português](README_pt.md)

---

Mnemo est un assistant IA personnel auto-hébergé qui transforme vos conversations Telegram en notes Obsidian structurées, connectées dans un graphe de connaissances. Chaque fait, projet, personne ou idée que vous mentionnez est extrait, lié et stocké de façon permanente et privée sur votre propre infrastructure.

```
Vous : "J'ai eu un appel avec Anna de l'équipe LegAI aujourd'hui.
        On a convenu de lancer le MVP avant le 15 juin."

Mnemo : Noté. Créé : Anna (Personnes), LegAI (Projets),
        lancer le MVP (Tâche, échéance 2026-06-15). Tout est lié.
```

---

## Fonctionnalités

- **Mémoire éternelle** — chaque session est extraite en notes Obsidian structurées avec frontmatter, tags et wikilinks typés
- **Graphe de connaissances** — les notes sont automatiquement connectées via `[[wikilinks]]` et indexées dans un graphe sémantique (LightRAG)
- **Déduplication** — la correspondance floue évite les notes en double (« LegAI » et « legai-projet » désignent la même entité)
- **Lieur intelligent** — après chaque session, un LLM propose des relations typées (`for_project`, `works_at`, `about_person`, etc.)
- **Liens bidirectionnels typés** — ajouter `for_project` sur une tâche ajoute automatiquement `tasks: [...]` sur le projet
- **Cartes de Contenu** — `_meta/MOC_People.md`, `MOC_Projects.md`, etc. sont régénérés automatiquement
- **Entrée multimodale** — texte, voix (transcription Whisper) et images (GPT-4 Vision)
- **Personnalité personnalisée** — nommez votre assistant et choisissez son style de communication lors de l'intégration
- **Rappels proactifs** — résumé matinal, réflexion hebdomadaire, vérification des projets abandonnés
- **Vault sauvegardé Git** — chaque commit de note est versionné ; `/undo` annule la dernière modification
- **Entièrement privé** — liste blanche uniquement, toutes les données restent dans votre propre Docker + git

---

## Démarrage rapide

### 1. Prérequis

- Docker + Docker Compose
- Token de bot Telegram — créez-en un avec [@BotFather](https://t.me/BotFather)
- Clé API OpenAI — obtenez-la sur [platform.openai.com](https://platform.openai.com/api-keys)
- Votre ID utilisateur Telegram — trouvez-le avec [@userinfobot](https://t.me/userinfobot)

### 2. Cloner et configurer

```bash
git clone https://github.com/yourname/mnemo.git
cd mnemo
cp .env.example .env
```

Éditez `.env` — remplissez les trois valeurs requises :

```env
TELEGRAM_BOT_TOKEN=votre_token_ici
OPENAI_API_KEY=sk-...
ALLOWED_USER_IDS=123456789
TZ=Europe/Paris
```

### 3. Construire et lancer

```bash
docker compose up -d
docker compose logs -f bot
```

### 4. Intégration via Telegram

1. Ouvrez Telegram, envoyez `/start` à votre bot
2. Donnez un nom à l'assistant (ex : « Max », « Mia », « Mnemo »)
3. Choisissez un style de communication
4. Dites à l'assistant votre prénom
5. Envoyez un texte libre sur vous-même — projets, personnes, objectifs, intérêts
6. Confirmez le plan → votre vault est actif

---

## Structure du Vault

```
vault/
├── _meta/           # fichiers système (propriétaire, portrait, ontologie, MOC)
├── 00_Inbox/        # captures non traitées
├── 10_Daily/        # notes de session quotidienne
├── 20_People/       # personnes dans votre vie
├── 30_Jobs/         # entreprises, organisations
├── 40_Projects/     # projets professionnels et personnels
├── 50_Tasks/        # tâches avec échéances
├── 60_Thoughts/     # idées, observations
├── 70_Memories/     # faits personnels, événements passés
├── 80_Themes/       # thèmes récurrents (santé, valeurs, loisirs)
└── 90_Attachments/  # messages vocaux, images
```

---

## Commandes du Bot

| Commande | Description |
|----------|-------------|
| `/start` | Intégration (première fois) ou vérification du statut |
| `/save` | Fermer la session en cours et extraire les notes immédiatement |
| `/undo` | Annuler le dernier commit du vault |

---

## Connecter à des Outils IA de Développement

Mnemo expose son graphe de connaissances comme serveur MCP, permettant à **Claude Code, Cursor, Cline** et autres outils compatibles de consulter votre second cerveau pendant que vous codez.

Le paquet n'est pas encore sur PyPI — installez depuis les sources :

```bash
git clone https://github.com/desimpkins/daniel-lightrag-mcp.git
cd daniel-lightrag-mcp && pip install -e .
```

Ajoutez ceci dans `~/.claude/claude_mcp_config.json` pour Claude Code :

```json
{
  "mcpServers": {
    "mnemo-brain": {
      "command": "daniel-lightrag-mcp",
      "env": {
        "LIGHTRAG_BASE_URL": "http://localhost:9621",
        "LIGHTRAG_API_KEY": "<contenu de secrets/lightrag_api_key.txt>"
      }
    }
  }
}
```

Guide complet : [README anglais](../README.md#connect-your-brain-to-ai-coding-tools)

---

## Sécurité et Confidentialité

- **Conception mono-utilisateur** — liste blanche `ALLOWED_USER_IDS` appliquée au niveau du middleware
- **Vos données vous appartiennent** — rien n'est stocké en dehors de votre infrastructure, sauf les appels à l'API OpenAI
- **Protections Git** — les drapeaux `--force`, `--no-verify`, `--hard` sont bloqués au niveau du code

---

## Auteur

Créé par **Komron Khakimov**

- GitHub : [@komrxn](https://github.com/komrxn)
- Telegram : [@komrxn](https://t.me/komrxn)
- LinkedIn : [@komrxn](https://linkedin.com/in/komrxn)
- Instagram : [@komrxn](https://instagram.com/komrxn)
- Email : [komronkhakimov17@gmail.com](mailto:komronkhakimov17@gmail.com)

---

## Licence

MIT — voir [LICENSE](../LICENSE).
