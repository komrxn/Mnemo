# Mnemo — AI assistan for your Brain 

> A Telegram bot that thinks with you, remembers everything, and organizes your life into a living knowledge graph.

**Translations:** [中文](docs/README_zh.md) · [Español](docs/README_es.md) · [Português](docs/README_pt.md) · [Français](docs/README_fr.md)

---

Mnemo is a self-hosted personal AI assistant that turns your Telegram conversations into structured Obsidian notes connected into a embedded knowledge graph. Every fact, project, person, or idea you mention gets extracted, linked, and stored — permanently and privately, on your own infrastructure.

```
You: "I had a call with Anna from the LegAI team today.
      We agreed to ship the MVP by June 15."

Mnemo: Noted. Created: Anna (People), LegAI (Projects),
       ship MVP (Task, due 2026-06-15). Linked everything.
```

---

## Features

- **Eternal memory** — every session is extracted into structured Obsidian notes with frontmatter, tags, and typed wikilinks
- **Knowledge graph** — notes are automatically connected through `[[wikilinks]]` and indexed into a semantic graph (LightRAG)
- **Deduplication** — fuzzy matching prevents duplicate notes ("LegAI" and "legai-project" resolve to the same entity)
- **Smart linker** — after each session, an LLM post-pass proposes typed relations (`for_project`, `works_at`, `about_person`, etc.)
- **Bidirectional typed links** — adding `for_project` on a task automatically adds `tasks: [...]` on the project
- **Maps of Content** — `_meta/MOC_People.md`, `MOC_Projects.md`, etc. auto-regenerate as navigable index pages
- **Multimodal input** — text, voice (Whisper transcription), and images (GPT-4 Vision)
- **Custom personality** — name your assistant and choose its communication style during onboarding
- **Proactive reminders** — APScheduler cron tasks: morning digest, weekly reflection, stale project checks
- **Git-backed vault** — every note commit is versioned; `/undo` reverts the last change
- **Topic shift detection** — when you change subjects, the previous session is auto-closed and saved
- **Full-text + semantic search** — ripgrep for exact matches, LightRAG for concept queries
- **Single-user, fully private** — whitelist-only, all data stays in your own Docker + git

---

## Architecture

```
Telegram ──► aiogram 3 handlers
                │
                ├── text / voice / photo
                │         │
                │    ChatActionSender (typing indicator)
                │         │
                │    process_input()
                │         │
                │    agent/loop.py ──► OpenAI GPT-4o (function calling)
                │         │
                │    tools/ ──► obsidian.* / scheduler.* / lightrag.*
                │
                └── session/manager.py ──► Redis
                         │
                    session close
                         │
                    agent/extractor.py ──► vault/writer.py ──► .md files
                    agent/linker.py    ──► vault/linking.py ──► typed links
                                                   │
                                             git_ops.py ──► GitHub (SSH)
                                                   │
                                         lightrag_svc/ ──► knowledge graph
```

**Stack:** Python 3.12, uv, aiogram 3, OpenAI SDK ≥1.57, Redis, APScheduler 3, LightRAG (embedded), rapidfuzz, ripgrep, structlog, pydantic v2, Docker

---

## Quick Start

### 1. Prerequisites

- Docker + Docker Compose
- A Telegram bot token — create one with [@BotFather](https://t.me/BotFather)
- An OpenAI API key — get one at [platform.openai.com](https://platform.openai.com/api-keys)
- Your Telegram user ID — find it with [@userinfobot](https://t.me/userinfobot)

### 2. Clone & configure

```bash
git clone https://github.com/yourname/mnemo.git
cd mnemo
cp .env.example .env
```

Edit `.env` — fill in the three required values:

```env
TELEGRAM_BOT_TOKEN=your_token_here
OPENAI_API_KEY=sk-...
ALLOWED_USER_IDS=123456789
TZ=Europe/London        # your timezone
```

### 3. (Optional) SSH deploy key for vault sync

Skip this step if you don't want to sync your vault to GitHub.

```bash
# Create the directory first (it's git-ignored, won't be committed)
mkdir -p secrets

# Generate a key
ssh-keygen -t ed25519 -C "mnemo-vault" -f secrets/vault_ssh_key -N ""

# Add secrets/vault_ssh_key.pub to your GitHub repo:
# Settings → Deploy keys → Add deploy key → check "Allow write access"

chmod 600 secrets/vault_ssh_key
```

Then set in `.env`:
```env
VAULT_GIT_REMOTE=git@github.com:yourname/your-vault.git
```

### 4. Build & run

```bash
docker compose up -d
docker compose logs -f bot
```

### 5. Onboard via Telegram

1. Open Telegram, send `/start` to your bot
2. Give the assistant a name (e.g. "Max", "Mia", "Mnemo")
3. Choose a communication style (friendly / direct / sarcastic / mentor)
4. Tell the assistant your name
5. Send a free-form portrait about yourself — projects, people, goals, interests
6. Confirm the plan → your vault is live

### 6. Connect Obsidian (optional)

```bash
# If you set up git sync:
git clone git@github.com:yourname/your-vault.git ~/MyVault
```

Open the folder as an Obsidian vault. Install the **Obsidian Git** community plugin for auto-pull every 5 min.

---

## Vault Structure

```
vault/
├── _meta/
│   ├── owner.md             # you — the central anchor node
│   ├── portrait.md          # your onboarding portrait (raw text)
│   ├── ontology.md          # auto-generated LightRAG entity types
│   ├── scheduled_tasks.md   # active cron tasks (human-readable)
│   ├── MOC_People.md        # auto-generated index of all people
│   ├── MOC_Projects.md      # auto-generated index of all projects
│   ├── MOC_Jobs.md          # auto-generated index of all jobs
│   └── MOC_Themes.md        # auto-generated index of all themes
├── 00_Inbox/                # unprocessed captures
├── 10_Daily/                # daily session notes
├── 20_People/               # people in your life
├── 30_Jobs/                 # companies, organizations
├── 40_Projects/             # work & personal projects
├── 50_Tasks/                # tasks with deadlines
├── 60_Thoughts/             # ideas, observations
├── 70_Memories/             # personal facts, past events
├── 80_Themes/               # recurring themes (health, values, hobbies)
└── 90_Attachments/          # voice messages, images
```

Every note has YAML frontmatter with typed relation fields:

```yaml
---
type: task
status: open
due: 2026-06-15
for_project: "[[40_Projects/legai]]"
owner: "[[_meta/owner]]"
aliases: ["ship mvp", "legai launch"]
---
```

---

## Bot Commands

| Command | Description |
|---------|-------------|
| `/start` | Onboarding (first run) or status check |
| `/save` | Close current session and extract notes immediately |
| `/undo` | Revert the last vault commit |

---

## Configuration Reference

All settings go in `.env`. See `.env.example` for the full list with comments.

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `TELEGRAM_BOT_TOKEN` | ✅ | — | Bot token from @BotFather |
| `OPENAI_API_KEY` | ✅ | — | OpenAI API key |
| `ALLOWED_USER_IDS` | ✅ | — | Comma-separated Telegram user IDs |
| `TZ` | | `UTC` | Timezone for notes and scheduling |
| `VAULT_GIT_REMOTE` | | — | SSH URL of your vault GitHub repo |
| `OPENAI_MODEL_MAIN` | | `gpt-4o` | Main model (chat, extraction, linking) |
| `OPENAI_MODEL_FAST` | | `gpt-4o-mini` | Fast model (topic shift, compact tasks) |
| `SESSION_IDLE_TIMEOUT_MIN` | | `15` | Auto-save session after N minutes idle |
| `SENTRY_DSN` | | — | Sentry error tracking (optional) |

---

## Recommended Obsidian Graph Settings

For the cleanest graph view:

- **Filters → search:** `-path:_meta -path:90_Attachments -path:00_Inbox`
- **Display → Existing files only:** ON
- **Display → Orphans:** OFF
- **Forces → Repel:** 15

---

## Development

```bash
# Install dependencies
uv sync

# Run locally (requires Redis running)
uv run python -m src.main

# Lint
uv run ruff check src/
uv run ruff format src/

# Type check
uv run mypy src/ --strict

# Tests
uv run pytest
```

Pre-commit hooks run ruff + mypy automatically. Never use `--no-verify`.

---

## Security & Privacy

- **Single-user by design** — `ALLOWED_USER_IDS` whitelist enforced at middleware level; all other users are silently ignored
- **Your data stays yours** — nothing is stored outside your Docker instance and your own git repo
- **OpenAI only** — the only external service is OpenAI API (and optionally Sentry). No analytics, no telemetry
- **SSH key safety** — Docker mounts the key read-only; the bot copies it to `/tmp` at runtime with `0600` permissions
- **Git safeguards** — `--force`, `--no-verify`, `--hard` flags are blocked at the code level
- **Destructive actions** require explicit inline Telegram confirmation (e.g. note deletion)

---

## Contributing

Issues and PRs are welcome. Please read `CLAUDE.md` for coding standards before contributing.

1. Fork the repo
2. Create a feature branch: `git checkout -b feat/something`
3. Follow conventions in `CLAUDE.md`
4. Run `ruff check`, `mypy --strict`, `pytest` — all must pass
5. Open a PR with a clear description of what and why

---

## Author

Built by **Komron Khakimov**

- GitHub: [@komrxn](https://github.com/komrxn)
- Telegram: [@komrxn](https://t.me/komrxn)
- LinkedIn: [@komrxn](https://linkedin.com/in/komrxn)
- Instagram: [@komrxn](https://instagram.com/komrxn)
- Email: [komronkhakimov17@gmail.com](mailto:komronkhakimov17@gmail.com)

---

## License

MIT — see [LICENSE](LICENSE).
