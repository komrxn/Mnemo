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

### v5 — i18n (ru / en / uz)
✅ См. IMPLEMENTATION_PLAN_V5.md

- `src/i18n.py` + `src/locales/{ru,en,uz}.yaml` — `t(key, lang, **vars)` loader с fallback ru→key.
- `prompts/{ru,en,uz}/*.md` — переезд `prompts/*.md` под language dirs. Loader `agent/prompts.py:render` теперь lang-aware.
- Профиль обогащается `ui_language` + `notes_language` (независимые); миграция `_apply_language_migration` бэкфилит для существующих юзеров.
- `/lang` inline-keyboard через `handle_lang_set` callback; `set_my_commands` для трёх языков.
- `vault/section_headers.py` — `## Факты` / `## Facts` / `## Faktlar`, читатели распознают все три.

### v6 — memory layers + read-before-ask (этот рефакторинг)
✅ См. docs/adr/0001-memory-layers.md

Идея: «забывание» возможно потому что read-path памяти проходит через лоссовый LLM-слой. Чиним структурно — два слоя памяти и hard-gate на recall.

**M0 — ADR.** [docs/adr/0001-memory-layers.md](docs/adr/0001-memory-layers.md) фиксирует двухслойную память и read-path **slot → transcript → graph**.

**M1 — Transcript layer.**
- `src/vault/transcripts.py` — `seal_session(session_id, msgs, lang)` пишет `90_Transcripts/YYYY/MM/YYYY-MM-DD_<session_id>.md` с дословным диалогом и frontmatter `type: transcript`.
- `escape_wikilinks` — `[[X]]` → `\[\[X\]\]` в теле transcript-нот, чтобы они не создавали edges в Obsidian / LightRAG.
- `search_literal(query)` — substring grep по transcript-файлам (parses out frontmatter, чтобы `session_id:` не давал false positives).
- `extractor.run_pipeline` сначала seal-ит transcript, потом extract — литерал сохранён даже при падении экстракции.
- Онбординг тоже sealit-ся: `_seal_onboarding_transcript` в handlers/text.py на `[ONBOARDING_DONE]` И на force-end.
- Конвертер ([lightrag_svc/converter.py](src/lightrag_svc/converter.py)) и `full_reindex` фильтруют `90_Transcripts/` по path и по `type: transcript`. **0 edges в KG от transcripts.**
- `90_Transcripts/` добавлен в `_VAULT_FOLDERS` bootstrap.

**M2 — Slot-binding.**
- `src/session/slots.py` — `PendingSlot` / `FilledSlot` pydantic-модели, Redis-keys `slot:pending:{user_id}` (TTL 10min) + `slot:filled:{session_id}` (TTL 7d).
- `consume_pending(redis, user_id, session_id, user_msg)` — эвристика «direct answer» (короткое, не кончается на `?`), записывает литерал в filled-list и чистит pending.
- `src/tools/slots.py` — tool `set_pending_slot(field, question, entity_hint)`. Гейт: отказывает если `was_recall_done == False` (см. M3).
- `handlers/text.py:_maybe_consume_slot` — обёртка, инжектит filled-slot в next LLM turn как system-сообщение `format_filled_for_prompt`.
- `extractor.extract(msgs, session_id)` — собирает filled-slots в system prompt через `format_filled_list_for_extraction`. После парсинга — `_warn_missing_slot_literals` логирует если literal_value слота не попал ни в одну entity.
- Onboarding и normal chat одинаково wire'нуты — slot Redis namespace разный (`slot:filled:onboarding` vs `slot:filled:ses_*`).
- В extractor после успешного `apply_to_vault` filled-slots чистятся.

**M3 — Recall tool.**
- `src/tools/recall.py` — `recall(query, top_k)` агрегирует 4 источника **параллельно** (`asyncio.gather`): current session msgs, transcripts literal, vault ripgrep, LightRAG semantic.
- Помечает `session:recall_done:{session_id}` в Redis на 120s.
- `was_recall_done(session_id)` — экспортируется как helper для гейтов.
- В onboarding dispatch передаётся `session_id="onboarding"`.
- Все 4 источника swallow-ят свои исключения (`try/except` на каждый helper), один сломанный источник не валит остальные.

**M4 — Proper-noun preservation в Entity.**
- `vault/entity.py:extract_proper_noun_candidates(text)` — regex `[A-ZА-Я]{2,}` (ALL-CAPS) + `\w[a-z]+[A-Z]\w*` (CamelCase/iPhone). Plain Title-case (Forbes, Москва) намеренно НЕ ловятся — слишком много false positives.
- `Entity._no_lost_proper_nouns` model_validator активируется через `context={"source_tokens": {...}}`; без context — legacy behavior (no-op).
- `extractor._apply_entity` собирает source_tokens из `EntityInfo.name + new_facts + aliases + updates`, прокидывает в `Entity.model_validate(..., context=...)`.
- `tools/obsidian._create_note` через `_source_tokens_from_slots(session_id)` подтягивает proper-nouns из filled slot literals и валидирует Entity с ними. Это закрывает онбординг-путь записи (где extract не используется).

**M5 — Topic-shift suppression в Q&A.**
- `session/topic_shift.py:detect()` — guard: если последняя реплика бота заканчивается на `?` (с учётом trailing emojis/punctuation) ИЛИ есть `slot:pending:*` → return `(False, "")` без LLM-call.
- Защита: ответ юзера не порежется в новую сессию, если бот ему только что задал вопрос.

**M6 — Cleanup + invariants.**
- CLAUDE.md обновлён 8 новыми инвариантами memory layer.
- `mypy` ошибки — все pre-existing v1/v2 baseline (та же история что в Memory.md §7).
- Тесты выросли: 40 → 178.

### v6+ — UX hardening (после первого live-теста)

После того как юзер начал тестировать v5+v6, нашлись и пофиксились видимые UX-баги:

**Streaming UI.** `src/agent/loop.py` теперь поддерживает `on_text` + `on_tool_start` callbacks через `_request_stream` (parses `delta.content` и инкрементально собирает `delta.tool_calls` по индексам). `OpenAI client timeout=300s` (5 мин). В `handlers/text.py` есть `_StreamUI` класс: посылает placeholder `…`, edit_message_text каждые ~0.9s или 25+ char delta, swap на «💭 проверяю память…» / «✍️ запоминаю…» при tool calls. Streaming подключён и к normal chat, и к онбординг flow.

**Hard read-before-ask guard.** `src/agent/guard.py` — `is_ignorance_claim(text)` ловит 18 ru/en/uz паттернов («не помню», «I don't see», «menda yo'q», etc). В `run_chat` после финального текста LLM: если ignorance И `was_recall_done` = False → injection `GUARD_RETRY_SYSTEM_MESSAGE` (англоязычная директива «вызови recall, посмотри результат, отвечай»), повторный round. Max 1 guard retry. `set_pending_slot` тоже требует `was_recall_done`. **Soft prompt-инструкция превратилась в hard structural gate.**

**Onboarding улучшения.**
- `_build_onboarding_system_prompt(profile)` — чистая функция, вызывается на КАЖДОМ ходу. Закрывает баг «`/lang` switch не доходит до онбординга» (раньше system prompt кэшировался в `state.messages[0]`).
- `_is_onboarding_looping(saved_messages)` — rapidfuzz `max(token_set_ratio, partial_ratio) >= 70` сравнивает последний assistant-ответ с предыдущими тремя. Если loop → force-end через тот же done-path (refine owner + seal transcript + bootstrap defaults). Закрывает реальный prod-баг где бот зацикливался на одних и тех же вопросах.

**Personality auto-translate.** `commands._maybe_retranslate_personality` на `/lang` UI-switch вызывает gpt-5.4-mini, перегоняет stored personality в новый язык, чистит кавычки/markdown. Soft fallback на старое значение при ошибке. Закрывает баг «personality осталась узбекской после переключения на русский».

**Memory > speed.** Per user feedback (memory `feedback_memory_over_speed.md`): никаких `asyncio.wait_for(timeout=...)` на recall/transcripts/extraction. OpenAI client timeout — да (внешний сервис). Внутренние memory-операции — нет. Скорость прячется через streaming UI и параллелизацию.

**Parallelized recall.** В `process_input` recall запускается через `asyncio.create_task` сразу как пришло сообщение и крутится конкурентно с topic-shift + Redis-ops. Awaited только перед LLM call. Экономит max(recall, session_setup) вместо суммы.

**Telegram Markdown → HTML.** `src/telegram/formatting.py:to_telegram_html` — конвертер MD-подмножества (`**bold**`, `_italic_`, `~~strike~~`, бэктики, headers `#` → `<b>`, bullets `-` → `•`, ссылки `[X](url)`). Бот создан с `parse_mode=HTML`, LLM выдаёт MD — раньше `**Юлю**` показывалось буквально. Конвертер применяется в `_StreamUI._edit` и в обёртках `reply_fn` (`handle_text/voice/photo`). Code fences (```\n...\n```) escape-ятся и не конвертируются.

**Brain-feel copy.** Все progress labels переписаны: «ищу в памяти» → «проверяю память», «транскрибирую» → «обрабатываю», «работаю» → «думаю». На ru/en/uz, в `_TOOL_PROGRESS_LABELS` и в локалях `multimodal.*` / `save.*` / `pipeline.*`.

### v4 (предыдущий рефакторинг) — senior-grade architecture
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

### Группа I — Streaming UI flood-control storm (May 2026)
**Симптомы:** Бот словил `TelegramRetryAfter: Retry in 200+ seconds` на `EditMessageText`. Десятки failed-edit'ов подряд, потом крах update handler'а с трейсбэком на `SendMessage` тоже rate-limited. Юзер не получает ответ вообще.
**Корень:** В `_StreamUI.on_text` ([telegram/handlers/text.py:908](src/telegram/handlers/text.py#L908)) гейт был `elapsed < 0.9s AND grew_by < 25 chars: skip`. Это означало — edit летит, когда **любое** из условий пройдено, не оба. При быстром стриминге gpt-5.4 grew_by пробивал 25 символов за ~0.3-0.5 сек → 2-3 edit/сек → выше Telegram-лимита 1/сек. И на 429 `_last_edit_at` не обновлялся → каждые новые 25 символов снова били API → шторм усиливал сам себя.
**Решение:**
1. Гейт переписан: ОБА условия должны быть выполнены (`elapsed ≥ 0.9s AND grew_by ≥ 25`).
2. На `TelegramRetryAfter` парсим `retry_after`, ставим `_blocked_until` — последующие edit'ы молча возвращают False без обращения к API.
3. `_last_edit_at` обновляется на **любую** попытку, не только успешную — failed-edit тоже потратил наш 1/сек бюджет.
4. Финальный fallback `message.answer(reply)` обёрнут в `try/except TelegramRetryAfter` — если кулдаун ещё активен, логируем `reply rate-limited` и не крешим update.

Тесты: `tests/test_stream_ui_ratelimit.py` — 6 кейсов на инварианты.

### Группа J — Person dedup false-merge (May 2026)
**Симптомы:** Мама-Лола и дочка-Хилола слились в одну заметку. В логах `wanted: 20_People/лола.md, existing: 20_People/хилола.md, score: 90, event: "write_pipeline dedup: routing to existing"`. После этого «Лола» становилась алиасом «Хилолы», все факты мамы прилетали в дочкину заметку, smart linker строил ложные связи.
**Корень:** `fuzz.WRatio` для всех типов в `find_similar` ([vault/dedup.py](src/vault/dedup.py)). WRatio комбинирует `partial_ratio`, который даёт 100% за substring containment ("лола" ⊂ "хилола"). Score=90 пробивал HARD-порог 85 → авто-мердж без шанса на отказ. Тот же класс бага: «Джамолиддин» vs «Джамол» = WRatio 90.
**Решение:**
1. Для типа `person` теперь `fuzz.ratio` (чистый Левенштейн, без partial substring biass) вместо WRatio.
2. HARD-порог для person поднят до 92 (с 85 для остальных типов).
3. Эмпирически: «Лола»↔«Хилола» теперь 80 → ниже 92 → дальше soft warning, авто-мерджа нет. Эквивалентные имена (`Hilola`=`Hilola`=100) по-прежнему мерджатся.
4. Для themes/jobs/thoughts оставлен WRatio+85 — консолидация близких тем там полезна.

Тесты: `tests/test_dedup.py` — 4 новых кейса (Лола≠Хилола substring, Джамолиддин≠Джамол prefix, Hilola=Hilola exact, themes still merge aggressively).

### Группа K — User-initiated reminders skipped by LLM (May 2026)
**Симптомы:** Юзер сказал «напомни через 30 минут продолжить echelon». Агент вызвал `schedule_task`, scheduler сработал в нужное время — но бот юзеру ничего не написал. В логах: `proactive_trigger ... 2026-05-18 19:03:57 → event: "proactive decided to skip"`. Дважды подряд один и тот же ответ.
**Корень:** `proactive_trigger` ([scheduler/triggers.py](src/scheduler/triggers.py)) — единая точка для всех расписаний: и бот-инициированных кронов (утренний дайджест, weekly_reflection, stale_project), и one-shot напоминаний от юзера. LLM-decider судил всех по одним правилам и для user-initiated тоже мог вернуть SKIP. Решение «писать или нет» отнималось у юзера, который уже это решение принял.
**Решение:** Архитектурное разделение через флаг `user_initiated: bool`:
1. `_schedule_task` (тул, который зовёт агент по просьбе юзера) выставляет `user_initiated=True`.
2. `defaults.py` (бот-кроны: digest/reflection/check_in) оставляет default `False`.
3. В `proactive_trigger`: если `user_initiated=True` И LLM вернул SKIP/empty/exception → fallback на детерминированное напоминание `_fallback_user_reminder(payload, ui_lang)` локализованное «⏰ Напоминание: {description}». Belt-and-suspenders: и в промпт `proactive.md` добавлена жёсткая строка «user_initiated=true → SKIP запрещён».
4. Для бот-инициированных задач LLM-failure → молчим, не спамим юзера.

Тесты: `tests/test_proactive_user_initiated.py` — 8 кейсов (fallback в ru/en/empty, SKIP refused user-initiated, happy passthrough, LLM-failure user-initiated still delivers, LLM-failure bot-initiated silent).

### Группа L — Topic-bleed: контент в неподходящей заметке (May 2026)
**Симптомы:** Заметка `Бакалавриат в финансовом` через день получила факты про `форель` (рыбоводный бизнес юзера). После этого LightRAG/smart-linker увидел оба термина в одном теле и создал ложную графовую связь между бакалавриатом и форельным бизнесом.
**Корень (3-слойный, найден через Explore agent):**
1. `src/tools/obsidian.py` `_append_to_note` — **ноль topic-coherence проверок**. Агент решил «допишу про форель в бакалавриат», тул это разрешил молча.
2. `src/agent/extractor.py` session-end pipeline — `_apply_entity` для thoughts/memories тоже молча append'ит в существующую заметку если `note_exists`.
3. `src/vault/write_pipeline.py` после dedup-reroute — добавляет факты в смерджённую заметку + поднимает оригинальное имя в `aliases` (alias-poisoning).

**Решение:** Новый модуль `src/vault/coherence.py` с двухстадийным gate:
1. **Stage 1 — fuzzy (всегда, ~0мс):** `partial_ratio` блока против title/aliases (заголовок как substring в блоке) + `max(partial, token_set)` против первых ~100 слов body. Решительно на крайностях: ≥75 → ok, <40 → mismatch.
2. **Stage 2 — LLM fallback (только на borderline 40-75, ~400мс):** gpt-5.4-mini YES/NO с body excerpt. Ловит семантическое сходство которое fuzzy не видит («новый поставщик мяса» в `bek-restaurant.md` — body содержит «поставщики/кухня», title нет).
3. **Safe degradation:** на ошибке чтения / LLM-краше → `unsure` (caller трактует как permissive — отказ от легитимной записи из-за I/O-сбоя хуже bleed-риска).

**Wired в 3 точки:**
- `_append_to_note` тул: `mismatch` → возвращает агенту error string «⛔ блок не по теме заметки X. Создай новую через `create_note`» — агент решает retry.
- extractor thoughts/memories: `mismatch` → создаёт sibling-заметку через `make_unique_note_path` (бакалавриат.md существует → бакалавриат-2.md), не append'ит.
- write_pipeline после dedup-reroute: то же — sibling вместо poisoning.

**Prompt-level belt:** `prompts/{ru,en,uz}/system.md` добавлен блок «Дисциплина записи: перед каждым append проверь что блок по той же теме что и заметка». `prompts/ru/session_extract.md` rule 8: «new_facts должны быть про сущность из `name`».

Тесты: `tests/test_coherence_gate.py` (10), `tests/test_append_gate_integration.py` (3), `tests/test_extractor_bleed_regression.py` (2 + bleed-сценарий из прода). Также `make_unique_note_path` хелпер в `src/vault/frontmatter.py`.

### Группа M — Conversation balance: бот слишком философствовал (May 2026)
**Симптомы:** Бот копал одну тему уточняющими вопросами пока юзер сам не переключал. Уходил в философию даже когда юзер в режиме capture (просто записать факт). Юзер тратил много времени на одну заметку. Бот игнорировал короткие ответы как стоп-сигнал — продолжал расспрашивать.
**Корень (в `prompts/ru/system.md`):**
- Идентичность строка 1: «не помощник, а **напарник по мышлению**» → бот понимал работу как «мыслить вместе».
- Строка 12: «Размышления → **включаешься: задаёшь встречные вопросы, провоцируешь**» — без soft-cap, без exit-сигнала.
- Строка 14: «Имеешь право на свою позицию... аргументируешь» — без условия; бот спорил даже в pure capture-репликах.
- Не было правила «один вопрос за реплику», soft-cap «2-3 follow-up'а на одну нить», «короткий ответ = тема закрыта».

**Решение** (на основе индустри-исследования: Pi.ai, Mindsera, Mem.ai, Stanford MI/Bloom, Replika-anti-pattern). Rewrite `prompts/{ru,en,uz}/system.md`:
1. **Идентичность:** «**умный AI-дневник**, который умеет копать когда надо и помнить». Thinking-partner — вторично, только по сигналу.
2. **Два режима:** Capture (default) — минимум слов, прими и сохрани, 0-1 вопрос за реплику. Explore (по сигналам: ≥30 слов от юзера, эмоциональный регистр, открытый вопрос от юзера, прямое «помоги подумать»).
3. **Глубина:** один вопрос за реплику (hard), soft-cap 2-3 follow-up'а на нить → потом summarize+branch, mirror-before-probe в explore, намеренный skip-question каждые 2-3 хода (Pi.ai pattern).
4. **Stop-signals:** короткий ответ (≤5 слов / «ок»/«да»/«угу»/«не знаю»/«понял») = тема закрыта. Не задавай больше вопросов по этой нити, либо acknowledge+stop, либо pivot на другую открытую нить.
5. **Topic-shift wins:** юзер ввёл новую сущность → новая тема побеждает. Закрой старую одной строкой, иди за юзером, не тащи обратно.
6. **Personality = ТОН, не правила.** Стиль управляет КАК говорить (теплее/жёстче/иронично), не отменяет capture-default, one-question, soft-cap, exit-signals.

Также:
- `topic_shift.md` ослаблен — `shift=true` и при средней смене (новая сущность), не только при явной. Бот быстрее идёт за юзером.
- `onboarding.md` — добавлен batch-чек-лист (Mem.ai pattern): для 3+ структурных фактов про одну сущность одна реплика с чек-листом, а не серия вопросов. Плюс exit-сигнал «короткий ответ → следующий слот».

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
- **281/281 pytest** ✅ (v6 + UX hardening + groups I-M доехали)
- **ruff clean** ✅
- **mypy 6 errors** — legacy v1/v2 baseline (`session/manager.py:157,162` redis типы, `lightrag_svc/indexer.py` `# type: ignore` unused, extractor unused-ignore). Новые группы багов не внесли регрешнов.

### PyPI
- `mnemo-brain-mcp` v0.1.0 опубликован: https://pypi.org/project/mnemo-brain-mcp/
- Установлен через pipx локально: `~/.local/bin/mnemo-brain-mcp`
- Зарегистрирован в `~/.claude.json` через `claude mcp add mnemo-brain` — `✓ Connected`

### Memory (Claude Code automemory, persistent across sessions)
Хранится в `/Users/komrxn/.claude/projects/-Users-komrxn-Projects-Khusayinbek-brain/memory/`:
- `user_language.md` — общается на русском
- `feedback_models.md` — никаких gpt-4o/4-turbo, только gpt-5.4
- `feedback_obsidian_graph_ux.md` — после изменений всегда проверять как граф выглядит в Obsidian глазами юзера
- `feedback_skeptical_user_function_audit.md` — «аудит» = проверка каждой user-функции против кода, не README
- `feedback_memory_over_speed.md` — НЕ таймаутить recall / transcripts / extraction; скорость через streaming UI, не через skip полноты
- `feedback_product_grade.md` — каждая фича = продукт, не PoC; думать про edge-cases, UX, backward-compat
- `feedback_docker_full_stack.md` — после `compose down` использовать `docker compose up -d --build` БЕЗ имени сервиса; иначе lightrag-api / redis могут не подняться и юзер тихо лишится auto-recall + MCP

### Repository state
- main branch
- Последние коммиты:
  ```
  8084b05 feat(prompts+coherence): capture-first balance + topic-bleed gate
  da5e541 fix(scheduler): user-initiated reminders bypass the SKIP path
  f5bbfd5 fix(formatting): preserve existing HTML tags in to_telegram_html
  049b50e fix(stream+dedup): two memory-critical bugs
  09188ef chore(settings): bump personality length limits
  9cf1611 feat(settings): personality synth + add-rule with anti-otsebyatina preview
  6732695 copy(kb): user-friendly confirmation prompts, mention undo on save
  783fe0b feat(kb): Yes/No confirmation for destructive Save and Undo buttons
  aec856d feat(kb): persistent reply-keyboard with localized command buttons
  0dc1bb8 feat(settings): single-message /settings menu — name, style, languages
  ```
- Все изменения групп I-M **закоммичены и запушены** в `origin/main`.

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

- **Mypy 6 baseline errors** — все pre-existing v1/v2 типы (aiogram, redis async stub union). v5+v6 не добавили новых.
- **MOC регенерируется на каждой сессии** — можно дебаунсить (одна перезапись в день вместо N).
- **vault_pull_sync** дёргает scheduler каждые 2 мин даже когда нет remote — пустой diff, лишь тратит CPU. Можно skip если remote=пустой.
- **Lock в `src/session/locks.py`** создан но **не использован** ещё в process_input. Готов к интеграции.
- **TODO/FIXME в коде нет** — это политика CLAUDE.md.

### Из аудита v6 — выявленные продуктовые гэпы (НЕ закрытые в этой итерации)

- **Эвалов нет.** Заявка «лучше ChatGPT/Claude на памяти» — архитектурно обоснована, эмпирически непроверена. Нужен eval harness с 30+ фикстурных диалогов и метриками recall accuracy / completeness / integrity.
- **Re-extraction loop.** Старые transcripts (после v6) могут содержать пропущенные extractor'ом сущности. Раз в неделю проходить и обогащать граф — не реализовано.
- **Backfill старых сессий.** Сессии ДО v6 не имеют transcript-нот; восстановить из Redis нельзя (TTL истёк), частично можно из `10_Daily/*.md` если session_id остался.
- **Proper-noun guard leaky.** Ловит ALL-CAPS + CamelCase. Plain Title-case (Forbes, Москва, Анна) не ловится — accepted trade-off (false positives на обычных капитализированных словах).
- **MCP use-cases в Claude Code** — продуктово сильнейшая фишка, нет туториала «что спрашивать у Claude через mnemo-brain». Юзер сам должен догадываться.

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
