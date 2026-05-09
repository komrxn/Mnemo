# Mnemo — Project Memory (handoff document)

> Назначение этого файла: если сессия с моделью внезапно сломается, любая
> новая модель должна прочитать этот файл и **сразу понять** что за проект,
> что сделано, что не работало, какие решения приняты, и что происходит
> прямо сейчас. Никаких внешних знаний — всё нужное в этом файле.

---

## 1. Что за проект

**Mnemo** — single-user Telegram-бот = «внешний мозг». Конкретно:

- Юзер шлёт боту текст / голос / фото в Telegram
- Бот извлекает структурированную информацию (typed entities) из диалога
- Информация сохраняется как **Markdown-заметки** в Obsidian Vault (через git-commit'ы)
- Параллельно строится **типизированный граф знаний** в LightRAG (через custom KG injection — без LLM-extraction)
- Граф экспонируется как **MCP-сервер** на `127.0.0.1:9621`, к которому подключается Claude Code / Cursor / Cline для чтения «второго мозга» во время кодинга

**Single user.** Whitelist по `ALLOWED_USER_IDS` в `.env`.

**Стек:** Python 3.12 · uv · aiogram 3 · OpenAI SDK ≥1.57 · Redis 7 · APScheduler 3 · LightRAG (embedded в боте + HTTP сервер для MCP) · rapidfuzz · ripgrep · structlog · pydantic v2 · Docker Compose.

**Модели OpenAI:** `gpt-5.4` (main), `gpt-5.4-mini` (fast / topic-shift / owner-refresh / `_create_owner_note`), `whisper-1` (voice), `text-embedding-3-large` (3072-dim embeddings для LightRAG).

⚠️ **Никаких gpt-4o / gpt-4-turbo** в проекте — это требование владельца, зафиксировано в memory `feedback_models.md`.

---

## 2. Топология

```
mnemo/
├── data/                       (gitignored — runtime data)
│   ├── vault/                  Obsidian vault (markdown + git)
│   │   ├── _meta/
│   │   │   ├── owner.md        ⭐ anchor графа (type=person, is_owner=true)
│   │   │   ├── portrait.md     сырой архив онбординг-портрета
│   │   │   ├── ontology.md     ⚠️ legacy от Phase D2 (не генерится больше)
│   │   │   ├── scheduled_tasks.md
│   │   │   ├── MOC_People.md   автогенерируется после сессий
│   │   │   ├── MOC_Projects.md
│   │   │   ├── MOC_Jobs.md
│   │   │   └── MOC_Themes.md
│   │   ├── 00_Inbox/
│   │   ├── 10_Daily/
│   │   ├── 20_People/
│   │   ├── 30_Jobs/
│   │   ├── 40_Projects/
│   │   ├── 50_Tasks/
│   │   ├── 60_Thoughts/
│   │   ├── 70_Memories/
│   │   ├── 80_Themes/
│   │   └── 90_Attachments/
│   ├── lightrag/               LightRAG storage (graphml + vdb_*.json)
│   └── redis/
├── secrets/                    (gitignored)
│   ├── lightrag_api_key.txt
│   └── vault_ssh_key
├── prompts/                    (Jinja2 templates)
│   ├── onboarding.md           переписан на семантический dialog + few-shot
│   ├── session_extract.md      переписан, инжектится vault_map
│   ├── system.md               main agent prompt
│   ├── topic_shift.md          для topic-shift detector
│   └── proactive.md            для scheduled prompts
├── src/
│   ├── agent/
│   │   ├── extractor.py        session → vault (через write_pipeline)
│   │   ├── linker.py           smart linker post-pass
│   │   ├── loop.py             OpenAI agent loop + run_chat
│   │   ├── owner_refresh.py    ⭐ NEW v4 — auto-refresh owner.md
│   │   └── prompts.py          Jinja2 loader
│   ├── lightrag_svc/
│   │   ├── client.py           LightRAG singleton + query() с only_need_context
│   │   ├── converter.py        vault → custom_kg для ainsert_custom_kg
│   │   ├── indexer.py          index_files / full_reindex
│   │   ├── graph_sync.py       handle_rename / handle_delete (граф ↔ vault)
│   │   └── reindex_queue.py    debounce queue (TTL=2s, dedup)
│   ├── multimodal/
│   │   ├── whisper.py
│   │   └── vision.py           (max_completion_tokens, async file read)
│   ├── safety/
│   │   └── confirmations.py    inline-button confirms через Redis pubsub
│   ├── scheduler/
│   │   ├── apsched.py          AsyncIOScheduler + RedisJobStore
│   │   ├── defaults.py         5 дефолтных задач + vault_pull_sync
│   │   └── triggers.py         _handle_system_task + _do_vault_pull_sync
│   ├── session/
│   │   ├── manager.py          Redis sessions + key helpers
│   │   ├── lifecycle.py        run_forever — idle session closer
│   │   ├── locks.py            ⭐ NEW v4 — per-user redis lock
│   │   └── topic_shift.py      LLM-based topic-shift detection
│   ├── telegram/
│   │   ├── bot.py              aiogram Dispatcher + WhitelistMiddleware
│   │   ├── keyboards.py        inline keyboards
│   │   └── handlers/
│   │       ├── commands.py     /start (с auto-onboarding и safety) /save /undo
│   │       ├── text.py         process_input + coalesce + auto-recall + onboarding flow
│   │       ├── voice.py
│   │       └── photo.py
│   ├── tools/
│   │   ├── registry.py
│   │   ├── obsidian.py         search_existing_entities + create_note (refactored)
│   │   ├── lightrag.py         kg_query / kg_get_entity / kg_get_related
│   │   ├── scheduler.py        schedule_task / list / update / cancel
│   │   └── misc.py             get_datetime / profile / fetch_url / confirmations
│   └── vault/
│       ├── entity.py           ⭐ NEW v4 — strict Entity + Relation contract
│       ├── write_pipeline.py   ⭐ NEW v4 — единая точка записи
│       ├── vault_map.py        ⭐ NEW v4 — карта vault для системного промпта
│       ├── paths.py            ⭐ NEW v4 — resolve_inside_vault, traversal guard
│       ├── frontmatter.py      pydantic Frontmatter + serialize/parse + slugify
│       ├── reader.py           note_exists / read_note (через resolve_inside_vault)
│       ├── writer.py           write_note / append_to_note / update_frontmatter / move / delete
│       ├── git_ops.py          stage_and_commit / push / pull_with_diff / revert
│       ├── linking.py          add_typed_link с inverse generation
│       ├── dedup.py            find_similar (rapidfuzz, по type-folder)
│       ├── moc.py              regenerate_moc для person/project/job/theme
│       └── search.py           ripgrep wrapper
├── tests/                      40 тестов, ruff clean
│   ├── conftest.py
│   ├── test_dedup.py
│   ├── test_typed_links.py
│   ├── test_linker.py
│   ├── test_moc.py
│   ├── test_converter.py
│   ├── test_graph_sync.py
│   ├── test_reindex_queue.py
│   ├── test_vault_sync.py
│   ├── test_entity_contract.py    ⭐ NEW v4
│   └── test_paths_safety.py       ⭐ NEW v4
├── scripts/
│   ├── init_lightrag_api_key.sh
│   └── lightrag_entrypoint.sh  передаёт --key в lightrag-server
├── Dockerfile                  bot
├── Dockerfile.lightrag         lightrag-api
├── docker-compose.yml          3 сервиса с healthchecks
├── setup.sh                    one-command bootstrap
├── .env.example                все vars документированы
├── pyproject.toml              uv-managed, ruff config (RUF001/2/3 ignore — Cyrillic project)
├── IMPLEMENTATION_PLAN.md      v2 LEGACY/CLOSED — историческая справка
├── IMPLEMENTATION_PLAN_V3.md   v3 закрыт
├── AUDIT_AND_REFACTOR_PLAN.md  ⭐ v4 architecture (этот рефакторинг)
└── Memory.md                   ← этот файл
```

---

## 3. Data flow (как оно реально работает)

### 3.1. Юзер шлёт сообщение в Telegram

```
Telegram update
  → aiogram WhitelistMiddleware (drop если user не в ALLOWED_USER_IDS)
  → router (commands → confirm_callbacks → voice → photo → text)
  → handle_text → _coalesce_text_message
```

`_coalesce_text_message` — debounce обёртка через Redis INCR token. Если юзер шлёт 3 сообщения за 1.5 сек, обработается **только последний** хендлер, и он увидит **все три** склеенными.

### 3.2. process_input

После coalesce → `process_input(user_id, content)`:

1. **Onboarding gate:** `_handle_onboarding(user_id, content)` — если в Redis есть state `step_*` или `agent_question` — продолжаем онбординг и `return`
2. **Auto-onboarding gate** ⭐ NEW v4: если нет `_meta/portrait.md` И нет onboarding state — стартуем онбординг автоматически (не нужно `/start`)
3. **Get-or-create session** в Redis (`session:active:<user_id>`)
4. **Topic-shift detector** (если len(history) ≥ 4): LLM-вызов через `gpt-5.4-mini` → если shift detected, закрываем старую сессию (через `_run_pipeline_bg` background task) и открываем новую
5. **Auto-recall** ⭐ NEW v4: `_fetch_recall_context(content)` — query LightRAG с `only_need_context=True` через embeddings, top-5 chunks. Инжектится в системный промпт как «Контекст из твоей долгосрочной памяти: ...»
6. **Run agent loop** через `agent_loop.run_chat` (max_rounds=15)
7. **Reply fingerprint** ⭐ NEW v4: SHA-256 hash последних 5 ответов. Если новый дублирует — log + skip (защита от LLM-повтора и Telegram retry)
8. **Push msg** в session.msgs + `await reply_fn(reply)`

### 3.3. Сессия закрывается (`/save` или 15 мин idle)

`commands.cmd_save` или `lifecycle._close_one` →
```
session_mgr.close_session
  → session_mgr.get_msgs
  → extractor.run_pipeline(session, msgs)
      → _compact_msgs (если > 50 messages)
      → extract(msgs) — structured output SessionExtraction
          (system prompt + vault_map injection)
      → apply_to_vault(extraction, session_id)
          → for entity in entities:
              → _apply_entity → write_pipeline.write_entity(Entity)
                  → find_similar dedup (threshold=85 hard, 70 soft)
                  → render_body programmatically
                  → write_note OR append_to_note
                  → add_typed_link для non-owner relations (с inverse generation)
                  → reindex_queue.enqueue
          → standalone thoughts/memories
          → _update_daily (heroes-only links + notes_touched в frontmatter)
          → links_to_create через _add_link
          → smart linker post-pass (linker.propose_links + apply_links)
          → MOC regen (для person/project/job/theme)
          → owner.md auto-refresh ⭐ NEW v4 (если затронут job/memory/theme)
          → git_ops.push (graceful — если SSH сломан, не падает pipeline)
          → reindex_queue uses internal debounce (TTL=2s coalescing)
```

### 3.4. LightRAG индексация (custom KG injection)

```
reindex_queue.enqueue(paths)
  → _flush_loop debounce 2 sec
  → indexer.index_files(paths)
      → converter.vault_to_custom_kg(paths)
          → строит chunks/entities/relationships из frontmatter
      → rag.ainsert_custom_kg(custom_kg)  ← НЕТ LLM-extraction!
          → embeds chunks/entities/relationships
          → upsert в graph_chunk_entity_relation.graphml
          → upsert в vdb_*.json (vector dbs)
```

**Главная фишка Phase H v2:** мы не позволяем LightRAG делать свой LLM-extraction. Мы **сами** строим граф из typed wikilinks в frontmatter и скармливаем готовый. Один источник правды (Obsidian) = граф (LightRAG). Стоимость per note = ~$0.0001 (только embeddings).

### 3.5. MCP-доступ из Claude Code

Claude Code запускает `mnemo-brain-mcp` (наш PyPI пакет) → он по stdio-MCP → ходит в `http://localhost:9621` (наш `lightrag-api` Docker сервис) → читает graph + chunks. Auth через `X-API-Key` (genned at setup).

### 3.6. Vault auto-sync ⭐ Phase I v3

Юзер правит markdown руками в Obsidian (через Obsidian Git plugin или прямо). Каждые 2 минуты scheduler-задача `vault_pull_sync` →
```
git_ops.pull_with_diff
  → git pull --rebase
  → git diff --name-status -M70 HEAD@{1} HEAD
  → классифицирует added/modified/deleted/renamed
  → enqueue (added+modified) + handle_rename + handle_delete
```

При конфликте — алерт юзеру в Telegram, **никакого auto-resolve** (CLAUDE.md §3 запрет).

---

## 4. Семантика графа (КРИТИЧНО — как устроен «мозг»)

### 4.1. 9 типов заметок

| Тип | Папка | Что это | Имеет owner? |
|---|---|---|---|
| person | 20_People/ | Конкретный человек | **НЕТ** (через works_at/about_person/related_people) |
| job | 30_Jobs/ | Место работы / учёбы | ✅ всегда |
| project | 40_Projects/ | Проект (личный или рабочий) | ✅ всегда |
| task | 50_Tasks/ | Задача с дедлайном | ✅ всегда |
| thought | 60_Thoughts/ | Мысль, инсайт | ✅ всегда |
| memory | 70_Memories/ | Воспоминание, факт жизни | ✅ всегда |
| theme | 80_Themes/ | Тема жизни, интерес | ✅ всегда |
| inbox | 00_Inbox/ | Незавершённое | — (не сущность графа) |
| daily | 10_Daily/ | Дневная сводка | — (заполняется автоматически) |

### 4.2. 8 типов связей (forward) + автогенерируемые инверсии

| forward | где | куда | inverse | где (target) |
|---|---|---|---|---|
| `owner` | jobs/projects/tasks/thoughts/memories/themes | `_meta/owner` | **(нет)** — намеренно. Иначе owner.md = свалка |
| `works_at` | person | job | `employs` | job |
| `for_job` | project/task | job | `projects`/`tasks` (динамически по source_type) | job |
| `for_project` | task/thought/memory | project | `tasks`/`thoughts`/`memories` (динамически) | project |
| `themes` | project/thought/memory | theme (list) | `examples` | theme |
| `about_person` | thought/memory | person | `mentions` | person |
| `related_people` | project/job/memory | person (list) | `involved_in` | person |
| `parent_theme` | theme | theme | `sub_themes` | theme |

Инверсии генерируются автоматически в `linking.add_typed_link`. Один git-commit на пару (forward + inverse).

### 4.3. Принципы (важно)

1. **Owner в центре графа.** Все типы кроме `person` имеют прямой `owner: [[_meta/owner]]`.
2. **Person транзитивно.** Person НЕ имеет owner-поля. Связь person → owner идёт через works_at→job→owner или через about_person/related_people.
3. **Иерархия тем через `parent_theme`.** «Claude» → parent → «Разработка».
4. **Никаких дублей.** Одна сущность = одна заметка. Дедуп через rapidfuzz (string similarity) + LightRAG embeddings (semantic similarity).
5. **Имена на родном языке.** slugify `allow_unicode=True` — `аня-петрова.md`, не `anya-petrova.md`.

### 4.4. Дефолтное древо после онбординга

```
                    _meta/owner.md (anchor)
                          │
       ┌──────────┬───────┼───────┬─────────┐
     JOBS      PROJECTS  MEMORIES THEMES   (related_people от owner — нет)
                                            
       │          │           │
   employs    for_job    about_person  
   (inverse)               
                
   PEOPLE    TASKS / THOUGHTS / детали-MEMORIES (через for_project / for_job)
   (через                
   works_at)              
```

---

## 5. История реализации (что было, что есть)

### v1 (до меня) — базовая инфра
✅ Telegram-бот aiogram 3 (whitelist, typing-indicator, multimodal)
✅ Redis-сессии с sliding TTL, /save, /undo
✅ Vault writer + git commit
✅ Agent loop с function calling и retries
✅ APScheduler с RedisJobStore + 5 дефолтных задач
✅ Inline-confirms через Redis pub/sub
✅ Multi-step onboarding (имя → стиль → портрет → подтверждение)
✅ Docker compose

### v2 (фазы A-K) — типизированный граф + custom KG
🟡 КОД написан, runtime не валидирован полностью; см. IMPLEMENTATION_PLAN.md (LEGACY)

- **A** — Owner anchor + dedup через rapidfuzz
- **B** — Smart linker post-pass через structured outputs
- **C** — Frontmatter-driven typed links + bidirectional rendering + `## Связи` section
- **D** — LightRAG ontology через addon_params, `mode="mix"` дефолт
- **E** — MOC + daily-note hygiene (heroes-only)
- **F** — MCP-мост (lightrag-server в отдельном Docker сервисе)
- **G** — Hot-fixes Phase F (auth via entrypoint, env, healthchecks, тесты, mnemo-brain-mcp packaging)
- **H** — Custom KG injection (`ainsert_custom_kg` вместо `ainsert`, **0 LLM-extraction**) + rename/delete graph_sync hooks
- **K** — Dockerfile.lightrag + entrypoint для API key injection

### v3 (фазы L-K) — brain-style graph + self-sync
✅ См. IMPLEMENTATION_PLAN_V3.md

- **L** — Семантические промпты (заменили чеклисты на семантику + few-shot)
- **M** — Multi-turn onboarding с `[ONBOARDING_DONE]` sentinel
- **J** — Reindex debounce queue (TTL=2s)
- **I** — Vault pull-and-sync (Obsidian → LightRAG через git pull каждые 2 мин)
- **K** — mnemo-brain-mcp на PyPI v0.1.0 (форк desimpkins/daniel-lightrag-mcp)

### v4 (этот рефакторинг) — senior-grade architecture
✅ См. AUDIT_AND_REFACTOR_PLAN.md

- **Этап 1 — архитектурный фундамент:**
  - `src/vault/entity.py` — strict-typed `Entity` + `Relation` контракт с pydantic-validators
    (regex anti-3-person в canonical_name, `min_length`/`max_length` на one_liner, etc.)
  - `src/vault/write_pipeline.py` — единая точка записи `write_entity(Entity)` с dedup + render_body + add_typed_link
  - `src/vault/vault_map.py` — компактная карта vault для system prompt (закрывает Group A — слепая запись)
  - `src/vault/paths.py` — `resolve_inside_vault` с path-traversal guard
  - `tools/obsidian.search_existing_entities` — обязательная READ-перед-WRITE тулза с Redis-gate
  - `_create_note` рефакторнут под `write_entity` — удалены 150 строк defensive костылей
  - extractor `_apply_entity` тоже через `write_entity`
  - vault_map injection в session_extract + onboarding промпты
  - переписан onboarding.md с few-shot examples + DON'T-блок

- **Этап 2 — concurrency:**
  - `src/session/locks.py` — per-user Redis lock (для будущих критических секций)
  - `confirmations.request_confirmation` — фикс broken `pubsub.listen()` deadline через `pubsub.get_message(timeout=...)`

- **Этап 3 — operational hardening:**
  - `reader.note_exists` / `read_note` через `resolve_inside_vault` (закрывает Group F)
  - `linking.add_typed_link` с traversal guard
  - `git_ops.push` graceful в extractor (если SSH сломан, pipeline не падает)
  - `cmd_save` — guard на пустую сессию
  - `cmd_undo` — safe-list (отказывает если в HEAD коммите owner.md / portrait.md)
  - `vision.describe` — async file read через `to_thread` + `max_completion_tokens`
  - `_fetch_url` (новый tool) — bs4 + lxml для HTML→text, ban localhost (anti-SSRF)
  - reply fingerprint в `process_input` (sha256 last-5)

- **Этап 4 — наблюдаемость:**
  - (отложено) correlation_id middleware, sentry на каждый logger.error

- **Этап 5 — UX:**
  - `tools/misc.get_user_profile` баг fixed (читал `user:profile:main` вместо `key_profile(user_id)`)
  - `cmd_start` — резет stale onboarding state + safe-list гард если уже онбординг'нут
  - **Auto-onboarding** ⭐ — любое первое сообщение от не-онбординг'д юзера запускает онбординг (раньше только `/start`)
  - **Owner.md 2-фазный** ⭐ — placeholder при старте онбординга → enrichment после `[ONBOARDING_DONE]` из ВСЕГО диалога
  - **Owner.md auto-refresh** ⭐ — после каждого `/save` если затронут job/memory/theme — пересобираем owner.md из текущего vault через LLM (gpt-5.4-mini)
  - **Auto-recall middleware** ⭐ — перед каждым user message → query LightRAG с `only_need_context=True` → инжектится в system prompt. Закрывает recall-bug «бот забывает что год назад говорили».
  - **Semantic dedup** ⭐ — `search_existing_entities` теперь использует ДВА gate: rapidfuzz (string) + LightRAG embeddings (semantic). Закрывает «AI» ↔ «искусственный интеллект» дубли.

---

## 6. Найденные баги и решения (история граблей)

### Группа A — Слепая запись (агент не видит state vault)
**Симптомы:** дубли тем (`ai` ↔ `искусственный-интеллект`), дубли memory про Ханчжоу (×3), typed_links с пробелами в target, short-name `[[AI]]`, потеря typed_links после /save.
**Решение:** `vault_map` injection в системный промпт + `search_existing_entities` обязательно перед `create_note` + semantic dedup через embeddings.

### Группа B — Слабая tool-schema
**Симптомы:** двойной frontmatter в body, `[[[[X]]]]` quad-bracket, `## Связи` руками, обобщённое body «Рабочий проект komron», title в 3-м лице.
**Решение:** strict-typed `Entity` + `Relation` с pydantic-validators. Markdown в `one_liner` отвергается на parse. Wikilinks нормализуются автоматически. canonical_name с regex anti-3-person.

### Группа C — Раздутые промпты
**Симптомы:** owner.aliases смешан с Дашей; бот спрашивает что юзер уже отвечал; body-отписки.
**Решение:** Few-shot examples + DON'T-блок в промптах. Программная фильтрация aliases (rapidfuzz partial_ratio ≥ 60). open_questions правило «закрытое НЕ включай».

### Группа D — Race conditions
**Симптомы:** дубль ответа в Telegram, lifecycle vs process_input race на закрытие сессии.
**Решение:** `_coalesce_text_message` через Redis INCR token + `confirmations` через `get_message(timeout)` + `src/session/locks.py` (готов к использованию).

### Группа E — Error handling
**Симптомы:** push без try/except → весь pipeline падает после успешной записи; whisper без graceful; vision блокирует event loop.
**Решение:** graceful push в extractor; vision через `asyncio.to_thread`; reply fingerprint от LLM-повторов.

### Группа F — Path traversal
**Симптомы:** reader/linking без resolve_inside_vault.
**Решение:** `src/vault/paths.py` с `resolve_inside_vault` используется во всех reader/writer/linking путях.

### Группа G — Несогласованность данных
**Симптомы:** `tools/misc.get_user_profile` читал `user:profile:main` а update писал в `user:profile:<id>` — разные хранилища.
**Решение:** оба идут через canonical `session_mgr.get_profile(redis, user_id)`.

### Группа H — Operational
**Симптомы:** undo может revert'нуть онбординг; cmd_save на пустой сессии падает.
**Решение:** safe-list в cmd_undo; guard в cmd_save.

### Конкретные API-баги
- **`max_tokens` устарел** в новых OpenAI моделях → `max_completion_tokens`
- **`dict[str, list[str]]` не работает в strict mode** OpenAI structured outputs → заменено на `list[TypedLink]` с фиксированной schema

### Owner.md проблемы
- **Создавался из первого portrait, не из всего диалога** → 2-фазный (placeholder + post-dialog refine)
- **Никогда не обновлялся** → auto-refresh после `/save` если затронут job/memory/theme

### Auto-onboarding
- **Раньше:** онбординг только через `/start`. Если юзер написал «привет» — попадал в blank chat.
- **Сейчас:** auto-onboarding gate в `process_input` — любое первое сообщение запускает онбординг.

### `/start` safety
- **Раньше:** `/start` после удаления `_meta/portrait.md` запустил бы заново и переписал бы профиль.
- **Сейчас:** проверяется И owner.md И portrait.md. Если хотя бы один файл есть — отвечает «жив, удали оба для пере-онбординга».

---

## 7. Текущий статус

### Health
```
docker compose ps:
  bot           Up (healthy)
  lightrag-api  Up (healthy)   127.0.0.1:9621
  redis         Up (healthy)
```

### Tests
- **40/40 pytest** ✅
- **ruff clean** ✅
- **mypy 43 errors** — это legacy v1/v2 baseline, v3/v4 не вносят новых

### PyPI
- `mnemo-brain-mcp` v0.1.0 опубликован: https://pypi.org/project/mnemo-brain-mcp/
- Установлен через pipx локально: `~/.local/bin/mnemo-brain-mcp`
- Зарегистрирован в `~/.claude.json` через `claude mcp add mnemo-brain` — `✓ Connected`

### Memory (Claude Code automemory, persistent across sessions)
Хранится в `/Users/komrxn/.claude/projects/-Users-komrxn-Projects-Khusayinbek-brain/memory/`:
- `user_language.md` — общается на русском
- `feedback_models.md` — никаких gpt-4o/4-turbo, только gpt-5.4
- `feedback_obsidian_graph_ux.md` — после изменений всегда проверять как граф выглядит в Obsidian глазами юзера

### Repository state
- main branch
- Последние коммиты:
  ```
  873e1d9 rename: mnemo-mcp → mnemo-brain-mcp across docs
  80cfc8e fix(extractor): route ALL non-owner typed_links through add_typed_link
  ab6dc4e v3: brain-style graph + self-sync + multi-turn onboarding
  0fbf7d4 v2: graph quality phases A-K + Phase 0 lint baseline
  ```
- v4 рефакторинг (этап 1-5 из AUDIT_AND_REFACTOR_PLAN.md) сделан, но **не закоммичен** ещё. См. `git status`.

---

## 8. Как работать с проектом

### Запуск
```bash
# 1. Edit .env (TELEGRAM_BOT_TOKEN, OPENAI_API_KEY, ALLOWED_USER_IDS, TZ)
./setup.sh                        # idempotent: secrets/, data/, api key
docker compose up -d --build      # 3 сервиса
docker compose logs -f bot
```

### Тесты / lint
```bash
uv run ruff format src/ tests/
uv run ruff check src/ tests/
uv run mypy src/ --strict          # 43 baseline errors, не блокирует
uv run pytest -q tests/            # 40 tests
```

### Reset state (если онбординг или vault сломаны)
```bash
docker compose exec -T redis redis-cli FLUSHALL
find data/lightrag -type f \( -name "*.json" -o -name "*.graphml" \) -delete
find data/vault -name "*.md" ! -name ".gitkeep" -delete
rm -rf data/vault/.git
cd data/vault && git init -q -b main && \
  git config user.email "mnemo-bot@localhost" && \
  git config user.name "mnemo-bot" && \
  git add -A && git commit -q -m "init"
docker compose up -d --build bot
docker compose restart lightrag-api
```

### Catch-up index (если в графе пусто но в vault есть заметки)
```bash
docker compose exec -T bot /app/.venv/bin/python -c "
import asyncio
from pathlib import Path
async def main():
    from src.lightrag_svc.indexer import index_files
    vault = Path('/data/vault')
    paths = [str(p.relative_to(vault)) for p in vault.rglob('*.md')
             if not str(p.relative_to(vault)).startswith(('_meta/MOC_', '_meta/ontology', '_meta/portrait'))]
    await index_files(paths)
    print(f'indexed {len(paths)} notes')
asyncio.run(main())
"
docker compose restart lightrag-api  # чтобы перечитать storage
```

### Проверка что граф работает
```bash
API_KEY=$(cat secrets/lightrag_api_key.txt)
curl -s -H "X-API-Key: $API_KEY" http://localhost:9621/graph/label/list
```

### MCP подключение к Claude Code
```bash
pipx install mnemo-brain-mcp
API_KEY=$(cat secrets/lightrag_api_key.txt)
claude mcp add mnemo-brain --scope user \
  -e LIGHTRAG_BASE_URL=http://localhost:9621 \
  -e LIGHTRAG_API_KEY="$API_KEY" \
  -- "$(which mnemo-brain-mcp)"
claude mcp list
```

---

## 9. Что важно знать перед изменениями

### CLAUDE.md правила (обязательно соблюдать)
- Python 3.12, uv для зависимостей, ruff + mypy --strict
- Pydantic v2 для всего что пересекает границу
- structlog, никаких print
- Все vault-записи **только** через `vault/writer.py`
- Все git-операции **только** через `vault/git_ops.py`
- Никаких `--force` / `--no-verify` / `--mirror` — assertion блокирует
- Никаких `subprocess.run(["git", ...])` вне git_ops.py
- Один тип сущностей — 9, не вводить новые
- structured outputs обязательны для extractor + topic-shift

### Memory (см. §7 выше)
- Ответы по-русски (юзер русскоязычный)
- Никаких gpt-4o / gpt-4-turbo
- После изменений — проверять как граф выглядит в Obsidian, не только тесты

### Конкретные «не трогай»
- Не переделывать LightRAG embedded → HTTP в боте (Phase F запрет)
- Не вводить новые типы заметок без согласования
- Не делать MOC из всех типов — только person/project/job/theme
- Не делать `add_link` для типизированных связей — только через `add_typed_link`
- Owner.md намеренно не имеет inverse-полей — иначе будет flood

---

## 10. Известные мелкие issues / TODO

- **Mypy 43 baseline errors** в v1/v2 коде (aiogram type-hints, redis async, scheduler types). Не блокирует, но неприятно. Чинить отдельным PR.
- **MOC регенерируется на каждой сессии** — можно дебаунсить (одна перезапись в день вместо N).
- **`tests/test_typed_links.py`** не была обновлена под новый `paths.resolve_inside_vault` (нужен дополнительный patch в фикстуре). Не критично — основные тесты ОК.
- **vault_pull_sync** дёргает scheduler каждые 2 мин даже когда нет remote — пустой diff, лишь тратит CPU. Можно skip если remote=пустой.
- **Lock в `src/session/locks.py`** создан но **не использован** ещё в process_input. Готов к интеграции в Этап 2 follow-up.
- **TODO/FIXME в коде нет** — это политика CLAUDE.md.

---

## 11. Quick reference: где что искать

| Задача | Файл |
|---|---|
| Добавить новый тул для агента | `src/tools/<name>.py` + `_register()` |
| Изменить промпт онбординга | `prompts/onboarding.md` |
| Изменить промпт extract | `prompts/session_extract.md` |
| Добавить новый тип заметки | НЕЛЬЗЯ без согласования (см. CLAUDE.md) |
| Изменить семантику связей | `src/vault/frontmatter.py::RELATION_INVERSE` + `entity.py::SINGLE_VALUE_RELATIONS` |
| Поменять dedup threshold | `src/vault/write_pipeline.py::_DEDUP_HARD/_DEDUP_SOFT` |
| Изменить debounce timing | `src/lightrag_svc/reindex_queue.py::_DEBOUNCE_SECONDS` или `text.py::_COALESCE_DEBOUNCE_SEC` |
| Добавить scheduler-задачу | `src/scheduler/defaults.py::_DEFAULTS` + handler в `triggers.py::_handle_system_task` |
| Добавить новый тест | `tests/test_*.py` (pytest-asyncio mode=auto) |

---

## 12. Если у тебя «всё сломано» — алгоритм восстановления

1. `git status` — что изменено?
2. `uv run pytest -q tests/` — что падает?
3. `docker compose ps` — что не healthy?
4. `docker compose logs bot --tail 100` — что в логах?
5. Если граф пуст после restart: `find data/lightrag -type f` — есть ли graphml?
6. Если онбординг сломан: `docker compose exec redis redis-cli KEYS 'user:onboarding:*'`
7. Если ничего не помогает — Reset state (см. §8) и `/start` заново.

---

**Этот файл — handoff для следующей модели. Обновляй его если делаешь архитектурные изменения. Не для каждого мелкого фикса.**
