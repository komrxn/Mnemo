# Mnemo — Implementation Plan v3 (Brain-style graph + Self-sync)

> **Назначение продукта:** Mnemo — single-user ИИ-ассистент в Telegram, «внешний мозг» владельца. Текст/голос/фото → типизированные markdown-заметки в Obsidian Vault (через git) → граф знаний (LightRAG, custom KG injection) → проактивные крон-уведомления + MCP-доступ для Claude Code/Cursor/Cline.
>
> **Этот документ — единственный источник правды для текущей итерации (v3).** Если он противоречит [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md) (v2, закрыт) или [`README.md`](README.md) — побеждает v3, и нужно зафиксировать дельту в README.
>
> **Single user.** Whitelist Telegram ID. Никакой мульти-юзер-логики не вводить.
>
> **Если непонятно — спрашивай владельца через Telegram/чат, не угадывай.** Лучше задать вопрос на 30 секунд, чем неделю чинить кривой граф.

---

## 0. Что было до v3 (НЕ переделывать)

### v1 — базовая инфраструктура
✅ Telegram-бот aiogram 3, whitelist, typing-indicator, multimodal (Whisper-1, GPT-5.4 Vision)
✅ Redis-сессии: open/close, sliding TTL, topic-shift detector, /save, /undo
✅ Obsidian vault: единый writer, git-commit на каждое изменение, push по SSH
✅ Agent loop: OpenAI function calling, retries, structured outputs
✅ APScheduler с RedisJobStore, дефолтные задачи
✅ Inline-confirmations через Redis pub/sub

### v2 (фазы A-K) — типизированный граф + custom KG (КОД написан, RUNTIME не валидирован)
🟡 A — Owner anchor + dedup через rapidfuzz (код есть)
🟡 B — Smart linker post-pass через structured outputs (код есть)
🟡 C — Frontmatter-driven typed links + bidirectional rendering (код есть)
🟡 D — LightRAG ontology (entity_types через addon_params) (код есть)
🟡 E — Maps of Content + daily-note hygiene (код есть)
🟡 F — MCP-мост (lightrag-server в отдельном Docker сервисе) (код есть, но F7/F8 manual smoke не пройден)
🟡 G — Hot-fixes (код есть, но G9 sanity check не пройден)
🟡 H — Custom KG injection (`ainsert_custom_kg` вместо `ainsert`, **0 LLM-extraction**) (код есть, но H8/H9.5 manual smoke не пройден)

⚠️ **Что не валидировано в v2:** pytest на реальном окружении не прогонялся; runtime-DoD (F7/F8, G9, H8, H9.5) — это manual smoke checklists, требующие запущенный docker compose. Закрываем их в **Phase 0** ниже + в финальном `§11. Test plan` v3.

⚠️ **Закоммичено не всё:** удаление `src/lightrag_svc/ontology.py` сейчас в working tree как `D` (staged, not committed). Закрывается в `§10.2`.

### Что в v2 ОСТАЛОСЬ кривым (это и решаем в v3)

| Проблема | Где | Решение в v3 |
|---|---|---|
| Промпты — чеклисты («task без проекта невалиден»), а не семантика. ИИ механически штампует, не понимая зачем | `prompts/onboarding.md`, `prompts/session_extract.md`, `agent/linker.py` system-string | Phase L (переписать на семантику) |
| Onboarding создаёт `_meta/owner.md` ПОСЛЕ заметок → агент в момент создания их не якорит к owner | `src/telegram/handlers/text.py::_run_onboarding_execute` | Phase M1 (поменять порядок) |
| Onboarding one-shot — агент не может уточнить если в портрете двусмысленность. Угадывает или пропускает | то же | Phase M3 (мульти-турн с уточнениями) |
| Slugify транслитерирует кириллицу: `аня-петрова.md` → `anya-petrova.md`. Юзеру в Obsidian нечитаемо | `src/vault/frontmatter.py::make_note_path` | Phase L1 (`allow_unicode=True`) |
| Ручные правки в Obsidian (через Obsidian Git plugin) не синхронизируются с LightRAG до weekly reindex | нигде нет sync-механизма | Phase I (vault pull-and-sync) |
| 10 параллельных reindex'ов после batch `add_typed_link` → race на graphml | `src/vault/linking.py` | Phase J (debounce queue) |
| MCP-мост ставится из `git clone` (не PyPI) — пользователю неудобно, апдейты вручную | `README.md` + 4 перевода | Phase K (форк → mnemo-brain-mcp на PyPI) |
| Старые тестовые vault-файлы (anna, legai, mnemo, ai) от v1 болтаются в репо | `data/vault/{20_People,30_Jobs,40_Projects,80_Themes}/*.md` | удалить (см. §10) |

---

## 1. Цели v3

1. **ИИ как мозг.** Промпты дают семантику типов связей с примерами, не правила-чеклисты. Агент сам решает что связать, опираясь на смысл. Связь, которую ИИ не может объяснить, — не ставится.
2. **Onboarding строит каркас за разговор.** Агент задаёт уточняющие вопросы пока не разберётся как правильно якорить новые сущности. Никаких молчаливых угадываний.
3. **Древо вокруг owner.** В центре графа — `_meta/owner.md`. Все сущности кроме `person`/`inbox`/`daily`/`attachment` имеют прямой `owner: [[_meta/owner]]`. Любой узел графа достижим от owner за 1-3 хопа по типизированным рёбрам.
4. **Двусторонняя синхронизация.** Бот пишет в vault → LightRAG. Юзер правит руками в Obsidian → периодический git pull → LightRAG обновляется. Один источник правды — markdown-файлы.
5. **Идиоматичная установка.** Разработчик ставит MCP-мост через `pip install mnemo-brain-mcp` (наш форк на PyPI). Никаких git clone в инструкции.

---

## 2. Главные принципы v3

### 2.1. Семантика > правила
Промпты дают ИИ **смысл** каждого типа связи + один короткий пример. Не «task должна иметь for_project», а «for_project = "это часть проекта X"; если думаешь о деплое Mnemo — это `for_project: [[40_Projects/mnemo]]`».

ИИ читает семантику и решает по контексту. Если связь неоднозначна — не ставит и (в onboarding) спрашивает владельца.

### 2.2. ВСЁ кроме `person` имеет прямой owner
| Тип | Owner в frontmatter | Пояснение |
|---|---|---|
| `job` | ✅ всегда | Твоё место работы — часть твоей жизни |
| `project` | ✅ всегда | Твой проект, личный или рабочий (рабочий доп. `for_job`) |
| `task` | ✅ всегда | Твоя задача (доп. `for_project` или `for_job`) |
| `thought` | ✅ всегда | Твоя мысль (доп. `themes` / `for_project`) |
| `memory` | ✅ всегда | Твоё воспоминание (доп. `about_person` / `for_job`) |
| `theme` | ✅ всегда | Твоя тема жизни (даже «AI», «Claude» — потому что это **твой** интерес) |
| `person` | ❌ никогда | Транзитивно через `works_at` → job → owner, или через `about_person` от memory, или через `related_people` от project |
| `inbox` / `daily` / attachment | ❌ | Не сущности графа |

В Obsidian Graph будет звезда от owner с лучами во все стороны (jobs, projects, themes, memories, thoughts, tasks). Это **не баг**, это цель: «всё что я думаю/делаю/вспоминаю — это часть меня».

`person` не получает owner потому что иначе owner.md превратился бы в инверсный список ВСЕХ людей с кем когда-либо говорил пользователь. Люди связаны через работы/мемори/проекты — этого достаточно.

⚠️ **Owner намеренно не имеет инверсий.** Когда `40_Projects/mnemo.md` ставит `owner: [[_meta/owner]]` — на стороне owner.md обратное поле НЕ создаётся. Это плановое решение фазы C v2 (см. `RELATION_INVERSE` в `src/vault/frontmatter.py` — owner отсутствует в маппинге специально). Иначе owner.md = свалка из тысячи wikilinks. В графе LightRAG ребро есть, в Obsidian Graph оно тоже видно (через wikilink в `40_Projects/mnemo.md`).

### 2.3. Транзитивные пути
Граф устроен так что **любая нода достижима от owner за 1-3 хопа**:

```
owner ──┬── jobs/restaurant.md ──── memory/выручка-апрель.md   (2 хопа: owner → restaurant → выручка)
        │                       └── people/аня.md (через works_at)  (2 хопа)
        │
        ├── projects/mnemo.md ── tasks/mvp-launch.md           (2 хопа: owner → mnemo → mvp-launch)
        │
        ├── themes/разработка.md ── themes/claude.md           (2 хопа через parent_theme)
        │                       └── thoughts/инсайт-про-claude.md (2 хопа через themes)
        │
        ├── memories/училась-в-мгу.md (1 хоп — owner-якорь напрямую)
        │
        └── thoughts/мысль-про-политику.md (1 хоп через owner; доп. themes если применимо)
```

### 2.4. Иерархия тем через `parent_theme`
Темы образуют дерево. Если в сессии всплывает «обсуждали Claude» и уже существует тема «Разработка» — у новой темы `Claude` ставится `parent_theme: [[80_Themes/разработка]]`. Это позволяет в Obsidian Graph группировать связанные темы в кластеры.

ИИ в extractor'е **проверяет существующие темы** перед созданием новой и решает: это самостоятельная тема или подтема существующей? Если подтема — `parent_theme`.

### 2.5. Никаких дублей
Один человек = одна нода `20_People/X.md`, даже если он коллега + сосед + друг. Связи разносятся по разным полям (`works_at` для коллеги-роли, `about_person` от memory для друга-роли). Дедупликатор (`src/vault/dedup.py`) уже работает с порогами 70/85/90 — не трогать.

### 2.6. ИИ переспрашивает в любой сессии
Если агенту не хватает информации чтобы правильно якорить сущность — он отвечает текстом-вопросом юзеру вместо угадывания. После ответа продолжает. Это работает в onboarding (где много неоднозначностей) и в обычных сессиях (когда юзер бросает обрывок без контекста).

Уже частично описано в `prompts/system.md`: «Если не уверен куда классифицировать — спрашиваешь одной короткой репликой. Не угадываешь молча.» В v3 это **усиливается** в onboarding-промпте и в session_extract.

### 2.7. Имена на родном языке
Slugify работает с `allow_unicode=True`. `Аня Петрова` → `аня-петрова.md`. Obsidian читает оба варианта одинаково, но русские имена нагляднее в файловом браузере и логах.

### 2.8. Качество тем
Темы из одного общего слова («работа», «жизнь») создают супер-узлы и портят граф. Минимум 2 слова, кроме случая когда тема — конкретный термин/технология/имя собственное (Claude, React, GPT-5.4, AI). Это правило в обоих промптах (onboarding + session_extract).

---

## 3. Дефолтное древо после онбординга

После того как юзер рассказал портрет «Я работаю CTO в LegAI. Веду проект Mnemo. Анна — мой сооснователь. Увлекаюсь AI и фотографией. Жил в Питере, переехал в Лиссабон в 2024.», бот должен создать **примерно** такую структуру:

```
_meta/
  owner.md           type=person, is_owner=true
                     aliases=[Komron, Komrxn, ...]
                     (тело: портрет владельца, 3-7 ключевых фактов)

30_Jobs/
  legai.md           type=job, owner=[[_meta/owner]]
                     employs=[[20_People/анна]]

40_Projects/
  mnemo.md           type=project, owner=[[_meta/owner]],
                     for_job=[[30_Jobs/legai]]   (т.к. рабочий проект)
                     related_people=[[20_People/анна]]

20_People/
  анна.md            type=person   (БЕЗ owner!)
                     works_at=[[30_Jobs/legai]]

70_Memories/
  переезд-лиссабон-2024.md  type=memory, owner=[[_meta/owner]]
  работа-питер.md           type=memory, owner=[[_meta/owner]]

80_Themes/
  разработка-ai.md   type=theme, owner=[[_meta/owner]]
  фотография.md      type=theme, owner=[[_meta/owner]]
```

В Obsidian Graph:
- В центре `_meta/owner` (5 рёбер: → legai, → mnemo, → memory1, → memory2, → 2 темы)
- На второй орбите: `legai` имеет дополнительно ребро к `анна` (через employs), `mnemo` — к `анна` (через related_people)
- Анна на 2 хопа от owner (через legai)
- Каждая тема — на 1 хоп от owner

После пары сессий в граф вольются `60_Thoughts/*` (с `themes` или `for_project`), `50_Tasks/*` (с `for_project`), `70_Memories/*` (мелкие, через `for_job`/`for_project`).

---

## 4. Решения по открытым вопросам v2 (что подтвердил владелец)

| Открытый вопрос v2 | Решение v3 | Реализуется в |
|---|---|---|
| **H1**: description сущности — все факты или 400 char? | 400 char первых bullet'ов. «Логи никто не смотрит, делай как удобно системе» | Текущая реализация в [src/lightrag_svc/converter.py](src/lightrag_svc/converter.py) уже такая. Не трогаем. |
| **H4**: дебаунсить параллельные reindex после `add_typed_link`? | Да, очередь с TTL=2 сек | **Phase J** |
| **q3** (новая идея): ручные правки в Obsidian → LightRAG? | Да, периодический `git pull` + diff | **Phase I** |
| **G2**: форкнуть `daniel-lightrag-mcp` как `mnemo-brain-mcp` на PyPI? | Да, скопировать как есть, лёгкий ребрендинг (имя пакета, README), логику не трогать | **Phase K** |
| Slugify Unicode? | Да, `allow_unicode=True` | **Phase L1** |
| Owner для job/project/task/thought/memory/theme? | Да, для всех. Person — нет. | Реализуется в промптах **Phase L** |
| Семантика > правила | Промпты переписать на смысл с примерами | **Phase L** |
| Onboarding мульти-турн с уточнениями | Да | **Phase M** |
| Иерархия тем через parent_theme | Да, ИИ ставит при создании subtheme | Уже в схеме v2 (фаза C). В промптах v3 — усилить инструкцию. |

Открытые вопросы v2 которые **остаются открытыми** (не блокируют v3, решим позже):
- H2: full_reindex теряет llm_response_cache.json — приемлемо для personal use.
- G7: read-only enforcement через reverse-proxy — не делаем, оставляем честную документацию.
- §6 v2 Q5 (initialize_share_data блокирует event loop ~1 сек) — приемлемо.

---

## 4.5. ФАЗА 0 — Validate v2 baseline (перед стартом v3)

> **Цель:** убедиться что код v2 не сломан до того как класть на него v3. Если pytest красный или docker не поднимается — чиним до начала L/M/J/I/K.

### 0.1. pytest baseline

```bash
cd /Users/komrxn/Projects/Khusayinbek_brain
uv sync                          # убедиться что deps установлены
uv run ruff check src/ tests/    # должен быть чист
uv run mypy src/ --strict        # должен быть чист
uv run pytest -q tests/          # все 16 тестов зелёные
```

**Ожидаемый результат:** 0 red, 0 mypy errors, 16/16 pytest passing.

**Если что-то падает:**
- ruff/mypy ошибки — фиксим минимально, без рефакторинга. Цель — зелёный baseline.
- pytest падает — смотрим стек. Возможные причины (по моему аудиту v2):
  - `tests/test_typed_links.py` использует `patch("src.vault.linking.git_ops.stage_and_commit", ...)` — должно работать. Если падает на импорте `reindex_queue` (которого ещё нет) — ОК, до фазы J этот тест не трогается.
  - `tests/test_linker.py` фикстура с 3 файлами — должно работать.
  - `tests/test_dedup.py` без cyrillic transliteration test — должно работать.
- Любой другой fail — фиксим, документируем в этом разделе как «найдено в Phase 0».

### 0.2. Закоммитить незавершённое из v2

```bash
git add -A src/lightrag_svc/ontology.py    # это staged deletion
git commit -m "remove src/lightrag_svc/ontology.py (Phase H6 Variant A)"
```

(Это закроет §10.2 v3.)

### 0.3. Docker baseline (опционально, требует Docker Desktop)

Если есть возможность поднять docker локально:

```bash
./setup.sh                          # создаст secrets, .env (если ещё нет)
# Заполнить .env (TELEGRAM_BOT_TOKEN, OPENAI_API_KEY, ALLOWED_USER_IDS)
docker compose up -d --build
sleep 60                            # ждём healthchecks
docker compose ps                   # все 3 должны быть (healthy)
```

**Если контейнер не поднимается:**
- `docker compose logs bot` — смотрим что не так
- `docker compose logs lightrag-api` — особое внимание на ошибки `LLM_BINDING is required` (значит .env не пробросился) или embedding dim mismatch
- Чиним и повторяем

**Если docker нет (только локальная разработка через `uv run`):**
- Этот шаг пропускаем. Smoke-тесты в §11 v3 будут single source of truth для runtime-валидации.

### 0.4. Фиксации Phase 0 в плане

После прогона:
- Отметить ✅ в DoD §16 пункт «Phase 0: pytest зелёный, ontology.py deletion закоммичена».
- Если в 0.1 нашлись красные тесты которые пришлось чинить — записать что именно в этот раздел плана как историю.

### Phase 0 — DoD

- [ ] `uv run ruff check src/ tests/` → no errors
- [ ] `uv run mypy src/ --strict` → no errors
- [ ] `uv run pytest -q tests/` → all green
- [ ] `git status` не показывает `D src/lightrag_svc/ontology.py` (закоммичено)
- [ ] (опц) docker compose up — 3 контейнера healthy

---

## 5. ФАЗА L — Семантические промпты (главное в v3)

> **Цель:** заменить чеклисты-правила на семантику с примерами. ИИ должен понимать **зачем** ставит связь, а не следовать алгоритму.
>
> **Файлы:** `prompts/onboarding.md`, `prompts/session_extract.md`, `prompts/system.md`, `src/agent/linker.py` (system-string в `propose_links`).

### L1. Slugify Unicode

**Файл:** `src/vault/frontmatter.py`

**Что менять:** функцию `make_note_path`.

**Старое:**
```python
slug = slugify(title, allow_unicode=False, separator="-")
```

**Новое:**
```python
slug = slugify(title, allow_unicode=True, separator="-", lowercase=True)
```

**DoD L1:**
- `make_note_path("person", "Аня Петрова")` → `20_People/аня-петрова.md`
- `make_note_path("project", "Mnemo MVP")` → `40_Projects/mnemo-mvp.md`
- `make_note_path("theme", "Разработка с AI")` → `80_Themes/разработка-с-ai.md`
- Существующие тесты не падают (slug изменился, тестов специфичных к slug нет).

### L2. Полностью переписать `prompts/onboarding.md`

См. §13.1 за полным текстом нового файла.

**Ключевые изменения относительно старого:**
- Добавлена таблица 9 типов сущностей с пояснением где owner=Да/Нет.
- Добавлена таблица 8 типов связей с примерами.
- Принцип «корень — owner; всё кроме person имеет owner-якорь».
- Принцип «не угадывай, спрашивай в Telegram прямо текстом».
- Порядок создания: jobs → themes (корни) → projects → people → memories → thoughts → tasks. Гарантирует что target wikilink'а уже существует.
- Запрет на `add_link` (нетипизированный) во время онбординга. Только через `frontmatter` в `create_note`.
- Запрет тем из одного общего слова.

### L3. Полностью переписать `prompts/session_extract.md`

См. §13.2 за полным текстом.

**Ключевые изменения:**
- Удалить блок «Обязательные правила линковки» (со словами «должна», «невалиден»).
- Добавить блок «Семантика типизированных связей» с примером каждого типа.
- Добавить новое поле `typed_links` в схеме `EntityInfo` (см. §5.4 ниже).
- Добавить инструкцию: «все entities кроме person/inbox/daily получают `owner: [[_meta/owner]]`».
- Добавить инструкцию про `parent_theme` для подтем.
- Добавить инструкцию про дубли (одна сущность = одна нода).

### L4. Расширить `EntityInfo` schema в `src/agent/extractor.py`

**Старое:**
```python
class EntityInfo(BaseModel):
    type: Literal[...]
    name: str
    aliases: list[str] = []
    new_facts: list[str] = []
    updates: list[str] = []
    due: str = ""
    status: str = "open"
```

**Новое:**
```python
class EntityInfo(BaseModel):
    type: Literal["person", "project", "task", "job", "theme", "memory", "thought"]
    name: str
    aliases: list[str] = []
    new_facts: list[str] = []
    updates: list[str] = []
    due: str = ""                    # task only
    status: str = "open"             # task only
    typed_links: dict[str, list[str]] = {}  # ⭐ NEW
    # typed_links example:
    #   {"owner": ["_meta/owner.md"],
    #    "works_at": ["30_Jobs/legai.md"],
    #    "themes": ["80_Themes/ai.md", "80_Themes/startups.md"]}
    # Single-value relations also use list (always exactly 1 item).
```

**Реализация в `_apply_entity()`:** после `write_note`/`update_frontmatter` пройти по `entity.typed_links` и для каждой пары `(field, [target_paths])` либо добавить в frontmatter напрямую (если single-value), либо batch через `add_typed_link()` (если list-value, с генерацией обратных связей).

**Псевдокод нового `_apply_entity`:**
```python
async def _apply_entity(entity: EntityInfo, session_id: str) -> str:
    note_type = _ENTITY_TO_NOTE_TYPE.get(entity.type, "inbox")
    rel_path = make_note_path(note_type, entity.name)

    body = ...  # как сейчас (## Факты + ## Обновления)
    fm = {"type": note_type, "aliases": entity.aliases}
    if note_type == "task":
        fm["status"] = entity.status
        fm["due"] = entity.due

    # ⭐ Single-value typed links go directly into fm before write
    SINGLE = {"owner", "works_at", "for_job", "for_project", "about_person", "parent_theme"}
    LIST = {"themes", "related_people"}

    list_links_to_add: list[tuple[str, str]] = []   # (field, target) for add_typed_link

    for field, targets in entity.typed_links.items():
        if field in SINGLE and targets:
            fm[field] = f"[[{targets[0].removesuffix('.md')}]]"
        elif field in LIST:
            for tgt in targets:
                list_links_to_add.append((field, tgt))

    # Write or update note
    if reader.note_exists(rel_path):
        if body:
            await writer.append_to_note(rel_path, body, session_id)
        await writer.update_frontmatter(rel_path, fm, session_id)
    else:
        await writer.write_note(rel_path, body or entity.name, fm, session_id)

    # ⭐ List-value links go via add_typed_link (creates inverse on target)
    for field, target_path in list_links_to_add:
        await add_typed_link(rel_path, target_path, field, session_id)

    return rel_path
```

### L5. Переписать system-string в `src/agent/linker.py::propose_links`

См. §13.3 за полным текстом.

**Ключевые изменения:**
- Подчеркнуть что extractor УЖЕ проставил основные связи. Linker дополняет упущенное.
- Удалить «должна» / «обязательно» / «невалиден».
- Добавить семантику каждого типа связи с одним примером.
- Добавить правило «не предлагай связи которые уже в frontmatter» (it's уже есть, оставить).

### L6. Минорное обновление `prompts/system.md`

В разделе «Что ты делаешь» **усилить** строчку про переспрашивание:

**Старое:**
> Если не уверен куда классифицировать — **спрашиваешь** одной короткой репликой. Не угадываешь молча.

**Новое:**
> Если не уверен куда классифицировать или какие типизированные связи проставить — **обязательно спрашиваешь** владельца одной короткой репликой в Telegram. Угадывать молча запрещено: лучше задать вопрос на 30 секунд, чем неделю чинить кривой граф.

### Phase L — общий DoD

- [ ] L1: `make_note_path` с `allow_unicode=True`, проверено на 3+ кейсах кириллицы
- [ ] L2: `prompts/onboarding.md` соответствует §13.1 этого плана
- [ ] L3: `prompts/session_extract.md` соответствует §13.2 этого плана
- [ ] L4: `EntityInfo.typed_links` есть, `_apply_entity` пишет single-value в fm и list-value через add_typed_link
- [ ] L5: system-string в `linker.py::propose_links` соответствует §13.3
- [ ] L6: `prompts/system.md` обновлён про переспрашивание
- [ ] Все существующие тесты зелёные (`uv run pytest`)
- [ ] Smoke: послать боту портрет «работаю в LegAI как CTO, веду Mnemo, увлекаюсь AI» → проверить что в `40_Projects/mnemo.md` есть `owner` и `for_job`, в `20_People/...` нет owner, в `80_Themes/ai.md` есть owner

---

## 6. ФАЗА M — Multi-turn onboarding

> **Цель:** превратить one-shot onboarding в диалог, где агент задаёт уточняющие вопросы пока не разберётся как правильно якорить сущности.
>
> **Файлы:** `src/telegram/handlers/text.py`, `src/session/manager.py` (если нужны новые ключи), `prompts/onboarding.md` (см. Phase L2).

### M1. Новый порядок в `_run_onboarding_execute`

**Файл:** `src/telegram/handlers/text.py`

**Старый порядок (буквально):**
```
1. Дёрнуть агента → создать заметки + portrait через create_note(type=inbox)
2. Записать _meta/portrait.md (отдельно через vault_writer.write_note)
3. Записать _meta/owner.md через _create_owner_note (LLM-вызов на extract фактов)
4. bootstrap_defaults
```

**Новый порядок:**
```
1. Создать _meta/owner.md (короткий каркас + 3-7 фактов из портрета через _create_owner_note)
2. Сохранить owner_path и owner_name в Redis profile
3. Записать _meta/portrait.md (raw текст портрета)
4. Запустить multi-turn agent loop (см. M3) — агент строит граф, может задавать вопросы юзеру
5. bootstrap_defaults
```

Owner.md создаётся **первым** так что когда агент строит граф он уже знает что owner есть. Передаём `owner_path` в onboarding-промпт.

### M2. `_create_owner_note` остаётся как есть

Владелец подтвердил: owner.md = ядро с **портретом** владельца (3-7 ключевых фактов из портрета через LLM). НЕ упрощать до пустого каркаса. Сохраняется текущая реализация в `text.py:56-114`.

⚠️ Внимание: текущий код вызывает `_create_owner_note(portrait, profile)` с дефолтным `user_id` из `profile.get("user_id", settings.allowed_user_ids[0])`. В M1 нужно убедиться что `user_id` сохранён в profile **до** вызова `_create_owner_note`. См. `text.py:202-203` — там в step_owner_name записывается `user_id`. ОК, не трогаем.

### M3. Multi-turn agent loop с вопросами

**Файл:** `src/telegram/handlers/text.py`

**Текущая реализация:**
```python
result = await agent_loop.run_chat(messages, registry.openai_specs(), dispatch, max_rounds=30)
await reply_fn(result)
```

`run_chat` крутится пока агент использует tools. Когда агент возвращает текст без tool-call — возвращает этот текст. Сейчас этот текст показывается юзеру как final.

**Новое:** если агент возвращает текст без tool-call **и онбординг ещё не закрыт** (флаг в Redis) — это вопрос юзеру. Обрабатываем:
1. Показываем текст юзеру (через `reply_fn`).
2. Сохраняем в Redis: `onboarding_state = "agent_question"`, плюс полную историю сообщений `messages` и `portrait`.
3. Когда юзер отвечает — `_handle_onboarding` подхватывает state, добавляет ответ как `{"role": "user", "content": <ответ>}` в messages, продолжает `run_chat` с этой историей.
4. Цикл продолжается пока агент не вернёт **специальный sentinel** в тексте — например `"[ONBOARDING_DONE]"`. Тогда: показываем юзеру текст без sentinel, удаляем onboarding-state из Redis.

**Реализация: новая функция `_run_onboarding_turn`**

```python
async def _run_onboarding_turn(
    user_id: int,
    messages: list[dict[str, Any]],
    reply_fn: Callable[[str], Awaitable[None]],
) -> tuple[str, bool]:
    """Run one turn of onboarding agent. Returns (text, is_done)."""
    registry = get_registry()

    async def dispatch(name: str, args: dict) -> str:
        return await registry.call(name, args)

    result = await agent_loop.run_chat(
        messages, registry.openai_specs(), dispatch, max_rounds=30
    )

    is_done = "[ONBOARDING_DONE]" in result
    text = result.replace("[ONBOARDING_DONE]", "").strip()
    return text, is_done
```

**Изменить `_run_onboarding_execute`:**

```python
async def _run_onboarding_execute(
    portrait: str,
    profile: dict[str, object],
    reply_fn: Callable[[str], Awaitable[None]],
) -> None:
    user_id = int(str(profile.get("user_id", settings.allowed_user_ids[0])))

    # Step 1: write owner.md (with extracted facts) and portrait.md FIRST
    try:
        await _create_owner_note(portrait, profile)
    except Exception as exc:
        logger.warning("owner note creation failed", error=str(exc))

    from src.vault import writer as vault_writer
    try:
        await vault_writer.write_note("_meta/portrait.md", portrait, {"type": "inbox"})
    except Exception as exc:
        logger.warning("portrait save failed", error=str(exc))

    # Step 2: build initial agent messages
    bot_name = str(profile.get("bot_name", "Ассистент"))
    personality = str(profile.get("personality", ""))
    owner_name = str(profile.get("owner_name", "Владелец"))
    system_prompt = prompts.render(
        "onboarding",
        bot_name=bot_name,
        personality=personality,
        owner_name=owner_name,
        owner_path="_meta/owner.md",
    )
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": (
            f"Текст портрета:\n\n{portrait}\n\n"
            "Построй начальное древо знаний согласно инструкциям в системном промпте. "
            "Если что-то неясно или двусмысленно — спроси меня прямо текстом. "
            "Когда закончишь — финальное сообщение должно содержать [ONBOARDING_DONE]."
        )},
    ]

    text, is_done = await _run_onboarding_turn(user_id, messages, reply_fn)
    await reply_fn(text)

    if is_done:
        try:
            from src.scheduler.defaults import bootstrap_defaults
            bootstrap_defaults()
        except Exception as exc:
            logger.warning("default tasks bootstrap failed", error=str(exc))
        return

    # Save state for follow-up turns
    redis = await session_mgr.get_redis()
    key = session_mgr.key_onboarding(user_id)
    # Append agent question to messages, save messages
    messages.append({"role": "assistant", "content": text})
    await redis.set(
        key,
        orjson.dumps({"state": "agent_question", "messages": messages, "portrait": portrait}),
        ex=86400,
    )
```

**Изменить `_handle_onboarding` — добавить новый state:**

```python
if step == "agent_question":
    # User is answering the agent's clarifying question
    saved = state.get("messages", [])
    portrait = state.get("portrait", "")
    profile = await session_mgr.get_profile(redis, user_id)

    saved.append({"role": "user", "content": content})

    text, is_done = await _run_onboarding_turn(user_id, saved, reply_fn)
    await reply_fn(text)

    if is_done:
        await redis.delete(key)
        try:
            from src.scheduler.defaults import bootstrap_defaults
            bootstrap_defaults()
        except Exception as exc:
            logger.warning("default tasks bootstrap failed", error=str(exc))
    else:
        saved.append({"role": "assistant", "content": text})
        await redis.set(
            key,
            orjson.dumps({"state": "agent_question", "messages": saved, "portrait": portrait}),
            ex=86400,
        )
    return True
```

### M4. Защита от бесконечного цикла

Если агент задаёт > 5 уточняющих вопросов подряд — что-то идёт не так. Добавить счётчик `turn_count` в state, после 5 → принудительно завершить онбординг с тем что есть и логировать `logger.warning("onboarding hit max turns, ending forcibly")`.

### M5. Обновить размер `messages` чтобы не разрастался

OpenAI рейт-лимит на токены. После 10 раундов диалога messages может быть большой. **Не делаем компакцию в M** — onboarding короткий, до 5 раундов. Если разрастается > 30 сообщений — компактить через `_compact_msgs` (уже есть в extractor.py).

### Phase M — общий DoD

- [ ] M1: новый порядок (owner first → agent с инструкцией строить вокруг owner)
- [ ] M2: `_create_owner_note` не сломан
- [ ] M3: agent может вернуть текст-вопрос → юзер отвечает → агент продолжает
- [ ] M4: после 5 уточнений принудительное завершение
- [ ] Smoke test: послать неоднозначный портрет («работал, потом ушёл, теперь свой проект»), проверить что бот переспрашивает
- [ ] Smoke test: послать чёткий портрет, проверить что онбординг заканчивается за 1 раунд (агент сразу вернёт `[ONBOARDING_DONE]`)

---

## 7. ФАЗА J — Reindex debounce queue

> **Цель:** убрать race condition при множественных параллельных `index_files`. Когда `apply_to_vault` создаёт 5 заметок и smart linker добавляет 10 typed links — сейчас может стартовать 15 параллельных `ainsert_custom_kg`. Файл `data/lightrag/graph_chunk_entity_relation.graphml` пишется не атомарно.

### J1. Новый модуль `src/lightrag_svc/reindex_queue.py`

```python
from __future__ import annotations
import asyncio
import structlog

logger = structlog.get_logger()

_DEBOUNCE_SECONDS = 2.0
_pending: set[str] = set()
_lock = asyncio.Lock()
_flush_task: asyncio.Task[None] | None = None
_last_enqueue: float = 0.0


async def enqueue(paths: list[str]) -> None:
    """Add paths to debounce queue. Triggers flush after _DEBOUNCE_SECONDS of quiet."""
    if not paths:
        return
    global _flush_task, _last_enqueue
    loop = asyncio.get_event_loop()
    async with _lock:
        _pending.update(paths)
        _last_enqueue = loop.time()
        if _flush_task is None or _flush_task.done():
            _flush_task = asyncio.create_task(_flush_loop())


async def _flush_loop() -> None:
    """Wait until DEBOUNCE_SECONDS pass without new enqueues, then flush."""
    while True:
        await asyncio.sleep(_DEBOUNCE_SECONDS)
        loop = asyncio.get_event_loop()
        async with _lock:
            quiet_for = loop.time() - _last_enqueue
            if quiet_for >= _DEBOUNCE_SECONDS and _pending:
                paths = sorted(_pending)
                _pending.clear()
                break
            if not _pending:
                return

    # Flush outside lock
    try:
        from src.lightrag_svc.indexer import index_files
        await index_files(paths)
        logger.info("debounced reindex flushed", count=len(paths))
    except Exception as exc:
        logger.error("debounced reindex failed", count=len(paths), error=str(exc))


async def flush_now() -> None:
    """Force-flush pending paths immediately (used in tests, shutdown)."""
    async with _lock:
        if not _pending:
            return
        paths = sorted(_pending)
        _pending.clear()
    from src.lightrag_svc.indexer import index_files
    await index_files(paths)
```

### J2. Перевести вызовы

**Файл:** `src/vault/linking.py`, функция `add_typed_link`.

**Старое:**
```python
try:
    import asyncio
    from src.lightrag_svc.indexer import index_files
    _t = asyncio.create_task(index_files(paths_to_commit))
    _ = _t
except Exception as exc:
    logger.warning("link reindex skipped", error=str(exc))
```

**Новое:**
```python
try:
    from src.lightrag_svc.reindex_queue import enqueue
    await enqueue(paths_to_commit)
except Exception as exc:
    logger.warning("link reindex enqueue skipped", error=str(exc))
```

**Файл:** `src/agent/extractor.py`, функция `apply_to_vault` → `_index_created`.

**Старое:**
```python
async def _index_created(paths: list[str]) -> None:
    try:
        from src.lightrag_svc.indexer import index_files
        await index_files(paths)
    except Exception as exc:
        logger.warning("lightrag index skipped", error=str(exc))
```

**Новое:**
```python
async def _index_created(paths: list[str]) -> None:
    try:
        from src.lightrag_svc.reindex_queue import enqueue
        await enqueue(paths)
    except Exception as exc:
        logger.warning("lightrag index enqueue skipped", error=str(exc))
```

### J3. Тест `tests/test_reindex_queue.py`

```python
from __future__ import annotations
import asyncio
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_debounce_coalesces_calls() -> None:
    """5 quick enqueues → exactly 1 index_files call after debounce window."""
    with patch("src.lightrag_svc.indexer.index_files", new_callable=AsyncMock) as mock_idx:
        from src.lightrag_svc.reindex_queue import enqueue

        await enqueue(["a.md"])
        await enqueue(["b.md"])
        await enqueue(["a.md"])  # dedup
        await enqueue(["c.md"])

        # Wait past debounce window
        await asyncio.sleep(2.5)

    assert mock_idx.call_count == 1
    called_paths = sorted(mock_idx.call_args[0][0])
    assert called_paths == ["a.md", "b.md", "c.md"]


@pytest.mark.asyncio
async def test_flush_now_forces_immediate() -> None:
    with patch("src.lightrag_svc.indexer.index_files", new_callable=AsyncMock) as mock_idx:
        from src.lightrag_svc.reindex_queue import enqueue, flush_now

        await enqueue(["x.md"])
        await flush_now()

    mock_idx.assert_called_once_with(["x.md"])
```

### J4. Заметка про shutdown

`flush_now` нужно вызывать при graceful shutdown бота (в `src/main.py` finally-блоке) чтобы не потерять pending переиндексацию. Не критично для personal use (потеря 5 секунд reindex'а — норм), но желательно.

**Добавить в `src/main.py::main()` finally:**
```python
finally:
    lifecycle_task.cancel()
    scheduler.shutdown(wait=False)
    try:
        from src.lightrag_svc.reindex_queue import flush_now
        await flush_now()
    except Exception as exc:
        logger.warning("reindex flush on shutdown failed", error=str(exc))
    await bot.session.close()
    logger.info("stopped")
```

### Phase J — общий DoD

- [ ] J1: модуль `src/lightrag_svc/reindex_queue.py` создан, с `enqueue` и `flush_now`
- [ ] J2: оба caller'а (`add_typed_link`, `_index_created`) используют `enqueue` вместо прямого `index_files`
- [ ] J3: тест `tests/test_reindex_queue.py` зелёный
- [ ] J4: `main.py` вызывает `flush_now` при shutdown
- [ ] Smoke: создать в боте 3 заметки за раз, проверить в логах что `incremental index done (custom kg)` всего ОДИН (а не 3+)

---

## 8. ФАЗА I — Vault pull-and-sync

> **Цель:** ручные правки в Obsidian (через Obsidian Git plugin auto-pull, или прямо в файлах на десктопе) → синхронизируются с LightRAG в течение 2 минут.

### I1. Новый конфиг

**Файл:** `src/config.py`

Добавить поле:
```python
vault_pull_interval_min: int = 2  # 0 = disable
```

**Файл:** `.env.example`

Добавить в секцию Vault:
```
# How often (in minutes) to git-pull vault and sync changes to LightRAG.
# Set to 0 to disable (only push-on-write will work).
VAULT_PULL_INTERVAL_MIN=2
```

### I2. `git_ops.pull_with_diff`

**Файл:** `src/vault/git_ops.py`

Добавить функцию:

```python
from typing import TypedDict


class VaultDiff(TypedDict):
    added: list[str]
    modified: list[str]
    deleted: list[str]
    renamed: list[tuple[str, str]]  # (old_path, new_path)


async def pull_with_diff(vault_path: Path) -> VaultDiff | None:
    """Pull from remote and return the diff (file changes since last HEAD).

    Returns None on conflict (caller should alert user, not auto-resolve).
    Returns empty diff if no remote changes.

    Uses HEAD@{1}..HEAD diff after successful pull.
    """
    from src.config import settings
    if not settings.vault_git_remote:
        return VaultDiff(added=[], modified=[], deleted=[], renamed=[])

    # Capture HEAD before pull
    try:
        before = await _run(vault_path, "rev-parse", "HEAD")
    except RuntimeError:
        # No commits yet — first sync
        before = ""

    # Pull (with rebase to avoid merge commits)
    try:
        await _run(vault_path, "pull", "--rebase", use_ssh=True)
    except RuntimeError as exc:
        msg = str(exc)
        if "conflict" in msg.lower() or "Could not apply" in msg:
            logger.warning("vault pull conflict", error=msg)
            # Abort the rebase to leave repo in clean state
            try:
                await _run(vault_path, "rebase", "--abort")
            except RuntimeError:
                pass
            return None
        # Other errors (network, auth) — propagate
        raise

    after = await _run(vault_path, "rev-parse", "HEAD")
    if before == after:
        return VaultDiff(added=[], modified=[], deleted=[], renamed=[])

    # Diff with rename detection (-M, similarity threshold 70%)
    if before:
        diff_raw = await _run(vault_path, "diff", "--name-status", "-M70", before, after)
    else:
        # First-ever pull — treat all current .md as added
        diff_raw = await _run(vault_path, "diff", "--name-status", "--cached", after)

    diff = VaultDiff(added=[], modified=[], deleted=[], renamed=[])
    for line in diff_raw.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        status = parts[0]
        if status.startswith("A") and len(parts) >= 2:
            if parts[1].endswith(".md"):
                diff["added"].append(parts[1])
        elif status.startswith("M") and len(parts) >= 2:
            if parts[1].endswith(".md"):
                diff["modified"].append(parts[1])
        elif status.startswith("D") and len(parts) >= 2:
            if parts[1].endswith(".md"):
                diff["deleted"].append(parts[1])
        elif status.startswith("R") and len(parts) >= 3:
            if parts[1].endswith(".md") and parts[2].endswith(".md"):
                diff["renamed"].append((parts[1], parts[2]))
    return diff
```

### I3. Scheduler-задача `vault_pull_sync`

**Файл:** `src/scheduler/defaults.py`

Добавить новую дефолтную задачу:

```python
(
    "vault_pull_sync",
    "*/2 * * * *",   # каждые 2 минуты
    "system",
    {"action": "vault_pull_sync", "description": "Sync manual Obsidian edits to LightRAG"},
),
```

Если в settings `vault_pull_interval_min == 0` — НЕ регистрировать. См. логику ниже.

**Изменить `bootstrap_defaults`:**

```python
def bootstrap_defaults() -> None:
    from src.config import settings
    scheduler = get_scheduler()
    for task_id, cron, kind, payload in _DEFAULTS:
        # Skip vault_pull_sync if disabled in config
        if task_id == "vault_pull_sync" and settings.vault_pull_interval_min == 0:
            continue
        # If interval differs from default, override cron
        if task_id == "vault_pull_sync" and settings.vault_pull_interval_min != 2:
            cron = f"*/{settings.vault_pull_interval_min} * * * *"
        if scheduler.get_job(task_id) is not None:
            continue
        ...
```

### I4. Handler в `src/scheduler/triggers.py`

В функции `_handle_system_task` добавить ветку:

```python
async def _handle_system_task(payload: dict) -> None:
    action = payload.get("action")
    if action in {"full_reindex", "regenerate_ontology_then_reindex"}:
        try:
            from src.lightrag_svc.indexer import full_reindex
            await full_reindex()
            logger.info("full reindex completed")
        except Exception as exc:
            logger.error("reindex failed", error=str(exc))
        return

    if action == "vault_pull_sync":
        await _do_vault_pull_sync()
        return


async def _do_vault_pull_sync() -> None:
    from pathlib import Path
    from src.config import settings
    from src.vault import git_ops

    vault = Path(settings.vault_path)
    try:
        diff = await git_ops.pull_with_diff(vault)
    except Exception as exc:
        logger.error("vault pull failed", error=str(exc))
        return

    if diff is None:
        # Conflict — alert user, no auto-resolve
        try:
            from src.telegram.bot import get_bot
            bot = get_bot()
            user_id = settings.allowed_user_ids[0]
            await bot.send_message(
                user_id,
                "⚠️ Конфликт при git pull в vault. Разруливай вручную в репо. "
                "До разрешения — синхронизация остановлена."
            )
        except Exception as exc2:
            logger.warning("conflict alert failed", error=str(exc2))
        return

    total = (
        len(diff["added"]) + len(diff["modified"])
        + len(diff["deleted"]) + len(diff["renamed"])
    )
    if total == 0:
        return

    logger.info(
        "vault diff detected",
        added=len(diff["added"]),
        modified=len(diff["modified"]),
        deleted=len(diff["deleted"]),
        renamed=len(diff["renamed"]),
    )

    # Reindex added + modified via debounce queue
    from src.lightrag_svc.reindex_queue import enqueue
    to_index = diff["added"] + diff["modified"]
    if to_index:
        await enqueue(to_index)

    # Handle renames + deletes via graph_sync
    from src.lightrag_svc.graph_sync import handle_rename, handle_delete
    for old, new in diff["renamed"]:
        try:
            await handle_rename(old, new)
        except Exception as exc:
            logger.warning("rename sync failed", old=old, new=new, error=str(exc))
    for path in diff["deleted"]:
        try:
            await handle_delete(path)
        except Exception as exc:
            logger.warning("delete sync failed", path=path, error=str(exc))
```

### I5. Тест `tests/test_vault_sync.py`

```python
from __future__ import annotations
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_vault_pull_sync_dispatches_diff() -> None:
    from src.vault.git_ops import VaultDiff
    fake_diff: VaultDiff = {
        "added": ["20_People/анна.md"],
        "modified": ["40_Projects/mnemo.md"],
        "deleted": ["50_Tasks/old.md"],
        "renamed": [("30_Jobs/legai.md", "30_Jobs/legai-corp.md")],
    }

    with (
        patch("src.vault.git_ops.pull_with_diff", new=AsyncMock(return_value=fake_diff)),
        patch("src.lightrag_svc.reindex_queue.enqueue", new_callable=AsyncMock) as mock_enq,
        patch("src.lightrag_svc.graph_sync.handle_rename", new_callable=AsyncMock) as mock_ren,
        patch("src.lightrag_svc.graph_sync.handle_delete", new_callable=AsyncMock) as mock_del,
    ):
        from src.scheduler.triggers import _do_vault_pull_sync
        await _do_vault_pull_sync()

    mock_enq.assert_called_once()
    enq_args = sorted(mock_enq.call_args[0][0])
    assert enq_args == ["20_People/анна.md", "40_Projects/mnemo.md"]
    mock_ren.assert_called_once_with("30_Jobs/legai.md", "30_Jobs/legai-corp.md")
    mock_del.assert_called_once_with("50_Tasks/old.md")


@pytest.mark.asyncio
async def test_vault_pull_sync_handles_conflict() -> None:
    with (
        patch("src.vault.git_ops.pull_with_diff", new=AsyncMock(return_value=None)),
        patch("src.telegram.bot.get_bot") as mock_bot_factory,
    ):
        mock_bot = mock_bot_factory.return_value
        mock_bot.send_message = AsyncMock()

        from src.scheduler.triggers import _do_vault_pull_sync
        await _do_vault_pull_sync()

    mock_bot.send_message.assert_called_once()
    args, _ = mock_bot.send_message.call_args
    assert "Конфликт" in args[1]
```

### Phase I — общий DoD

- [ ] I1: `vault_pull_interval_min` в config + `.env.example`
- [ ] I2: `git_ops.pull_with_diff` возвращает классифицированный diff или None при конфликте
- [ ] I3: scheduled task `vault_pull_sync` зарегистрирована (если interval != 0)
- [ ] I4: `_do_vault_pull_sync` обрабатывает добавленные/изменённые/удалённые/переименованные файлы
- [ ] I5: тесты зелёные
- [ ] Smoke: правнуть руками файл в `data/vault/40_Projects/mnemo.md`, закоммитить, push на GitHub из локального клона; через 2 мин в логах бота `vault diff detected` + `debounced reindex flushed`

---

## 9. ФАЗА K — mnemo-brain-mcp fork + PyPI publish

> **Цель:** заменить `git clone https://github.com/desimpkins/daniel-lightrag-mcp.git && pip install -e .` на простое `pip install mnemo-brain-mcp`. Это форк апстрима с минимальным ребрендингом — логика та же.
>
> **Делается в отдельной директории** (`~/Projects/mnemo-brain-mcp/`), НЕ в этом репо. PyPI publish — владелец сам, по инструкции K3.

### K1. Локальный fork

Команды для владельца (одна сессия):

```bash
# 1. Клонируем апстрим в отдельную директорию вне Mnemo
cd ~/Projects
git clone https://github.com/desimpkins/daniel-lightrag-mcp.git mnemo-brain-mcp
cd mnemo-brain-mcp

# 2. Удаляем git-связь с апстримом, начинаем свою историю
rm -rf .git
git init
git add -A
git commit -m "Initial commit (fork of desimpkins/daniel-lightrag-mcp)"

# 3. (Опционально) создать свой репо на GitHub и запушить
# gh repo create mnemo-brain-mcp --public --source=. --push
```

### K2. Глобальный rename

В клонированной папке `~/Projects/mnemo-brain-mcp/`:

**Список замен (применить через find+sed или вручную):**

| Что | Где | На что |
|---|---|---|
| `daniel_lightrag_mcp` (snake_case) | директория модуля + все импорты | `mnemo_brain_mcp` |
| `daniel-lightrag-mcp` (kebab-case) | `pyproject.toml` `name=`, entry point | `mnemo-brain-mcp` |
| `Daniel Simpkins` (или его имя) в `pyproject.toml` `authors` | `pyproject.toml` | имя владельца + email |
| Описание пакета (`description=...`) | `pyproject.toml` | "Mnemo Brain MCP — query your Mnemo second brain from Claude Code, Cursor, Cline, and other MCP-compatible tools" |
| Версия | `pyproject.toml` | `0.1.0` (или совпадающая с апстримом) |

**Команды (примерные, проверь на месте):**

```bash
cd ~/Projects/mnemo-brain-mcp

# Переименовать директорию пакета
mv daniel_lightrag_mcp mnemo_brain_mcp

# Заменить во всех python-файлах импорты
find . -name "*.py" -type f -exec sed -i '' 's/daniel_lightrag_mcp/mnemo_brain_mcp/g' {} +

# В pyproject.toml — открыть и поправить вручную (name, description, authors, scripts)
# Аналогично — README.md в форке (см. K3)
```

⚠️ **Не использовать `replace_all` в скриптах для двух нюансов:**
1. Если в апстриме URL вида `github.com/desimpkins/daniel-lightrag-mcp` — он не переименовывается автоматически на наш repo. Проверить вручную.
2. Если апстрим использует name пакета в логах/error messages — переименовать.

### K3. README + LICENSE в форке

**Создать `~/Projects/mnemo-brain-mcp/README.md`:**

```markdown
# mnemo-brain-mcp

MCP (Model Context Protocol) bridge to query your [Mnemo](https://github.com/komrxn/Khusayinbek_brain) second brain from Claude Code, Cursor, Cline, Windsurf, and any MCP-compatible AI tool.

## What is this?

This is a friendly fork of [desimpkins/daniel-lightrag-mcp](https://github.com/desimpkins/daniel-lightrag-mcp). The logic is identical; the package is renamed for one reason: a clean PyPI install for Mnemo users.

If you're using LightRAG-server standalone (not Mnemo), use the upstream package — it has the same features and you'll get updates faster.

## Install

\`\`\`bash
pip install mnemo-brain-mcp
\`\`\`

## Configure (Claude Code example)

In `~/.claude/claude_mcp_config.json`:

\`\`\`json
{
  "mcpServers": {
    "mnemo-brain": {
      "command": "mnemo-brain-mcp",
      "env": {
        "LIGHTRAG_BASE_URL": "http://localhost:9621",
        "LIGHTRAG_API_KEY": "<paste secrets/lightrag_api_key.txt>"
      }
    }
  }
}
\`\`\`

For Mnemo-side setup, see https://github.com/komrxn/Khusayinbek_brain.

## Credit

Original code by [desimpkins](https://github.com/desimpkins). Renamed and republished with thanks. Same MIT license.

## License

MIT (inherited from upstream).
```

**LICENSE:** скопировать из апстрима как есть (если MIT — оставить MIT, добавить в копирайт `+ Komron Khakimov` второй строкой, оригинал не убирать).

### K4. PyPI account registration (для владельца)

⚠️ **Это разовый шаг. Делается один раз, потом только publish.**

1. Зайти на https://pypi.org/account/register/
2. Заполнить форму: username, email, password
3. Подтвердить email через ссылку из письма
4. Включить 2FA (обязательно для publish):
   - Settings → Account → "Add 2FA with authentication application"
   - Сканировать QR-код через Google Authenticator/Authy/1Password
   - Сохранить recovery codes в надёжном месте
5. Создать API token:
   - Settings → API tokens → "Add API token"
   - Token name: `mnemo-brain-mcp-publish`
   - Scope: «Entire account» (на первый publish; **после** успешной публикации создать второй token со scope «Project: mnemo-brain-mcp» и удалить первый)
   - Token виден ОДИН раз — скопировать в безопасное место (1Password / `~/.pypirc`)

**Сохранить token в `~/.pypirc`** (опционально, для удобства):
```ini
[distutils]
index-servers =
    pypi

[pypi]
username = __token__
password = pypi-AgENdGVzdC5weXBpLm9yZ...    ; вставить твой token
```
Права: `chmod 600 ~/.pypirc`.

### K5. Build + upload (для владельца)

В директории `~/Projects/mnemo-brain-mcp/`:

```bash
# Установить инструменты publish
python -m pip install --upgrade build twine

# Собрать дистрибутивы (создаст dist/*.whl и dist/*.tar.gz)
python -m build

# Sanity check (проверит что метаданные валидны)
python -m twine check dist/*

# Залить на PyPI
# Если есть ~/.pypirc — просто:
python -m twine upload dist/*
# Если нет — twine спросит username/password:
#   username: __token__
#   password: <вставить твой PyPI API token>
```

**Через ~30 секунд** пакет доступен через `pip install mnemo-brain-mcp`. Проверить на чистом venv:

```bash
python -m venv /tmp/test-mnemo-brain-mcp
source /tmp/test-mnemo-brain-mcp/bin/activate
pip install mnemo-brain-mcp
mnemo-brain-mcp --help    # должно работать
deactivate
```

**Дальнейшие апдейты:**
- Поднять версию в `pyproject.toml` (например `0.1.0` → `0.1.1`).
- `git tag v0.1.1 && git push --tags`.
- `python -m build && python -m twine upload dist/*` (загружает только новые версии — старые остаются).

### K6. Обновить документацию в Mnemo-репо

После того как `mnemo-brain-mcp` есть на PyPI — обновить:

**Файлы:**
- `README.md`
- `docs/README_zh.md`
- `docs/README_es.md`
- `docs/README_pt.md`
- `docs/README_fr.md`
- `docs/MCP_TESTING.md`

**Замены в каждом:**

1. Блок установки:
   ```diff
   - The bridge ([`desimpkins/daniel-lightrag-mcp`](...)) isn't on PyPI — install from source:
   - ```bash
   - git clone https://github.com/desimpkins/daniel-lightrag-mcp.git
   - cd daniel-lightrag-mcp
   - pip install -e .
   - ```
   + Install from PyPI:
   + ```bash
   + pip install mnemo-brain-mcp
   + ```
   ```

2. JSON-конфиг для Claude Code:
   ```diff
   - "command": "daniel-lightrag-mcp",
   + "command": "mnemo-brain-mcp",
   ```

3. Таблица «20 tools» — оставить как есть (логика та же).

4. В `docs/MCP_TESTING.md` — заменить чек на новую команду:
   ```diff
   - - [ ] Install from source succeeds:
   -   ```bash
   -   git clone https://github.com/desimpkins/daniel-lightrag-mcp.git
   -   cd daniel-lightrag-mcp && pip install -e .
   -   ```
   - - [ ] `daniel-lightrag-mcp --help` runs without errors
   + - [ ] `pip install mnemo-brain-mcp` succeeds in a clean venv
   + - [ ] `mnemo-brain-mcp --help` runs without errors
   ```

5. Self-referencing G9 чек (см. v2 §G9 baseline проблему):
   ```diff
   - - [ ] Поиск по README + docs/ слов `LIGHTRAG_API_URL`, `pip install daniel-lightrag-mcp`, `/graphs/labels` — везде пусто
   + - [ ] `grep -rE "LIGHTRAG_API_URL|/graphs/labels" README.md docs/ --exclude=MCP_TESTING.md` → пусто
   + - [ ] `grep -rE "git clone .*daniel-lightrag-mcp" README.md docs/` → пусто
   ```

### Phase K — общий DoD

- [ ] K1: локальный clone сделан, git-история своя
- [ ] K2: rename выполнен, `pyproject.toml` отражает новое имя/автора/описание
- [ ] K3: README в форке — короткий, ссылается на апстрим, MIT license сохранён
- [ ] K4: PyPI account создан, 2FA включена, API token создан
- [ ] K5: `python -m build && twine upload` прошёл, `pip install mnemo-brain-mcp` работает в чистом venv
- [ ] K6: 5 README + MCP_TESTING.md обновлены, `grep` ищущий старые упоминания возвращает пусто

---

## 10. Структурные фиксы (мелкие, ~30 минут)

### 10.1. Удалить старые vault-файлы
```bash
rm data/vault/20_People/anna.md
rm data/vault/30_Jobs/legai.md
rm data/vault/40_Projects/mnemo.md
rm data/vault/80_Themes/ai.md
```
(Но папки оставить + `.gitkeep` если они нужны для bootstrap.)

### 10.2. Закоммитить удаление `src/lightrag_svc/ontology.py`

В `git status` сейчас `D src/lightrag_svc/ontology.py` (deletion staged but not committed). Закоммитить отдельным коммитом или вместе с v3 фазами.

### 10.3. Self-referencing test fix в `MCP_TESTING.md`

Уже в Phase K6 — см. там.

### 10.4. (Не) добавлять новых полей frontmatter

Phase L4 расширяет `EntityInfo.typed_links` — это пайдентик-схема для LLM. На стороне frontmatter новых полей не появляется — все типизированные связи (`owner`, `works_at`, и т.д.) уже определены в фазе C v2 и поддерживаются `add_typed_link`. Только переезжаем с inline-полей на единое `typed_links: dict`.

---

## 11. Test plan — что прогнать после всех изменений

### 11.1. Lint + typecheck + unit tests
```bash
uv run ruff format src/ tests/
uv run ruff check src/ tests/
uv run mypy src/ --strict
uv run pytest -q tests/
```

### 11.2. Новые тесты (создать в Phase L/M/J/I)
- `tests/test_reindex_queue.py` (Phase J3)
- `tests/test_vault_sync.py` (Phase I5)
- (опционально) `tests/test_onboarding_multi_turn.py` — мок run_chat возвращает текст с/без `[ONBOARDING_DONE]`, проверить state-transitions

### 11.3. Smoke test (требует Docker + Telegram)
- `./setup.sh` на чистом склоне → проходит без ошибок
- `docker compose up -d --build` → 3 контейнера healthy за ≤ 2 мин
- `curl http://localhost:9621/health` → 200
- `curl -H "X-API-Key: WRONG" http://localhost:9621/graphs?label=mnemo` → 401
- В Telegram `/start` → пройти онбординг с портретом «Я работаю CTO в LegAI. Веду проект Mnemo. Анна — мой сооснователь. Увлекаюсь AI и фотографией.»
- Проверить структуру vault:
  - `_meta/owner.md` существует с `is_owner: true` + 3-7 фактов
  - `30_Jobs/legai.md` имеет `owner: [[_meta/owner]]`
  - `40_Projects/mnemo.md` имеет `owner: [[_meta/owner]]` + `for_job: [[30_Jobs/legai]]`
  - `20_People/анна.md` (с кириллицей!) имеет `works_at: [[30_Jobs/legai]]`, **БЕЗ** `owner` поля
  - `80_Themes/ai.md` имеет `owner: [[_meta/owner]]`
  - `80_Themes/фотография.md` имеет `owner: [[_meta/owner]]`
- Проверить логи: `docker compose logs bot | grep "debounced reindex flushed"` — есть как минимум одна строка
- Проверить логи: `docker compose logs bot | grep -E "Extracting stage|LLM cache"` — пусто (custom KG → no LLM extraction)
- Открыть `data/lightrag/graph_chunk_entity_relation.graphml` → найти ребро `entity_id="40_Projects/mnemo"` с типом `for_job`

### 11.4. Smoke test: мульти-турн онбординг
- Послать боту неоднозначный портрет: «Работаю в Restaurant. Веду pet-project Pixel. Раньше в LegAI был.»
- Проверить что бот переспрашивает: «Restaurant — это твоё место работы? Pixel — это часть Restaurant или личный проект? LegAI — это прошлая работа (поставить status archived) или ты ушёл оттуда насовсем?»
- Ответить на вопросы → проверить что граф построен с уточнённой инфой

### 11.5. Smoke test: Obsidian → LightRAG sync
- Вручную закоммитить в репо vault'а изменение в `40_Projects/mnemo.md` (например добавить `themes: [[80_Themes/новая-тема]]`)
- Создать `80_Themes/новая-тема.md` с правильным frontmatter
- `git push`
- Подождать 2 минуты
- Проверить логи бота: `docker compose logs bot | grep "vault diff detected"` — есть строка с `added=1, modified=1`
- Проверить что в `data/lightrag/graph_chunk_entity_relation.graphml` появилась entity `80_Themes/новая-тема`

### 11.6. Smoke test: rename hook
- В Telegram попросить бота переименовать заметку: «переименуй mnemo в mnemo-v2»
- Проверить что:
  - Файл переименован: `40_Projects/mnemo-v2.md` существует, `40_Projects/mnemo.md` нет
  - В графе LightRAG entity `40_Projects/mnemo` удалён, появился `40_Projects/mnemo-v2`
  - `kg_query` про mnemo-v2 возвращает свежие данные

### 11.7. Smoke test: ручной MCP query
- В Claude Code добавить mnemo-brain-mcp config (см. §K6)
- В Claude Code: «Что моя память знает про мой проект Mnemo?»
- Проверить что ответ ссылается на entity из графа (mnemo, legai, анна)

---

## 12. Порядок реализации (3 рабочих дня)

### День 1 (~5.5 часов): baseline + фундамент
- (30 мин) **Phase 0** — pytest/ruff/mypy baseline + коммит ontology.py deletion (§4.5)
- (10 мин) Удалить старые vault-файлы (§10.1)
- (10 мин) **L1** slugify Unicode + проверка
- (1 час) **Phase J** — debounce queue (новый модуль + переключение caller'ов + тест)
- (1.5 часа) **Phase L2/L3/L4/L5/L6** — переписать промпты + расширить EntityInfo + обновить _apply_entity
- (45 мин) Прогон pytest, ruff, mypy → fix breakage
- (45 мин) Local smoke test L+J: послать боту простой портрет, проверить структуру

### День 2 (~5 часов): онбординг + sync
- (1.5 часа) **Phase M** — мульти-турн onboarding (новые states в Redis, _run_onboarding_turn, обработка [ONBOARDING_DONE], защита от циклов)
- (1.5 часа) **Phase I** — vault pull-and-sync (git_ops.pull_with_diff, scheduler-задача, handler)
- (45 мин) Тесты для M и I (`test_onboarding_multi_turn.py` если нужен, `test_vault_sync.py`)
- (1 час) Smoke test онбординг + sync на чистом vault'е
- (15 мин) Финальный прогон pytest+ruff+mypy

### День 3 (~3 часа): mnemo-brain-mcp + публикация + финальный smoke
- (30 мин) **K1+K2** — fork + rename в `~/Projects/mnemo-brain-mcp/`
- (20 мин) **K3** — README в форке
- (15 мин) **K4** — владелец регистрируется на PyPI, создаёт token
- (10 мин) **K5** — `build + twine upload` (владелец)
- (20 мин) **K6** — обновить 5 README + MCP_TESTING.md в Mnemo-репо
- (1 час) Полный end-to-end smoke test (§11.3 + §11.4 + §11.5 + §11.6 + §11.7)
- (30 мин) Обновление `IMPLEMENTATION_PLAN_V3.md` — отметить все DoD ✅

---

## 13. Полные тексты новых промптов

> Когда дойдёшь до Phase L реализации — копируй эти тексты как есть в соответствующие файлы.

### 13.1. `prompts/onboarding.md` (полностью)

```
Ты — {{ bot_name }}, внешний мозг {{ owner_name }}.

{% if personality %}
Стиль общения: {{ personality }}
{% endif %}

# Твоя задача

Тебе прислали текстовый портрет — описание {{ owner_name }}: его жизни, работы, людей вокруг, интересов. Твоя задача: построить начальное древо знаний в Obsidian Vault. Корень древа уже создан — это `{{ owner_path }}` (заметка про самого {{ owner_name }}).

После твоей работы в Obsidian Graph должна быть видна звезда: в центре owner, вокруг — его работы, проекты, темы, воспоминания, мысли. Каждая ветвь — на 1-2 хопа от центра по типизированным связям.

# Девять типов сущностей

| Тип | Папка | Что это | Имеет owner: |
|---|---|---|---|
| person | 20_People/ | Конкретный человек | НЕТ (через works_at/about_person/related_people) |
| job | 30_Jobs/ | Место работы | ДА всегда |
| project | 40_Projects/ | Проект (личный или рабочий) | ДА всегда |
| task | 50_Tasks/ | Задача с дедлайном | ДА всегда |
| thought | 60_Thoughts/ | Мысль, инсайт, наблюдение | ДА всегда |
| memory | 70_Memories/ | Воспоминание, факт жизни | ДА всегда |
| theme | 80_Themes/ | Тема жизни, интерес, область | ДА всегда |
| inbox | 00_Inbox/ | Незавершённое — НЕ создаём в онбординге | — |
| daily | 10_Daily/ | Дневная сводка — создаётся автоматически | — |

# Восемь типов связей в frontmatter

Все связи указываются в `frontmatter` параметре `create_note`. Формат wikilink: `"[[путь/без/.md]]"`.

| Связь | Где (тип-источник) | Куда (тип-цель) | Семантика и пример |
|---|---|---|---|
| owner | job/project/task/thought/memory/theme | _meta/owner | «Это часть моей жизни». Пример: `40_Projects/mnemo` имеет `owner: "[[_meta/owner]]"` |
| works_at | person | job | «X работает в Y». Аня → `works_at: "[[30_Jobs/legai]]"` |
| for_job | project/task | job | «X делается в рамках работы Y». Mnemo → `for_job: "[[30_Jobs/legai]]"` |
| for_project | task/thought/memory | project | «X относится к проекту Y». «Деплой Mnemo» → `for_project: "[[40_Projects/mnemo]]"` |
| themes | project/thought/memory | theme (list!) | «X — про тему Y». Mnemo → `themes: ["[[80_Themes/ai]]"]` |
| about_person | thought/memory | person | «Это про человека X». «Воспоминание про Аню» → `about_person: "[[20_People/аня]]"` |
| related_people | project/job/memory | person (list!) | «В X упоминаются люди Y». Mnemo → `related_people: ["[[20_People/аня]]"]` |
| parent_theme | theme | theme | «Эта тема — подтема другой». Claude → `parent_theme: "[[80_Themes/разработка]]"` |

# Принципы построения

1. **Корень — owner.** Любая создаваемая сущность кроме person ОБЯЗАТЕЛЬНО содержит `owner: "[[_meta/owner]]"` в frontmatter. Это гарантирует что граф связан.
2. **Person — транзитивно.** Person НИКОГДА не имеет owner-поля. Связь person → owner всегда идёт через `works_at` → job → owner, или через `about_person` от memory, или через `related_people` от project.
3. **Не дубли — связи.** Если человек упомянут в двух ролях (коллега + друг) — это ОДНА нода person, со связями к разным сущностям через разные поля.
4. **Иерархия тем.** Если тема X — частный случай темы Y, у X ставь `parent_theme: "[[80_Themes/Y]]"`. Например: создаёшь тему «Claude» при упоминании работы с ним, у неё `parent_theme: "[[80_Themes/разработка]]"`.
5. **Имена.** Короткие, конкретные, на естественном языке (русский если в портрете русский, английский если английский). Темы из одного общего слова («работа», «жизнь») ЗАПРЕЩЕНЫ — они станут супер-узлами и сломают граф. Минимум 2 слова в теме, кроме случая когда тема — конкретный термин/технология (Claude, React, AI, GPT-5.4).
6. **Не угадывай.** Если в портрете не хватает информации (например непонятно: проект Mnemo рабочий или личный?) — **остановись и спроси {{ owner_name }}** прямо в Telegram одной короткой репликой. После ответа продолжай.

# Порядок работы

1. Прочитай портрет молча, в голове составь карту: какие jobs, какие themes (отдельно корневые темы и их подтемы), какие projects (для каждого: рабочий или личный? для какой работы?), какие people (для каждого: с какой работой связан?), какие важные memories, какие thoughts.

2. Создавай заметки в строгом порядке типов:
   - **(a) jobs** — все места работы
   - **(b) корневые themes** — общие темы жизни (без parent_theme)
   - **(c) suburb themes** — подтемы (с `parent_theme`)
   - **(d) projects** — все проекты, с правильными `for_job`/`themes` (jobs и themes уже существуют)
   - **(e) people** — все люди, с `works_at` если применимо (jobs существуют)
   - **(f) memories** — большие воспоминания (jobs/people/projects/themes уже есть для связей)
   - **(g) thoughts** — мысли (всё что нужно для связей уже есть)
   - **(h) tasks** — задачи (projects/jobs уже есть для for_project/for_job)

   Этот порядок гарантирует что target wikilink всегда существует на момент создания source.

3. Для каждой заметки используй `create_note` с **полным** frontmatter, включая ВСЕ типизированные связи в этом же вызове. Пример:

   ```
   create_note(
     type="project",
     title="Mnemo",
     body="Персональный AI-ассистент, второй мозг.",
     frontmatter={
       "aliases": ["Mnemo", "второй мозг"],
       "owner": "[[_meta/owner]]",
       "for_job": "[[30_Jobs/legai]]",
       "themes": ["[[80_Themes/ai]]"],
       "related_people": ["[[20_People/аня]]"]
     }
   )
   ```

4. **НЕ используй `add_link`** — это нетипизированная связь. Все связи кладутся через `frontmatter` в `create_note`.

5. Если в процессе сомневаешься — отвечай мне (через простой текст, не tool) одной короткой репликой. Например: «Уточни — Mnemo это твой рабочий проект в LegAI или личный pet-project?» Дождись ответа и продолжай.

6. Когда всё создано и граф построен — финальное сообщение должно содержать строку `[ONBOARDING_DONE]` (это сигнал что онбординг завершён). Перед ней — короткое summary: «Создал N заметок: jobs=X, themes=Y, projects=Z, people=W, memories=...» Без эмодзи, по делу.

# Жёсткие запреты

- Не создавай заметки типа `inbox` или `daily` во время онбординга.
- Не используй `add_link`. Только frontmatter в `create_note`.
- Не создавай дубликаты сущностей (одна сущность = одна заметка).
- Не выдумывай факты. Только то что явно в портрете или прямо следует.
- Не создавай темы из одного общего слова («работа», «жизнь», «дела»).
- Не угадывай связи — спрашивай.
- Не пиши `[ONBOARDING_DONE]` если ещё не закончил.
```

### 13.2. `prompts/session_extract.md` (полностью)

```
Проанализируй диалог сессии и извлеки структурированные сущности для записи в Obsidian.

# Поля результата

- summary: 2-3 предложения, суть сессии
- topic: 3-5 слов, главная тема
- entities: список сущностей упомянутых в диалоге, см. схему ниже
- thoughts: атомарные мысли заслуживающие отдельной заметки (используй entity type=thought)
- memories: долгосрочные факты которые нужно помнить (entity type=memory)
- links_to_create: пары [from_path, to_path] для нетипизированных wikilink'ов в теле (используй редко — основные связи через `typed_links` в entities)
- open_questions: незакрытые темы для follow-up

# Семь типов сущностей

person, project, task, job, theme, memory, thought.

Расположение по папкам определяется автоматически (`make_note_path`):
- person → 20_People/
- job → 30_Jobs/
- project → 40_Projects/
- task → 50_Tasks/
- thought → 60_Thoughts/
- memory → 70_Memories/
- theme → 80_Themes/

# Семантика типизированных связей

Каждая сущность кроме person получает `owner: ["_meta/owner.md"]` в `typed_links` — она часть жизни владельца. Person — транзитивно через works_at/about_person/related_people.

| Связь | Где | Куда | Семантика |
|---|---|---|---|
| owner | job/project/task/thought/memory/theme | _meta/owner.md | «Это часть моей жизни». Всегда |
| works_at | person | job | «X работает в Y» |
| for_job | project/task | job | «X делается в рамках работы Y» |
| for_project | task/thought/memory | project | «X относится к проекту Y» |
| themes | project/thought/memory | theme (несколько) | «X — про темы [Y, Z]» |
| about_person | thought/memory | person | «Это про человека Y» |
| related_people | project/job/memory | person (несколько) | «В X упоминаются люди» |
| parent_theme | theme | theme | «X — подтема Y» (для иерархии) |

# Поля entity

- type: person | project | task | job | theme | memory | thought
- name: каноническое имя (короткое, конкретное, на естественном языке)
- aliases: все варианты упоминания в этой сессии («ЛегАИ», «legai», «контора»)
- new_facts: список новых фактов из этой сессии (буллетами)
- updates: изменения статуса/состояния
- due: дедлайн YYYY-MM-DD (только для tasks)
- status: open | done | archived (только для tasks)
- typed_links: dict с типизированными связями для frontmatter

Формат `typed_links`: `{"field_name": ["target_path1.md", "target_path2.md"]}`. Single-value поля (owner, works_at, for_job, for_project, about_person, parent_theme) — список из ОДНОГО элемента. List-value поля (themes, related_people) — список из 0+ элементов.

Пример entity:
```json
{
  "type": "project",
  "name": "Mnemo MVP",
  "aliases": ["MVP", "первый запуск"],
  "new_facts": ["дедлайн 15 июня", "основная фича — голос → vault"],
  "typed_links": {
    "owner": ["_meta/owner.md"],
    "for_job": ["30_Jobs/legai.md"],
    "themes": ["80_Themes/ai.md", "80_Themes/стартапы.md"],
    "related_people": ["20_People/аня.md"]
  }
}
```

# Принципы

1. **Связывай по смыслу.** Если в диалоге Аня упомянута как сотрудница LegAI — у её entity `typed_links: {"works_at": ["30_Jobs/legai.md"]}`. Если про неё мемори как про подругу — у memory `typed_links: {"about_person": ["20_People/аня.md"]}`.

2. **Все entities кроме person получают owner.** В `typed_links` у jobs/projects/tasks/thoughts/memories/themes должно быть `"owner": ["_meta/owner.md"]`. Person — НЕ должно.

3. **Дублей нет.** Если человек/проект/тема упомянут в нескольких ролях — это ОДНА entity, все связи к ней проставляются через разные поля.

4. **Качество имён.** Короткие, конкретные. Темы — минимум 2 слова кроме случаев когда тема — конкретный термин/технология/имя собственное (Claude, React, AI, GPT-5.4). «Работа» как тема — нельзя. «Работа в ресторане» — нельзя (это уже job-сущность, а не тема). Темы — про области интересов («разработка с AI», «здоровье и спорт», «отношения»).

5. **Иерархия тем.** Если новая тема — частный случай существующей темы (которая уже есть в vault или создаётся в этой же сессии) — у новой темы `typed_links: {"parent_theme": ["80_Themes/parent.md"]}`. Пример: появилась тема «Claude», уже есть «Разработка» → у Claude `parent_theme: "[[80_Themes/разработка]]"`.

6. **Не угадывай связи.** Если не понимаешь как связать сущность — оставь её без неоднозначных связей (только `owner` если применимо). Smart linker (отдельный пост-проход) подтянет упущенное.

7. **Не выдумывай.** Включай только то что явно сказано или прямо следует из диалога.

# Если сессия пустая или техническая
Возвращай пустые массивы `entities: []`, `thoughts: []`, etc.
```

### 13.3. System-string в `src/agent/linker.py::propose_links` (полностью)

Заменить весь блок `system = (...)` на:

```python
system = (
    "Ты — построитель графа знаний на подхвате. Extractor уже извлёк сущности и "
    "проставил основные типизированные связи в их frontmatter. Твоя задача: найти "
    "СВЯЗИ КОТОРЫЕ EXTRACTOR УПУСТИЛ — между затронутыми сейчас заметками и "
    "существующим vault'ом.\n\n"
    "Допустимые типы связей и их семантика:\n"
    "- owner: «эта сущность — часть жизни владельца». Применяй для job/project/task/thought/memory/theme если у заметки нет owner. НЕ для person.\n"
    "- works_at: «X работает в Y». Только person → job.\n"
    "- for_job: «X делается в рамках работы Y». project/task → job.\n"
    "- for_project: «X относится к проекту Y». task/thought/memory → project.\n"
    "- themes: «X — про тему Y». project/thought/memory → theme (список).\n"
    "- about_person: «X — про человека Y». thought/memory → person.\n"
    "- related_people: «в X упоминается человек Y, не как главный». project/job/memory → person (список).\n"
    "- parent_theme: «X — подтема Y». theme → theme.\n\n"
    "Принципы:\n"
    f"- owner всегда указывает на: {owner_path}\n"
    "- person НЕ имеет owner-поля. Person связан транзитивно через works_at/about_person/related_people.\n"
    "- НЕ предлагай связи которые уже есть в frontmatter (existing_links показаны в контексте).\n"
    "- НЕ выдумывай. Только то что прямо следует из текста заметки.\n"
    "- НЕ возвращай связи где from_path == to_path.\n"
    "- Если уверенности нет — НЕ предлагай.\n"
    "- Один проход. Не пытайся переписать всё что сделал extractor — только дотяни упущенное.\n"
)
```

---

## 14. Анти-паттерны v3 (НЕ делать)

- ❌ Не возвращай чеклисты в промпты («task ОБЯЗАТЕЛЬНО должна иметь for_project», «memory НЕВАЛИДНА без owner»). Семантика + примеры. Если не получается без правил — значит промпт плохо написан, переписывай не по чеклисту.
- ❌ Не вводи новых типов сущностей. Только 9 существующих.
- ❌ Не добавляй person → owner. Это ломает принцип «person транзитивно».
- ❌ Не делай LLM-вызовы в цикле. Smart linker — один batch-вызов на всю сессию (он уже такой, не ломай).
- ❌ Не используй `add_link` для типизированных связей в Phase L. Только frontmatter в `create_note` или `add_typed_link`.
- ❌ Не делай auto-resolve git конфликтов в Phase I. Конфликт → алерт юзеру → стоп.
- ❌ Не публикуй secrets/API token в commit (PyPI token, GitHub token). Это в Phase K — храни в `~/.pypirc` с правами 600 или в env, не в коде.
- ❌ Не форкай `daniel-lightrag-mcp` так чтобы потерять связь с апстримом. README в форке должен явно ссылаться на оригинал и упоминать что это renamed copy.
- ❌ Не индексируй vault при VAULT_PULL_INTERVAL_MIN=0 — это явный opt-out со стороны юзера.
- ❌ Не создавай темы из одного слова. ИИ должен разбирать «он любит фотографию» как тему `фотография-как-хобби` или `фотография`-если-это-конкретная-практика. Принципы — в промпте.

---

## 15. Открытые вопросы (для владельца, если возникнут при реализации)

Если при реализации возникает ситуация которой нет в плане — ДОБАВЬ в этот раздел и СПРОСИ владельца, не реши сам.

- (open) Если агент в M3 задаёт > 3 уточнений на один кейс — это плохой портрет или плохой промпт? Сейчас лимит 5, после — принудительный wrap-up.
- (open) Если конфликт git pull в Phase I случается часто — может стоит auto-rebase с приоритетом на нашу сторону (бот — единственный writer кроме Obsidian-юзера, конфликты редки)? Пока — алерт + стоп.
- (open) Если LightRAG-граф разрастётся > 10к нод — `full_reindex` (singleton swap) может занять минуты. Не блокирующая проблема для personal use, но если станет — делать инкрементальный clear через `adelete_by_entity` для всех старых.

---

## 16. Definition of Done (полный, для всей v3)

Все галочки должны быть отмечены ✅ в этом файле перед тем как закрыть v3:

### Phase 0 — Validate v2 baseline
- [ ] 0.1: ruff/mypy/pytest зелёные на чистом v2-коде
- [ ] 0.2: deletion `src/lightrag_svc/ontology.py` закоммичена
- [ ] 0.3 (опц): docker compose up — 3 healthy

### Phase L — Семантические промпты
- [ ] L1: slugify Unicode (3 кейса проверены)
- [ ] L2: prompts/onboarding.md = §13.1
- [ ] L3: prompts/session_extract.md = §13.2
- [ ] L4: EntityInfo.typed_links + _apply_entity обработка
- [ ] L5: linker.py system-string = §13.3
- [ ] L6: prompts/system.md строчка про переспрашивание усилена

### Phase M — Multi-turn onboarding
- [ ] M1: новый порядок (owner first → agent с инструкцией)
- [ ] M2: _create_owner_note не тронут
- [ ] M3: agent_question state, _run_onboarding_turn, [ONBOARDING_DONE] sentinel
- [ ] M4: max-turns защита (5)

### Phase J — Reindex debounce
- [ ] J1: src/lightrag_svc/reindex_queue.py с enqueue + flush_now
- [ ] J2: linking.py + extractor._index_created переключены на enqueue
- [ ] J3: tests/test_reindex_queue.py зелёный
- [ ] J4: main.py flush_now на shutdown

### Phase I — Vault pull-and-sync
- [ ] I1: vault_pull_interval_min в config + .env.example
- [ ] I2: git_ops.pull_with_diff
- [ ] I3: scheduler-задача vault_pull_sync (с условной регистрацией)
- [ ] I4: _do_vault_pull_sync handler
- [ ] I5: tests/test_vault_sync.py зелёный

### Phase K — mnemo-brain-mcp + PyPI
- [ ] K1: локальный fork в ~/Projects/mnemo-brain-mcp
- [ ] K2: rename выполнен
- [ ] K3: README + LICENSE в форке
- [ ] K4: PyPI account + 2FA + API token
- [ ] K5: build + upload, `pip install mnemo-brain-mcp` работает в чистом venv
- [ ] K6: 5 README + MCP_TESTING.md обновлены, grep чист

### Структурные фиксы
- [ ] 10.1: старые vault-файлы удалены
- [ ] 10.2: deletion ontology.py закоммичен
- [ ] 10.3: self-referencing test fix в MCP_TESTING.md (часть K6)

### Тесты
- [ ] §11.1: ruff format/check + mypy --strict + pytest — всё зелёное
- [ ] §11.2: новые unit-тесты созданы и зелёные
- [ ] §11.3: полный smoke onboarding (с проверкой структуры vault и графа)
- [ ] §11.4: smoke мульти-турн онбординг
- [ ] §11.5: smoke Obsidian → LightRAG sync
- [ ] §11.6: smoke rename hook
- [ ] §11.7: smoke MCP query из Claude Code

### Документация
- [ ] IMPLEMENTATION_PLAN.md (v2) помечен как LEGACY/CLOSED — ✅ сделано
- [ ] IMPLEMENTATION_PLAN_V3.md — этот файл — все DoD ✅
- [ ] README.md синхронизирован с реальным поведением (особенно секции про MCP install и про owner anchor)

---

**Когда все DoD ✅ — версия v3 закрыта. Делай PR с полным описанием эффекта (длина графа, число рёбер, время онбординга до/после). После merge этот файл переходит в legacy режим, как сейчас v2.**
