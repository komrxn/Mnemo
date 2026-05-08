# Mnemo — Implementation Plan v2 (Graph Quality) — ⚠️ LEGACY / CLOSED

> 🔒 **СТАТУС: ЗАКРЫТ.** Все фазы A–K реализованы. Этот файл — историческая справка
> (как мы дошли от v1 к v2). Текущий источник правды для разработки —
> [`IMPLEMENTATION_PLAN_V3.md`](IMPLEMENTATION_PLAN_V3.md).
>
> v3 переводит ассистента в режим «строитель мозга»: ИИ сам понимает семантику связей,
> onboarding становится мульти-турн с уточняющими вопросами, ручные правки в Obsidian
> синхронизируются обратно в граф. Не редактируй v2-план — все новые решения идут в v3.

---

# Mnemo — Implementation Plan v2 (Graph Quality)

> **Назначение проекта:** ИИ-ассистент в Telegram, «внешний мозг» владельца. Текст/голос/фото в чате → структурированные заметки в Obsidian Vault → граф знаний (LightRAG) → проактивные крон-уведомления.
>
> **Single user.** Whitelist Telegram ID.
>
> **Этот документ — единственный источник правды.** Не отступай ни на шаг. Если что-то неясно — спрашивай владельца. Лучше остановиться, чем нагородить.
>
> **Этот план — про качество графа.** Базовая инфраструктура (Telegram, Redis, vault writer, agent loop, LightRAG, scheduler, multimodal) уже работает. Здесь только то, что нужно сделать **сейчас**, чтобы граф стал плотным, типизированным и без дубликатов.

---

## 0. Что уже готово (НЕ переделывать)

✅ Telegram-бот на aiogram 3 — текст/голос/фото, whitelist, typing-indicator
✅ Redis-сессии — open/close, sliding TTL, topic-shift detector, /save, /undo
✅ Vault writer — единственная точка записи + git commit на каждое изменение
✅ Agent loop — OpenAI function calling, retries, structured outputs
✅ Tools: obsidian.* (10 шт), lightrag.* (3), scheduler.* (4), misc.*
✅ LightRAG embedded — incremental + full reindex
✅ APScheduler с RedisJobStore + 5 дефолтных задач
✅ Inline-confirmations через Redis pub/sub
✅ Multi-step onboarding (имя → стиль → портрет → подтверждение)
✅ Docker compose: bot + redis с volumes

❌ **Граф рыхлый.** Это и решаем в фазах A–E ниже.

---

## 1. Цель этого этапа

Перевести граф vault'а из состояния «звезда вокруг daily-note» в состояние «типизированная сеть с владельцем-якорем, без дубликатов».

**Метрика успеха:**
- Каждая заметка типа `project|task|memory|thought` имеет минимум 2 типизированные связи в frontmatter.
- Дубликаты сущностей < 5% от общего количества заметок.
- В Obsidian Graph владелец и темы — крупнейшие узлы по степени связности.
- LightRAG kg_query на запрос «расскажи про X» возвращает связные сущности, а не отдельные чанки.

---

## 2. Топология vault (не менять)

```
/
├── _meta/
│   ├── owner.md              # ⭐ НОВОЕ — нормализованная сущность владельца
│   ├── portrait.md           # сырой текст портрета (как сейчас)
│   ├── ontology.md           # ⭐ НОВОЕ — авто-сгенерированные entity_types для LightRAG
│   ├── scheduled_tasks.md
│   ├── MOC_People.md         # ⭐ НОВОЕ — карта контента
│   ├── MOC_Projects.md       # ⭐ НОВОЕ
│   ├── MOC_Jobs.md           # ⭐ НОВОЕ
│   └── MOC_Themes.md         # ⭐ НОВОЕ
├── 00_Inbox/
├── 10_Daily/
├── 20_People/
├── 30_Jobs/
├── 40_Projects/
├── 50_Tasks/
├── 60_Thoughts/
├── 70_Memories/
├── 80_Themes/
└── 90_Attachments/
```

**Что НЕ делать:**
- ❌ Не переименовывать существующие папки.
- ❌ Не вводить новые типы заметок (только 9 уже существующих).
- ❌ Не создавать новые корневые директории.

---

## 3. Расширенный контракт frontmatter

Добавляются типизированные поля связей. **Все поля опциональны**, но если присутствуют — должны быть валидным wikilink (`"[[path/to/note]]"`).

```yaml
---
type: task | thought | person | job | project | memory | theme | daily | inbox
created: 2026-05-07T14:32:00+05:00
updated: 2026-05-07T14:32:00+05:00
tags: [tag1, tag2]
status: open | done | archived       # для tasks
due: 2026-05-10                       # для tasks
session_id: ses_2026-05-07_14-15
session_ids: [ses_..., ses_...]       # ⭐ список всех сессий, в которых заметка обновлялась

# ⭐ НОВОЕ — Алиасы (варианты имени для дедупликации)
aliases: ["LegAI", "ЛегАИ", "legai-проект"]

# ⭐ НОВОЕ — Owner flag (только в _meta/owner.md)
is_owner: true

# ⭐ НОВОЕ — Типизированные связи (см. таблицу ниже)
owner: "[[_meta/owner]]"              # для project, job, task — указывает что заметка про владельца
works_at: "[[30_Jobs/foo]]"           # для person
employs: ["[[20_People/foo]]"]        # для job (обратное к works_at)
for_job: "[[30_Jobs/foo]]"            # для project, task
for_project: "[[40_Projects/foo]]"    # для task, thought, memory
themes: ["[[80_Themes/foo]]"]         # для project, thought, memory
about_person: "[[20_People/foo]]"     # для memory, thought
related_people: ["[[20_People/x]]"]   # для project, job
parent_theme: "[[80_Themes/foo]]"     # для theme (иерархия тем)
sub_themes: ["[[80_Themes/foo]]"]     # для theme (обратное к parent_theme)
---
```

### 3.1. Таблица типов связей и их инверсий

| Прямая связь | На каком типе | Цель | Обратная связь | На каком типе |
|---|---|---|---|---|
| `owner` | project, job, task, memory, thought | person (owner.md) | — | (owner не имеет обратных, иначе он будет линкован ко всему) |
| `works_at` | person | job | `employs` (list) | job |
| `for_job` | project, task | job | `projects` (list) / `tasks` (list) | job |
| `for_project` | task, thought, memory | project | `tasks` / `thoughts` / `memories` (lists) | project |
| `themes` (list) | project, thought, memory | theme | `examples` (list) | theme |
| `about_person` | memory, thought | person | `mentions` (list) | person |
| `related_people` (list) | project, job, memory | person | `involved_in` (list) | person |
| `parent_theme` | theme | theme | `sub_themes` (list) | theme |

**Правила обратных связей:**
1. Прямая связь = **single value** wikilink. Обратная = **list** wikilinks.
2. Обратная связь добавляется **автоматически** при добавлении прямой — через `add_typed_link()` (см. Фазу C).
3. Owner — единственное исключение, без обратной связи (иначе owner.md взорвётся).

---

## ФАЗА A — Owner anchor + Dedup ✅ DONE

> **Зачем:** без этого граф остаётся разреженным. Все сущности должны якориться к владельцу, и нельзя плодить дубликаты ("rabota", "rabota-v-restorane-bek", "bek").

### A1. Создать `_meta/owner.md` в onboarding

**Файл:** `src/telegram/handlers/text.py` → функция `_run_onboarding_execute()`

**Шаги:**

1. После того как агент создал заметки, и после `vault_writer.write_note("_meta/portrait.md", portrait, ...)`, **дополнительно вызвать** новую функцию `_create_owner_note()`.

2. `_create_owner_note(profile, portrait, reply_fn)`:
   - Собрать имя владельца из `profile.get("bot_name")` НЕ ПОДХОДИТ — это имя бота. Нужно отдельно.
   - **Перед началом step_portrait** добавить новый шаг `step_owner_name`: «Как тебя зовут? (твоё имя/ник, чтобы ассистент к тебе обращался)».
   - Сохранить в profile как `owner_name`.
   - В `_create_owner_note`:
     - Сгенерировать тело LLM-вызовом: «Извлеки из портрета ключевые факты о владельце в формате bullet-list (3-7 пунктов). Используй только что явно сказано». Вход — текст портрета.
     - Записать `_meta/owner.md` с frontmatter:
       ```yaml
       type: person
       is_owner: true
       aliases: [<owner_name>, <варианты которые упоминались в портрете>]
       ```
     - aliases собираются LLM из портрета (тот же запрос).

3. **Сохранить путь в Redis profile:** `await session_mgr.update_profile(redis, user_id, {"owner_path": "_meta/owner.md", "owner_name": <name>})`.

**Что НЕ делать:**
- ❌ Не дублировать содержимое portrait.md в owner.md. Owner — короткая нормализованная карточка, portrait — длинный сырой текст.
- ❌ Не создавать owner.md в `20_People/` — он специальный, лежит в `_meta/`.
- ❌ Не давать owner поля `works_at` или другие связи в момент создания. Связи добавит smart linker (фаза B) при первой реальной сессии.

**DoD A1:** после первого онбординга `_meta/owner.md` существует, `is_owner: true` в frontmatter, в Redis-профиле есть `owner_path` и `owner_name`.

---

### A2. Модуль дедупликации `src/vault/dedup.py`

**Создать новый файл** `src/vault/dedup.py`:

```python
from __future__ import annotations
from pathlib import Path
from typing import Literal

from rapidfuzz import fuzz, process

from src.config import settings
from src.vault import reader
from src.vault.frontmatter import TYPE_FOLDERS, parse


# Тип результата
class DedupCandidate(BaseModel):
    path: str
    title: str
    aliases: list[str]
    score: int  # 0-100


async def find_similar(
    note_type: str,
    title: str,
    aliases: list[str] = None,
    threshold: int = 70,
) -> list[DedupCandidate]:
    """Найти существующие заметки того же типа со схожим title или aliases.

    Возвращает кандидатов отсортированных по score убыванию.
    threshold: минимальный score для включения в результат.
    """
    folder = TYPE_FOLDERS.get(note_type)
    if not folder:
        return []

    vault = Path(settings.vault_path)
    folder_path = vault / folder
    if not folder_path.exists():
        return []

    # Собираем все существующие заметки этого типа
    existing: list[tuple[str, list[str]]] = []  # [(path, [title, *aliases])]
    for md_file in folder_path.rglob("*.md"):
        rel = str(md_file.relative_to(vault))
        raw = md_file.read_text(encoding="utf-8")
        fm, _ = parse(raw)
        existing_title = md_file.stem.replace("-", " ")
        existing_aliases = fm.get("aliases", []) or []
        existing.append((rel, [existing_title, *existing_aliases]))

    # Фаззи-поиск
    queries = [title.lower()] + [a.lower() for a in (aliases or [])]
    candidates: dict[str, int] = {}  # path → max_score

    for path, names in existing:
        names_lower = [n.lower() for n in names]
        for q in queries:
            for name in names_lower:
                score = fuzz.WRatio(q, name)
                if score >= threshold:
                    candidates[path] = max(candidates.get(path, 0), score)

    return [
        DedupCandidate(
            path=p,
            title=Path(p).stem,
            aliases=[],  # не нужно, агент сам прочитает
            score=s,
        )
        for p, s in sorted(candidates.items(), key=lambda x: -x[1])
    ]
```

**Зависимость:** добавить `rapidfuzz>=3.10` в `pyproject.toml`.

**Что НЕ делать:**
- ❌ Не использовать `difflib.SequenceMatcher` — медленно. Только `rapidfuzz`.
- ❌ Не сравнивать с заметками других типов. `task` не должен матчиться против `project`.
- ❌ Не считать score по полному содержимому заметки — только по title и aliases.

---

### A3. Использовать дедупликацию в create_note

**Файл:** `src/tools/obsidian.py` → функция `_create_note()`

**Изменить логику:**

```python
async def _create_note(p: CreateNoteParams, session_id: str = "") -> str:
    # ⭐ НОВОЕ: проверить дубликаты ПЕРЕД созданием
    from src.vault.dedup import find_similar

    aliases = p.frontmatter.get("aliases", []) if p.frontmatter else []
    candidates = await find_similar(p.type, p.title, aliases, threshold=85)

    if candidates and candidates[0].score >= 90:
        # Очень похожая заметка существует → НЕ СОЗДАЁМ, возвращаем существующую
        existing_path = candidates[0].path
        # Сливаем aliases
        from src.vault import writer as vault_writer
        existing_aliases = (await _read_existing_aliases(existing_path))
        merged = sorted(set(existing_aliases + [p.title] + aliases))
        await vault_writer.update_frontmatter(
            existing_path, {"aliases": merged}, session_id
        )
        return (
            f"найден дубликат: {existing_path} (score={candidates[0].score}). "
            f"Используй append_to_note для дописывания фактов."
        )

    if candidates and candidates[0].score >= 70:
        # Сомнительный кейс → возвращаем кандидатов, агент сам решит
        cand_str = "\n".join(
            f"  - {c.path} (score={c.score})" for c in candidates[:3]
        )
        return (
            f"возможные дубликаты для {p.title!r}:\n{cand_str}\n"
            f"Если это та же сущность — используй append_to_note к существующей. "
            f"Если это другая сущность — повтори create_note с уточнённым title."
        )

    # Нет дубликатов → создаём как обычно
    rel_path = make_note_path(p.type, p.title)
    fm: dict[str, Any] = {"type": p.type, **p.frontmatter}

    # ⭐ Гарантируем что aliases есть (хотя бы пустой список)
    if "aliases" not in fm:
        fm["aliases"] = []

    body = p.body
    if p.links:
        link_section = "\n".join(f"[[{lnk}]]" for lnk in p.links)
        body = body.rstrip() + f"\n\n## Связи\n\n{link_section}"

    sha = await writer.write_note(rel_path, body, fm, session_id)
    return f"создано: {rel_path} (sha={sha[:8]})"


async def _read_existing_aliases(rel_path: str) -> list[str]:
    note = reader.read_note(rel_path)
    return note.frontmatter.aliases or []
```

**Что НЕ делать:**
- ❌ Не создавать заметку и потом удалять при дубликате. Только проверка ДО.
- ❌ Не делать threshold ниже 70. Получим ложные срабатывания.
- ❌ Не пропускать threshold≥85, иначе агент решит создать новую.

**DoD A3:** на тесте создать `create_note(type=project, title="LegAI")`, потом `create_note(type=project, title="legai")` → второй вызов вернёт «найден дубликат», а не создаст новую заметку.

---

### A4. Алиасы в экстракторе

**Файл:** `src/agent/extractor.py` → схема `EntityInfo`

**Изменить:**

```python
class EntityInfo(BaseModel):
    type: Literal["person", "project", "task", "job", "theme", "memory", "thought"]
    name: str
    aliases: list[str] = []           # ⭐ НОВОЕ
    new_facts: list[str] = []
    updates: list[str] = []
    due: str = ""
    status: str = "open"
```

**Файл:** `prompts/session_extract.md` — добавить в правила:

```markdown
- aliases: все варианты упоминания этой сущности в диалоге (например: "LegAI", "ЛегАИ", "legai-проект"). Включай только то, что явно встречалось в тексте.
```

**Файл:** `src/agent/extractor.py` → `_apply_entity()` — пробросить aliases в frontmatter:

```python
fm: dict[str, Any] = {"type": note_type, "aliases": entity.aliases}
```

**DoD A4:** после сессии где упоминался «легаи и ещё этот legai-проект» → в `40_Projects/legai.md` frontmatter содержит `aliases: ["легаи", "legai-проект"]`.

---

### Фаза A — общий DoD

- [ ] `_meta/owner.md` существует после онбординга, `is_owner: true`
- [ ] В Redis-профиле есть `owner_path`, `owner_name`
- [ ] `find_similar()` работает, есть тест (см. §10)
- [ ] `create_note` блокирует создание дубликатов с score≥90
- [ ] Все новые заметки имеют поле `aliases` в frontmatter (хотя бы `[]`)

---

## ФАЗА B — Smart linker ✅ DONE

> **Зачем:** агент во время сессии не успевает аккуратно слинковать всё. Нужен пост-обработчик который видит вообще всё и создаёт типизированные связи.

### B1. Модуль `src/agent/linker.py`

**Создать новый файл.**

```python
from __future__ import annotations
from pathlib import Path
from typing import Literal

import structlog
from pydantic import BaseModel

from src.agent.loop import get_client
from src.config import settings
from src.vault import reader
from src.vault.frontmatter import parse

logger = structlog.get_logger()


# Допустимые типы связей (см. §3.1 плана)
RelationType = Literal[
    "owner", "works_at", "for_job", "for_project",
    "themes", "about_person", "related_people", "parent_theme",
]


class LinkProposal(BaseModel):
    from_path: str       # vault-relative
    to_path: str         # vault-relative
    relation: RelationType


class LinkBatch(BaseModel):
    links: list[LinkProposal]


async def propose_links(
    touched_paths: list[str],
    owner_path: str,
) -> list[LinkProposal]:
    """Запустить LLM-проход который предлагает типизированные связи между затронутыми заметками и существующим vault'ом."""
    if not touched_paths:
        return []

    # Собрать контекст: затронутые заметки + кандидаты
    touched_context = _build_touched_context(touched_paths)
    candidates_context = _build_candidates_context(touched_paths)

    if not candidates_context:
        return []

    system = (
        "Ты — построитель графа знаний. На вход — заметки сессии и существующие "
        "заметки vault'а. Твоя задача: предложить типизированные связи между ними.\n\n"
        f"Тип связи — один из: owner, works_at, for_job, for_project, themes, "
        f"about_person, related_people, parent_theme.\n\n"
        f"Правила:\n"
        f"- task должна иметь for_project ИЛИ for_job\n"
        f"- project должен иметь for_job (если работа) ИЛИ themes (если личный)\n"
        f"- memory или thought про человека → about_person\n"
        f"- memory или thought про тему/проект → themes или for_project\n"
        f"- любая заметка про владельца/связанная с его жизнью → owner: {owner_path}\n"
        f"- НЕ предлагай связи которые уже существуют в frontmatter\n"
        f"- НЕ выдумывай связи. Только то что прямо следует из текста.\n"
        f"- НЕ возвращай связи где from_path == to_path\n"
    )

    user = (
        f"=== Заметки этой сессии (предлагать связи ОТ них) ===\n{touched_context}\n\n"
        f"=== Существующие заметки vault'а (можно линковаться К ним) ===\n"
        f"{candidates_context}\n\n"
        "Верни JSON по схеме LinkBatch."
    )

    client = get_client()
    response = await client.beta.chat.completions.parse(
        model=settings.openai_model_main,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        response_format=LinkBatch,
    )
    parsed = response.choices[0].message.parsed
    if parsed is None:
        return []
    return parsed.links


def _build_touched_context(paths: list[str]) -> str:
    """Frontmatter + первые 200 слов тела для каждой затронутой заметки."""
    vault = Path(settings.vault_path)
    blocks = []
    for rel in paths:
        full = vault / rel
        if not full.exists() or full.suffix != ".md":
            continue
        raw = full.read_text(encoding="utf-8")
        fm, body = parse(raw)
        snippet = " ".join(body.split()[:200])
        blocks.append(
            f"--- {rel} ---\n"
            f"type: {fm.get('type', '?')}\n"
            f"aliases: {fm.get('aliases', [])}\n"
            f"existing_links: {_extract_existing_links(fm)}\n"
            f"body: {snippet}\n"
        )
    return "\n".join(blocks)


def _build_candidates_context(touched_paths: list[str]) -> str:
    """Все заметки vault'а кроме затронутых, сгруппированные по типу.

    Лимит: 100 заметок. Если больше — берём последние по mtime.
    """
    vault = Path(settings.vault_path)
    touched_set = set(touched_paths)
    by_type: dict[str, list[tuple[str, str, list[str]]]] = {}

    for md in vault.rglob("*.md"):
        rel = str(md.relative_to(vault))
        if rel in touched_set:
            continue
        raw = md.read_text(encoding="utf-8")
        fm, _ = parse(raw)
        typ = fm.get("type", "inbox")
        title = md.stem
        aliases = fm.get("aliases", []) or []
        by_type.setdefault(typ, []).append((rel, title, aliases))

    blocks = []
    for typ, items in sorted(by_type.items()):
        blocks.append(f"## {typ}")
        for rel, title, aliases in items[:30]:
            ali = f" (alias: {aliases})" if aliases else ""
            blocks.append(f"- {rel}{ali}")
    return "\n".join(blocks)


def _extract_existing_links(fm: dict) -> list[str]:
    """Достать все wikilinks из типизированных полей frontmatter."""
    keys = ["owner", "works_at", "for_job", "for_project", "themes",
            "about_person", "related_people", "parent_theme"]
    result = []
    for k in keys:
        v = fm.get(k)
        if isinstance(v, str) and v:
            result.append(v)
        elif isinstance(v, list):
            result.extend(v)
    return result
```

**Что НЕ делать:**
- ❌ Не передавать в контекст полное содержимое всех заметок vault — только title+aliases. Иначе токены кончатся.
- ❌ Не запрашивать LLM про связи каждой пары заметок отдельно — один batch-вызов на всю сессию.
- ❌ Не создавать связи через `_add_link()` старого образца — использовать новый `add_typed_link()` (фаза C).

---

### B2. Применение предложенных связей

**Файл:** `src/agent/linker.py` — добавить:

```python
async def apply_links(links: list[LinkProposal], session_id: str) -> int:
    """Применить связи через add_typed_link. Возвращает количество применённых."""
    from src.vault.linking import add_typed_link

    applied = 0
    for link in links:
        try:
            ok = await add_typed_link(
                from_path=link.from_path,
                to_path=link.to_path,
                relation=link.relation,
                session_id=session_id,
            )
            if ok:
                applied += 1
        except Exception as exc:
            logger.warning(
                "link apply failed",
                from_=link.from_path, to=link.to_path,
                relation=link.relation, error=str(exc),
            )
    return applied
```

**Что НЕ делать:**
- ❌ Не падать всей пайплайн если одна связь невалидна. Просто логировать и продолжать.

---

### B3. Хук в session-end pipeline

**Файл:** `src/agent/extractor.py` → функция `apply_to_vault()`

**Изменить:** после применения `links_to_create` и до `git_ops.push()` добавить:

```python
# ⭐ НОВОЕ — Smart linker post-pass
try:
    from src.agent.linker import propose_links, apply_links
    from src.session import manager as session_mgr

    redis = await session_mgr.get_redis()
    # owner_path берём из профиля любого whitelisted пользователя
    user_id = settings.allowed_user_ids[0]
    profile = await session_mgr.get_profile(redis, user_id)
    owner_path = str(profile.get("owner_path", "_meta/owner.md"))

    proposals = await propose_links(created, owner_path)
    applied = await apply_links(proposals, session_id)
    logger.info("smart linker done", proposed=len(proposals), applied=applied)
except Exception as exc:
    logger.warning("smart linker skipped", error=str(exc))
```

**Что НЕ делать:**
- ❌ Не падать пайплайн если linker сломался — это нон-критичный пост-проход.
- ❌ Не вызывать linker до `apply_to_vault` — нужны уже созданные заметки в файловой системе.

---

### B4. Жёсткие правила в session_extract промпте

**Файл:** `prompts/session_extract.md` — добавить раздел:

```markdown
## Обязательные правила линковки

При извлечении сущностей помни:
- task без проекта/работы — невалиден. Если упомянут таск, обязательно укажи в `links_to_create` пару `(task_path, project_or_job_path)` где project_or_job — существующая или создаваемая в этой же сессии заметка.
- project в работе → links_to_create включает пару `(project, job)`.
- memory/thought про конкретного человека → links_to_create `(memory, person)`.
- любая сущность связанная с владельцем (рабочий проект, личная память) → links_to_create `(entity, "_meta/owner.md")`.

Если ты не уверен с какой существующей заметкой связать — НЕ выдумывай. Smart linker дополнит связи постфактум, ему виднее.
```

**DoD B:** после тестовой сессии где упомянут «новый таск к проекту LegAI» — в `50_Tasks/<task>.md` после session-end pipeline в frontmatter `for_project: "[[40_Projects/legai]]"` и `owner: "[[_meta/owner]]"`. И в `40_Projects/legai.md` в `tasks: [..., "[[50_Tasks/<task>]]"]`.

---

## ФАЗА C — Frontmatter-driven граф ✅ DONE

> **Зачем:** Obsidian Graph рисует рёбра из wikilinks в теле. А мы храним связи в frontmatter. Нужен «мост»: автогенерация секции `## Связи` в теле из frontmatter + двунаправленность.

### C1. Маппинг прямых ↔ обратных связей

**Файл:** `src/vault/frontmatter.py` — добавить:

```python
# Маппинг прямой связи на обратную: (forward_field, is_list_on_target, reverse_field, is_list_on_self)
RELATION_INVERSE: dict[str, tuple[str, bool]] = {
    "works_at": ("employs", True),
    "for_job": ("projects", True),       # на job-стороне
    "for_project": ("tasks", True),      # на project-стороне (возможно перекроется ниже)
    "themes": ("examples", True),
    "about_person": ("mentions", True),
    "related_people": ("involved_in", True),
    "parent_theme": ("sub_themes", True),
    # owner — намеренно не инвертируется (см. §3.1)
}

# Поля связей которые рендерятся в "## Связи" блок тела
LINK_FIELDS: list[str] = [
    "owner", "works_at", "for_job", "for_project", "themes",
    "about_person", "related_people", "parent_theme",
    "employs", "projects", "tasks", "examples",
    "mentions", "involved_in", "sub_themes",
]
```

**Особый кейс:** `for_project` в task → обратная связь `tasks` на project. `for_project` в memory → обратная связь `memories` на project. `for_project` в thought → обратная связь `thoughts` на project. Чтобы это работало, обратное поле определяем динамически:

```python
def get_inverse_field(forward: str, source_type: str) -> str | None:
    """Вернуть имя обратного поля на target.

    Спец-логика для for_project: на target поле зависит от source_type.
    """
    if forward == "for_project":
        return {
            "task": "tasks",
            "memory": "memories",
            "thought": "thoughts",
        }.get(source_type)
    if forward == "for_job":
        return {
            "task": "tasks",
            "project": "projects",
        }.get(source_type)

    inv = RELATION_INVERSE.get(forward)
    return inv[0] if inv else None
```

**Что НЕ делать:**
- ❌ Не делать ВСЕ связи двунаправленными автоматически — owner не должен иметь обратных.
- ❌ Не путать `tasks` (на job) с `tasks` (на project) — они разные. Спец-логика по source_type.

---

### C2. Модуль `src/vault/linking.py`

**Создать новый файл:**

```python
from __future__ import annotations
from pathlib import Path

import structlog

from src.config import settings
from src.vault import reader, writer
from src.vault.frontmatter import (
    LINK_FIELDS, get_inverse_field, parse, serialize,
)

logger = structlog.get_logger()


async def add_typed_link(
    from_path: str,
    to_path: str,
    relation: str,
    session_id: str = "",
) -> bool:
    """Добавить типизированную связь. Возвращает True если связь была добавлена.

    Поведение:
    1. Проверить что обе заметки существуют. Если нет — return False, лог.
    2. Прочитать frontmatter from_path.
    3. Положить связь в нужное поле frontmatter (single value или append в list).
    4. Если связь уже есть → return False.
    5. Записать from_path обратно с обновлённым frontmatter.
    6. Перегенерировать секцию "## Связи" в теле (см. C3).
    7. Вычислить обратное поле и source_type. Применить аналогично к to_path.
    8. Один git commit с сообщением 'link from_path -[relation]-> to_path'.
    """
    if not reader.note_exists(from_path) or not reader.note_exists(to_path):
        logger.warning("typed link skipped: missing note",
                       from_=from_path, to=to_path)
        return False

    if from_path == to_path:
        return False

    wikilink_to = f"[[{to_path.removesuffix('.md')}]]"

    # Читаем from_path
    from_full = Path(settings.vault_path) / from_path
    raw = from_full.read_text(encoding="utf-8")
    fm, body = parse(raw)

    # Определяем тип поля (single или list)
    is_list = relation in {"themes", "related_people", "employs", "projects",
                            "tasks", "examples", "mentions", "involved_in",
                            "sub_themes", "memories", "thoughts"}

    if is_list:
        existing = fm.get(relation, []) or []
        if wikilink_to in existing:
            return False  # уже есть
        fm[relation] = sorted(set(existing + [wikilink_to]))
    else:
        if fm.get(relation) == wikilink_to:
            return False
        fm[relation] = wikilink_to

    # Пере-рендер секции связей
    body = render_links_section(fm, body)
    from_full.write_text(serialize(fm, body), encoding="utf-8")

    # Обратная связь
    source_type = fm.get("type", "")
    inverse = get_inverse_field(relation, source_type)
    if inverse and to_path != "_meta/owner.md":  # owner не инвертируется
        to_full = Path(settings.vault_path) / to_path
        raw_to = to_full.read_text(encoding="utf-8")
        fm_to, body_to = parse(raw_to)
        wikilink_from = f"[[{from_path.removesuffix('.md')}]]"

        existing_inv = fm_to.get(inverse, []) or []
        if wikilink_from not in existing_inv:
            fm_to[inverse] = sorted(set(existing_inv + [wikilink_from]))
            body_to = render_links_section(fm_to, body_to)
            to_full.write_text(serialize(fm_to, body_to), encoding="utf-8")

    # Один commit на обе стороны
    from src.vault import git_ops
    paths_to_commit = [from_path]
    if inverse and to_path != "_meta/owner.md":
        paths_to_commit.append(to_path)
    await git_ops.stage_and_commit(
        Path(settings.vault_path),
        paths_to_commit,
        f"link {from_path} -[{relation}]-> {to_path} [session={session_id}]",
    )
    return True


def render_links_section(fm: dict, body: str) -> str:
    """Сгенерировать/обновить секцию '## Связи' в body на основе LINK_FIELDS frontmatter."""
    # Удалить старую секцию если есть
    marker = "\n## Связи\n"
    if marker in body:
        body = body.split(marker)[0].rstrip() + "\n"

    # Собрать все связи
    items: list[str] = []
    for field in LINK_FIELDS:
        v = fm.get(field)
        if not v:
            continue
        if isinstance(v, str):
            items.append(f"- **{field}**: {v}")
        elif isinstance(v, list):
            for item in v:
                items.append(f"- **{field}**: {item}")

    if not items:
        return body

    section = "\n## Связи\n\n" + "\n".join(items) + "\n"
    return body.rstrip() + section
```

**Что НЕ делать:**
- ❌ Не создавать секцию `## Связи` если в frontmatter нет ни одного link-поля.
- ❌ Не дублировать одинаковые wikilinks в одном поле — `sorted(set(...))`.
- ❌ Не парсить body для извлечения старых связей — мы их перегенерируем целиком.
- ❌ Не делать commit для каждой стороны отдельно — один commit на пару.
- ❌ Не использовать `add_typed_link` если знаешь что связь уже есть в frontmatter — тратишь IO.

---

### C3. Заменить старый _add_link на add_typed_link где возможно

**Файл:** `src/tools/obsidian.py`

**Старый `_add_link` оставить** (для обратной совместимости и явных нетипизированных связей через `add_link` тул). Но в `apply_to_vault` (extractor.py) для `links_to_create` лучше дописать улучшенную логику:

Если LLM в `links_to_create` указал пару — старая логика просто добавит wikilink в `## Связи`. Это нормально, оставить как есть. **Smart linker** (фаза B) создаёт типизированные связи через `add_typed_link`.

**Что НЕ делать:**
- ❌ Не удалять старый `_add_link` — он используется агентом во время сессии и для явных пользовательских команд «свяжи X и Y».
- ❌ Не менять signature `_add_link`.

---

### Фаза C — DoD

- [ ] Создан `src/vault/linking.py` с `add_typed_link()` и `render_links_section()`
- [ ] Создан `src/vault/frontmatter.py::get_inverse_field()` и `RELATION_INVERSE`
- [ ] При вызове `add_typed_link("50_Tasks/foo", "40_Projects/bar", "for_project")`:
  - в `50_Tasks/foo.md` frontmatter содержит `for_project: "[[40_Projects/bar]]"`
  - в `40_Projects/bar.md` frontmatter содержит `tasks: ["[[50_Tasks/foo]]"]`
  - в обеих заметках есть секция `## Связи` с этими ссылками
  - один git commit на обе

---

## ФАЗА D — LightRAG ontology ✅ DONE

> **Зачем:** LightRAG по умолчанию извлекает «Person/Organization/Location/Event». Это слишком обобщённо. Передаём ему наши доменные типы.

### D1. Дефолтные entity_types

**Файл:** `src/lightrag_svc/client.py` → функция `get_rag()`

**Изменить:**

```python
# Дефолтная онтология (используется если _meta/ontology.md не существует)
_DEFAULT_ENTITY_TYPES = [
    "Person", "Organization", "Project", "Task",
    "Concept", "Skill", "Tool", "Goal", "Theme", "Event",
]


async def get_rag() -> Any:
    global _rag
    if _rag is not None:
        return _rag

    from lightrag import LightRAG
    import os
    os.makedirs(settings.lightrag_storage, exist_ok=True)

    entity_types = await _load_entity_types()

    rag = LightRAG(
        working_dir=settings.lightrag_storage,
        llm_model_func=_llm_func_factory(),
        embedding_func=_embedding_func_factory(),
        chunk_token_size=1200,
        chunk_overlap_token_size=100,
        addon_params={"entity_types": entity_types},  # ⭐ НОВОЕ
    )
    _rag = rag
    logger.info("lightrag initialized",
                storage=settings.lightrag_storage,
                entity_types=entity_types)
    return _rag


async def _load_entity_types() -> list[str]:
    """Прочитать entity_types из _meta/ontology.md если есть, иначе дефолт."""
    from pathlib import Path
    ontology = Path(settings.vault_path) / "_meta" / "ontology.md"
    if not ontology.exists():
        return list(_DEFAULT_ENTITY_TYPES)

    raw = ontology.read_text(encoding="utf-8")
    # Простой формат: после маркера "## Types" — comma-separated список
    if "## Types" not in raw:
        return list(_DEFAULT_ENTITY_TYPES)

    types_block = raw.split("## Types", 1)[1].strip()
    types = [t.strip() for t in types_block.split(",") if t.strip()]
    return types or list(_DEFAULT_ENTITY_TYPES)
```

**Что НЕ делать:**
- ❌ Не передавать `entity_types` через env или config — только через `_meta/ontology.md` или дефолт.
- ❌ Не пытаться парсить YAML или сложные форматы. Тупо — `## Types\n A, B, C`.

---

### D2. Авто-генерация онтологии

**Создать новый файл** `src/lightrag_svc/ontology.py`:

```python
from __future__ import annotations
from pathlib import Path
import random

import structlog

from src.agent.loop import get_client
from src.config import settings
from src.vault import writer as vault_writer

logger = structlog.get_logger()


_PROMPT_TEMPLATE = """\
ACT AS: Senior Data Ontologist & Knowledge Graph Architect.
TASK: Analyze the provided sample of user's vault notes to extract a domain ontology.
GOAL: Define a concise list of high-level Entity Types covering the user's domain.

GUIDELINES:
- Abstraction: prefer broad categories ("Organization" not "Company"/"Startup").
- Relevance: include abstract concepts ("Concept", "Methodology", "Goal").
- Coverage: types should classify ≥90% of key nouns in the text.

RULES:
1. Output ONLY a comma-separated list of types. NO preamble, NO markdown.
2. Types: singular, PascalCase (Person, Project, ResearchPaper).
3. 8-15 types total.
4. Output MUST be in English.

SAMPLE:
{sample}

YOUR OUTPUT (comma-separated types only):
"""


async def generate_ontology(sample_size: int = 10) -> list[str]:
    """Сгенерировать entity_types из выборки заметок vault.

    Сохраняет результат в _meta/ontology.md и возвращает список.
    """
    vault = Path(settings.vault_path)
    all_md = [
        m for m in vault.rglob("*.md")
        if not str(m.relative_to(vault)).startswith(("_meta/", "10_Daily/", "00_Inbox/"))
    ]

    if len(all_md) < 5:
        logger.info("ontology gen skipped: too few notes", count=len(all_md))
        return []

    sample = random.sample(all_md, min(sample_size, len(all_md)))
    sample_text = ""
    for md in sample:
        rel = str(md.relative_to(vault))
        content = md.read_text(encoding="utf-8")[:1000]
        sample_text += f"--- {rel} ---\n{content}\n...\n\n"

    client = get_client()
    response = await client.chat.completions.create(
        model=settings.openai_model_main,
        messages=[
            {"role": "user", "content": _PROMPT_TEMPLATE.format(sample=sample_text)},
        ],
    )
    raw = (response.choices[0].message.content or "").strip()
    # Очистка: убрать возможные обрамления типа "Output:" или "[ ... ]"
    raw = raw.removeprefix("Output:").strip().strip("[]")
    types = [t.strip() for t in raw.split(",") if t.strip()]

    if not types:
        logger.warning("ontology gen returned empty")
        return []

    # Сохранить в _meta/ontology.md
    body = (
        "Авто-сгенерированная онтология для LightRAG. "
        "Не редактируй — будет перезаписано при следующем full_reindex.\n\n"
        f"## Types\n\n{', '.join(types)}\n"
    )
    await vault_writer.write_note(
        "_meta/ontology.md",
        body,
        {"type": "inbox", "auto_generated": True},
    )
    logger.info("ontology generated", types=types, count=len(types))
    return types
```

**Что НЕ делать:**
- ❌ Не вызывать `generate_ontology` синхронно при старте бота. Только из крона или ручного триггера.
- ❌ Не семплить из `_meta/`, `00_Inbox/`, `10_Daily/` — там не доменный контент.
- ❌ Не ставить sample_size > 20 — токены дороже точности на этом масштабе.

---

### D3. Триггер генерации онтологии

**Файл:** `src/scheduler/defaults.py` — изменить дефолт `lightrag_full_reindex`:

```python
# Заменить старую задачу полного реиндекса на «онтология + реиндекс»
("lightrag_ontology_and_reindex", "0 3 * * 6", "system",
    {"action": "regenerate_ontology_then_reindex"}),
```

**Файл:** `src/scheduler/triggers.py` — обработать новый action:

```python
async def proactive_trigger(task_id: str, kind: str, payload: dict) -> None:
    # ... существующая логика ...

    if kind == "system" and payload.get("action") == "regenerate_ontology_then_reindex":
        from src.lightrag_svc.ontology import generate_ontology
        from src.lightrag_svc.indexer import full_reindex

        try:
            await generate_ontology()
            # Сбросить singleton чтобы новый rag init с новой онтологией
            from src.lightrag_svc import client as lc
            lc._rag = None
            await full_reindex()
            logger.info("ontology + full reindex done")
        except Exception as exc:
            logger.error("ontology+reindex failed", error=str(exc))
        return
```

**Что НЕ делать:**
- ❌ Не запускать генерацию онтологии чаще раза в неделю.
- ❌ Не падать пайплайн если onтология сгенерилась но reindex упал — логировать и продолжать.
- ❌ Не ловить старый action `reindex_all` отдельно — заменить полностью.

---

### D4. Smart query mode

**Файл:** `src/tools/lightrag.py` — поменять mode по умолчанию

В существующих тулах `kg_query`, `kg_get_entity`, `kg_get_related` — везде где `mode="local"` или `mode="hybrid"` поменять на `mode="mix"` (новый комбинированный режим LightRAG).

**Что НЕ делать:**
- ❌ Не убирать параметр `mode` из API — пусть LLM может его явно указать. Только дефолт меняем.

---

### Фаза D — DoD

- [ ] LightRAG инициализируется с `addon_params={"entity_types": [...]}`
- [ ] `_meta/ontology.md` создаётся при первом еженедельном крон-запуске
- [ ] После генерации онтологии следующий kg_query возвращает сущности с типами из новой онтологии (видно в логах LightRAG)
- [ ] Дефолтный `mode="mix"` во всех LightRAG-тулах

---

## ФАЗА E — Maps of Content + визуальная гигиена ✅ DONE

> **Зачем:** дать графу естественные кластерные узлы и убрать визуальный шум.

### E1. Авто-MOC

**Создать новый файл** `src/vault/moc.py`:

```python
from __future__ import annotations
from pathlib import Path
from typing import Literal

import structlog

from src.config import settings
from src.vault import reader, writer
from src.vault.frontmatter import parse

logger = structlog.get_logger()


MOC_TYPES: dict[str, str] = {
    "person": "_meta/MOC_People.md",
    "project": "_meta/MOC_Projects.md",
    "job": "_meta/MOC_Jobs.md",
    "theme": "_meta/MOC_Themes.md",
}


async def regenerate_moc(note_type: str, session_id: str = "") -> str | None:
    """Перегенерировать MOC для указанного типа.

    Возвращает путь к MOC или None если тип не поддерживается.
    """
    moc_path = MOC_TYPES.get(note_type)
    if moc_path is None:
        return None

    folder = {
        "person": "20_People",
        "project": "40_Projects",
        "job": "30_Jobs",
        "theme": "80_Themes",
    }[note_type]

    vault = Path(settings.vault_path)
    folder_path = vault / folder
    if not folder_path.exists():
        return None

    active: list[tuple[str, str]] = []
    archived: list[tuple[str, str]] = []

    for md in sorted(folder_path.rglob("*.md")):
        rel = str(md.relative_to(vault))
        raw = md.read_text(encoding="utf-8")
        fm, body = parse(raw)
        title = md.stem.replace("-", " ")
        first_line = next((line for line in body.splitlines() if line.strip()), "")
        summary = first_line[:120]
        link = f"[[{rel.removesuffix('.md')}|{title}]]"
        line = f"- {link} — {summary}" if summary else f"- {link}"
        if fm.get("status") == "archived":
            archived.append((title, line))
        else:
            active.append((title, line))

    body = (
        f"Автоматически сгенерированная карта типа «{note_type}». "
        f"Не редактируй — будет перезаписано.\n\n"
        f"## Активные ({len(active)})\n\n"
        + "\n".join(line for _, line in active)
        + (f"\n\n## Архив ({len(archived)})\n\n"
           + "\n".join(line for _, line in archived) if archived else "")
    )

    await writer.write_note(
        moc_path,
        body,
        {"type": "inbox", "auto_generated": True, "moc_for": note_type},
        session_id,
    )
    return moc_path
```

**Что НЕ делать:**
- ❌ Не создавать MOC для типов task/thought/memory/daily/inbox — слишком много, мусор.
- ❌ Не редактировать существующее тело MOC аккуратно — перезаписывать целиком.
- ❌ Не запускать `regenerate_moc` после каждой записи — батчить.

---

### E2. Хук в session-end

**Файл:** `src/agent/extractor.py` → `apply_to_vault()`

**После smart linker** добавить:

```python
# ⭐ Перегенерировать MOC для типов которые трогали
try:
    from src.vault.moc import regenerate_moc, MOC_TYPES
    touched_types = set()
    for path in created:
        # path вида "40_Projects/foo.md"
        if "/" not in path:
            continue
        folder = path.split("/", 1)[0]
        type_map = {
            "20_People": "person",
            "30_Jobs": "job",
            "40_Projects": "project",
            "80_Themes": "theme",
        }
        if folder in type_map:
            touched_types.add(type_map[folder])

    for t in touched_types:
        await regenerate_moc(t, session_id)
    logger.info("moc regenerated", types=list(touched_types))
except Exception as exc:
    logger.warning("moc regen skipped", error=str(exc))
```

**Что НЕ делать:**
- ❌ Не перегенерировать все MOC всегда — только те типы которые реально менялись в сессии.
- ❌ Не падать пайплайн при ошибке MOC — это косметика.

---

### E3. Daily-note без визуального шума

**Файл:** `src/agent/extractor.py` → функция `_update_daily()`

**Заменить логику** «линкуем все созданные» на «линкуем только героев»:

```python
async def _update_daily(
    extraction: SessionExtraction,
    created_paths: list[str],
    session_id: str,
) -> str:
    tz = ZoneInfo(settings.tz)
    date_str = datetime.now(tz).strftime("%Y-%m-%d")
    rel_path = f"10_Daily/{date_str}.md"

    # ⭐ Героев определяем как сущности с наибольшим количеством new_facts
    heroes = sorted(
        [e for e in extraction.entities if e.new_facts],
        key=lambda e: -len(e.new_facts),
    )[:3]

    hero_links = []
    for hero in heroes:
        from src.vault.frontmatter import make_note_path
        hero_path = make_note_path(_ENTITY_TO_NOTE_TYPE.get(hero.type, "inbox"), hero.name)
        hero_links.append(f"- [[{hero_path.removesuffix('.md')}|{hero.name}]]")

    open_q = (
        "\n\n## Открытые вопросы\n\n" +
        "\n".join(f"- {q}" for q in extraction.open_questions)
        if extraction.open_questions else ""
    )

    block = f"### Сессия {session_id}\n\n{extraction.summary}{open_q}"
    if hero_links:
        block += "\n\n#### Главное за сессию\n\n" + "\n".join(hero_links)

    if reader.note_exists(rel_path):
        await writer.append_to_note(rel_path, block, session_id)
    else:
        # Полный список затронутых заметок — в frontmatter, не в теле (не рисуется в графе)
        fm: dict[str, Any] = {
            "type": "daily",
            "notes_touched": [f"[[{p.removesuffix('.md')}]]" for p in created_paths],
        }
        await writer.write_note(rel_path, block, fm, session_id)
    return rel_path
```

**Что НЕ делать:**
- ❌ Не оставлять старый код который пишет ВСЕ wikilinks в тело daily-note.
- ❌ Не передавать `notes_touched` через тело — только frontmatter, чтобы не рисовалось в графе.
- ❌ Если daily уже существует — не перетирать frontmatter.notes_touched. Логика: при новой сессии добавляем блок в тело, frontmatter не трогаем (он от первой сессии дня).

---

### E4. (опционально) Графовые фильтры в Obsidian

**Не код, а инструкция в README.md.**

Добавить раздел:

```markdown
## Рекомендуемые настройки Obsidian Graph

Для чистой картины графа в Obsidian:
- Settings → Graph view → Filters → search: `-path:_meta -path:90_Attachments -path:00_Inbox`
- Display → Existing files only: ON
- Display → Orphans: OFF
- Forces → Repel: 15
```

**Что НЕ делать:**
- ❌ Не пытаться программно конфигурировать Obsidian — это клиент пользователя, мы туда не лезем.

---

### Фаза E — DoD

- [ ] `_meta/MOC_People.md`, `MOC_Projects.md`, `MOC_Jobs.md`, `MOC_Themes.md` авто-обновляются после сессии
- [ ] Daily-note содержит ссылки только на 3 «героев» сессии в теле, остальное в `notes_touched: [...]` frontmatter
- [ ] В README раздел «Рекомендуемые настройки Obsidian Graph»

---

## ФАЗА F — MCP-мост к Claude Code / Cursor / Cline ⚠️ PARTIAL — есть баги, чинятся в G

> **Зачем:** граф знаний пользователя должен быть доступен не только из Telegram-бота, но и как контекст для AI-инструментов разработки. Claude Code, открывая код, должен иметь возможность спросить «что владелец думал про этот проект» и получить ответ из графа.
>
> **Что реально делаем:** запускаем `lightrag-server` как отдельный Docker-сервис рядом с ботом, оба процесса делят `/data/lightrag` через встроенный в LightRAG `shared_storage.py` (file locking, multi-process safe). Подключаемся к нему из Claude Code через MCP-мост `daniel-lightrag-mcp`.
>
> **Архитектурная картинка:**
>
> ```
> Telegram ──► bot (embedded LightRAG, READS+WRITES)
>                       ↕
>                 /data/lightrag (shared volume, file locking)
>                       ↕
>            lightrag-api (FastAPI server, READS — query only)
>                       ↑
>            HTTP :9621 (with API_KEY auth, localhost only)
>                       ↑
>     daniel-lightrag-mcp ◄── stdio ◄── Claude Code / Cursor / Cline
> ```

### F0. Требования к решению (заранее, чтобы не отступать)

- **Read-only НЕ enforced на уровне MCP-сервера.** (см. G7 — пункт переписан) Юзер сам отвечает за то, какому клиенту даёт доступ. Бот остаётся каноническим writer'ом для vault'а — то что MCP-клиент пишет в граф, не синхронизируется обратно в Obsidian заметки.
- **Auth через API key.** Без ключа — 401. Ключ генерируется при первом старте и сохраняется в `secrets/lightrag_api_key.txt`.
- **Bind на localhost:9621.** Не 0.0.0.0. Если юзер хочет remote — пусть сам тоннелит через tailscale/ssh.
- **Бот продолжает работать в embedded-режиме.** Не рефакторим `lightrag_svc/client.py` под HTTP-вызовы. Слишком рискованно ломать рабочий пайплайн.
- **Concurrent access — best-effort, не транзакционный.** (см. G7 — пункт переписан) Бот — единственный writer, lightrag-api — read-only по факту использования. `initialize_share_data` вызывается в каждом процессе для активации внутренних file-locks LightRAG, но `multiprocessing.Manager()` НЕ шарится между контейнерами (он создаётся локально в каждом). Атомарность держится на: (1) бот пишет через `os.replace`, (2) lightrag-api при чтении переоткрывает файлы. В редких случаях api-сервер может вернуть устаревший снапшот — eventual consistency приемлема для personal-use графа.

---

### F1. Конфиг multi-worker shared storage в embedded клиенте

**Файл:** `src/lightrag_svc/client.py` → функция `get_rag()`

**Изменить:** перед созданием `LightRAG(...)` вызвать `initialize_share_data()`.

```python
async def get_rag() -> Any:
    global _rag
    if _rag is not None:
        return _rag

    from lightrag import LightRAG  # type: ignore[import]
    from lightrag.kg.shared_storage import initialize_share_data  # type: ignore[import]
    import os
    os.makedirs(settings.lightrag_storage, exist_ok=True)

    # ⭐ НОВОЕ — включаем multi-process locking перед инициализацией
    # workers=2: bot (writer) + lightrag-api (reader). Это активирует
    # multiprocessing.Lock внутри LightRAG.shared_storage.
    initialize_share_data(workers=2)

    entity_types = await _load_entity_types()
    rag = LightRAG(
        working_dir=settings.lightrag_storage,
        llm_model_func=_llm_func_factory(),
        embedding_func=_embedding_func_factory(),
        chunk_token_size=1200,
        chunk_overlap_token_size=100,
        addon_params={"entity_types": entity_types},
    )
    await rag.initialize_storages()  # ⭐ НОВОЕ — обязательно после initialize_share_data
    _rag = rag
    logger.info("lightrag initialized",
                storage=settings.lightrag_storage,
                entity_types=entity_types,
                multiprocess=True)
    return _rag
```

**Что НЕ делать:**
- ❌ Не вызывать `initialize_share_data` дважды — он идемпотентен но логирует warning.
- ❌ Не ставить `workers=1` — это отключит locking, не безопасно с lightrag-api сервисом.
- ❌ Не убирать `await rag.initialize_storages()` — без него shared structures не подцепятся.
- ❌ Не вызывать `initialize_share_data` ВНЕ функции `get_rag()` (например в `main.py` при старте) — нужно только перед первым обращением к графу.

**DoD F1:** бот стартует, лог `lightrag initialized ... multiprocess=True` появляется один раз. После записи документа — файл `/data/lightrag/graph_chunk_entity_relation.graphml` обновлён.

---

### F2. Скрипт инициализации API key

**Создать новый файл:** `scripts/init_lightrag_api_key.sh`

```bash
#!/usr/bin/env bash
# Generates a random API key for lightrag-api and stores it in secrets/.
# Idempotent: skips if key already exists.

set -euo pipefail

KEY_FILE="secrets/lightrag_api_key.txt"

mkdir -p secrets

if [ -f "$KEY_FILE" ]; then
    echo "API key already exists at $KEY_FILE"
    exit 0
fi

# 32-byte URL-safe base64 random key
python3 -c "import secrets; print(secrets.token_urlsafe(32))" > "$KEY_FILE"
chmod 600 "$KEY_FILE"
echo "Generated new API key at $KEY_FILE"
```

**Сделать исполняемым:** `chmod +x scripts/init_lightrag_api_key.sh`

**Что НЕ делать:**
- ❌ Не использовать `openssl rand` или `uuidgen` — Python `secrets.token_urlsafe(32)` стандартизированный и есть в любой Python 3 установке.
- ❌ Не хардкодить ключ в `.env.example` — генерируется уникально для каждой инсталляции.
- ❌ Не коммитить сам файл `secrets/lightrag_api_key.txt` — он уже в `.gitignore` (через `secrets/`).

**DoD F2:** запуск `./scripts/init_lightrag_api_key.sh` создаёт `secrets/lightrag_api_key.txt` с правами `600`. Повторный запуск — no-op.

---

### F3. Docker-сервис `lightrag-api`

**Файл:** `docker-compose.yml`

**Добавить новый сервис** (после `bot`, до `redis`):

```yaml
  lightrag-api:
    image: ghcr.io/hkuds/lightrag:latest
    command:
      - lightrag-server
      - --host
      - 0.0.0.0
      - --port
      - "9621"
      - --working-dir
      - /data/lightrag
      - --workers
      - "2"
    env_file: .env
    environment:
      LIGHTRAG_API_KEY_FILE: /run/secrets/lightrag_api_key
      WORKERS: "2"
      MAX_PARALLEL_INSERT: "1"   # writes идут только из бота, сервер не пишет
    volumes:
      - ./data/lightrag:/data/lightrag
      - ./secrets/lightrag_api_key.txt:/run/secrets/lightrag_api_key:ro
    ports:
      - "127.0.0.1:9621:9621"   # только localhost, не наружу
    depends_on:
      - bot
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://localhost:9621/health"]
      interval: 30s
      timeout: 5s
      retries: 3
```

**Если официального образа `ghcr.io/hkuds/lightrag` нет** или он несовместим — заменить на свой `Dockerfile.lightrag`:

```dockerfile
FROM python:3.12-slim
RUN pip install --no-cache-dir 'lightrag-hku[api]>=1.3'
WORKDIR /data
ENTRYPOINT ["lightrag-server"]
```

И в `docker-compose.yml`:
```yaml
  lightrag-api:
    build:
      context: .
      dockerfile: Dockerfile.lightrag
    # остальное как выше
```

**Что НЕ делать:**
- ❌ Не биндить `9621` на `0.0.0.0` — только `127.0.0.1:9621:9621`. Иначе любой в сети получит доступ к графу.
- ❌ Не убирать `depends_on: bot` — бот должен подняться первым (он инициализирует storage через `initialize_share_data`).
- ❌ Не делать `volumes: - ./data/lightrag:/data/lightrag:ro` — lightrag-server при старте может писать кеш-файлы. Read-only mount ломает запуск.
- ❌ Не использовать `latest` тег без проверки — закрепить версию когда заработает (`ghcr.io/hkuds/lightrag:1.4.0` или подобное).
- ❌ Не запускать `lightrag-gunicorn` для нашего кейса — gunicorn нужен для тяжёлой production load. Для personal use достаточно uvicorn-mode (`lightrag-server`).

**DoD F3:** `docker compose up -d` поднимает оба сервиса. `curl -H "X-API-Key: $(cat secrets/lightrag_api_key.txt)" http://localhost:9621/health` возвращает 200. Без заголовка — 401.

---

### F4. Quickstart-скрипт setup.sh (обёртка для всех init-шагов)

**Создать новый файл:** `setup.sh`

```bash
#!/usr/bin/env bash
# One-command setup: creates secrets dir, generates SSH key (optional),
# generates LightRAG API key, validates .env. Idempotent.

set -euo pipefail

if [ ! -f .env ]; then
    if [ ! -f .env.example ]; then
        echo "ERROR: .env.example not found. Are you in the project root?"
        exit 1
    fi
    cp .env.example .env
    echo "Created .env — edit it before running 'docker compose up -d'."
fi

mkdir -p secrets data/vault data/lightrag data/redis

./scripts/init_lightrag_api_key.sh

if [ ! -f secrets/vault_ssh_key ]; then
    echo ""
    echo "No vault SSH key found. Generate one with:"
    echo "  ssh-keygen -t ed25519 -C 'mnemo-vault' -f secrets/vault_ssh_key -N ''"
    echo "Then add secrets/vault_ssh_key.pub to your GitHub repo as a deploy key."
    echo "(Skip this if you don't want git-sync of vault.)"
fi

echo ""
echo "Setup complete. Next steps:"
echo "  1. Edit .env (set TELEGRAM_BOT_TOKEN, OPENAI_API_KEY, ALLOWED_USER_IDS)"
echo "  2. docker compose up -d"
echo "  3. docker compose logs -f bot"
```

**Сделать исполняемым:** `chmod +x setup.sh`

**Что НЕ делать:**
- ❌ Не делать ssh-keygen автоматически. Юзер сам решает нужен ли git-sync.
- ❌ Не делать `docker compose up` из этого скрипта. Юзер делает это явно после редактирования `.env`.
- ❌ Не запускать setup.sh из CI — он интерактивный (выводит инструкции).

**DoD F4:** на чистом склонированном репо `./setup.sh` создаёт нужные папки и API-ключ. Запуск повторно — no-op (никаких ошибок).

---

### F5. README — раздел «Connect to Claude Code / Cursor / Cline»

**Файл:** `README.md`

**Добавить раздел** после `## Recommended Obsidian Graph Settings` и до `## Bot Commands`:

```markdown
## Connect Your Brain to AI Coding Tools

Mnemo exposes its knowledge graph as an MCP server. This means **Claude Code, Cursor, Cline, Windsurf, and any MCP-compatible tool** can query your second brain while helping you code.

Want Claude Code to know what you've been thinking about a project? It will.

### 1. Make sure `lightrag-api` is running

```bash
docker compose ps
# bot           Up
# lightrag-api  Up    127.0.0.1:9621->9621/tcp
# redis         Up
```

### 2. Install the MCP bridge on your local machine (NOT in Docker)

```bash
pip install daniel-lightrag-mcp
# or with pipx for isolation:
pipx install daniel-lightrag-mcp
```

### 3. Configure your tool

#### Claude Code

Edit `~/.claude/claude_mcp_config.json` (or use `claude mcp add`):

```json
{
  "mcpServers": {
    "mnemo-brain": {
      "command": "daniel-lightrag-mcp",
      "env": {
        "LIGHTRAG_API_URL": "http://localhost:9621",
        "LIGHTRAG_API_KEY": "<paste contents of secrets/lightrag_api_key.txt>"
      }
    }
  }
}
```

Restart Claude Code. Try asking: "What does my brain know about my LegAI project?"

#### Cursor

`Settings → MCP → Add new MCP server`. Same JSON shape.

#### Cline / Windsurf

Similar — see their docs for `mcpServers` config location.

### 4. (Optional) Remote access

If you want to query your brain from a different machine, **don't expose port 9621 directly**. Use one of:

- **Tailscale**: `tailscale serve 127.0.0.1:9621` — gets you a HTTPS endpoint with auth
- **SSH tunnel**: `ssh -L 9621:localhost:9621 you@your-server`

### Available tools (read-only)

The MCP bridge gives Claude Code these tools — all read-only, your bot is the only writer:

- `query` — semantic + graph search ("What did I plan for next quarter?")
- `query_graph` — entity relationships ("Who is connected to LegAI?")
- `get_documents` — list indexed sources
- `get_graph_labels` — entity type taxonomy

It cannot insert, delete, or modify anything. All edits flow through the Telegram bot.
```

**Что НЕ делать:**
- ❌ Не описывать установку всех MCP-клиентов детально — линкуй их docs. Документация устаревает.
- ❌ Не предлагать `0.0.0.0` биндинг как «простое решение». Прямо говори про tailscale/ssh.
- ❌ Не публиковать примеры с реальными API-ключами в README.

---

### F6. Перевод раздела на 4 других языка

**Файлы:** `docs/README_zh.md`, `docs/README_es.md`, `docs/README_pt.md`, `docs/README_fr.md`

В каждом — короткий раздел (10-15 строк):

- Заголовок «Connect to AI Coding Tools» переведённый
- Одно предложение что это даёт
- Команды pip install + JSON-конфиг (код не переводится)
- Ссылка на главный README за полной инструкцией: «See [English README](../README.md#connect-your-brain-to-ai-coding-tools) for full setup guide.»

**Что НЕ делать:**
- ❌ Не дублировать всю английскую секцию в каждый перевод. Поддерживать 5 версий — головняк. Достаточно ссылки.
- ❌ Не переводить названия инструментов (Claude Code, Cursor, Cline остаются английскими).

---

### F7. Проверочный тест: интеграция работает end-to-end

**Не unit-тест, а ручной чеклист** (записать в `docs/MCP_TESTING.md`):

```markdown
# MCP Integration — Manual Test Checklist

## Setup
1. `./setup.sh` → creates secrets and api key
2. Fill `.env` with bot token + OpenAI key
3. `docker compose up -d` → 3 services up
4. Send a few messages to the bot to populate the graph

## API server health
- [ ] `curl http://localhost:9621/health` without key → 401
- [ ] `curl -H "X-API-Key: $(cat secrets/lightrag_api_key.txt)" http://localhost:9621/health` → 200
- [ ] `curl -H "X-API-Key: ..." http://localhost:9621/graphs/labels` → returns entity types from `_meta/ontology.md`

## MCP client
- [ ] `pip install daniel-lightrag-mcp` succeeds
- [ ] `daniel-lightrag-mcp --help` runs
- [ ] Claude Code config added → restart → MCP shows green status
- [ ] In Claude Code: ask "what is in my brain about <topic you mentioned to bot>" → response references actual entities from graph
- [ ] In Claude Code: try to insert a document via tool → tool not exposed (or returns 403 if exposed)

## Concurrent access safety
- [ ] Send a message to bot, immediately query via Claude Code → no errors, eventually-consistent results
- [ ] Bot logs no `pickle.UnpicklingError` or JSON decode errors during this
- [ ] After 5 min, Claude Code query returns the new entities
```

**DoD F7:** все чекбоксы пройдены на dev-машине. Скриншот / лог приложен к PR.

---

### Фаза F — общий DoD

- [x] `initialize_share_data(workers=2)` вызван в `get_rag()` ✅ (но claim про cross-container locking ложный, см. G7)
- [x] `secrets/lightrag_api_key.txt` создаётся скриптом, в `.gitignore` ✅
- [x] `lightrag-api` сервис в `docker-compose.yml`, биндинг только на 127.0.0.1 ✅ (но auth не работает, чинится в G1)
- [x] `setup.sh` идемпотентный, разворачивает с нуля ✅
- [x] README раздел «Connect to AI Coding Tools» с примерами для Claude Code/Cursor ✅ (но pip install + env var неверны, чинятся в G2/G3)
- [x] Переводы README обновлены (короткая секция + ссылка) ✅ (те же баги, чинятся в G2/G3)
- [ ] `docs/MCP_TESTING.md` — чеклист пройден end-to-end ❌ (чеклист с несуществующими endpoints, чинится в G4)
- [ ] Бот продолжает писать в граф; Claude Code продолжает его читать; никаких corrupted reads в логах ❌ (требует G1 для рабочей auth)

---

## 4. Порядок реализации

| Фаза | Зависимости | Время | Статус | Эффект |
|------|-------------|-------|--------|--------|
| A | — | 1 день | ✅ DONE | дубликаты устранены + якорь-владелец |
| B | A | 1 день | ✅ DONE | граф сшит smart linker'ом |
| C | A | 0.5 дня | ✅ DONE | Obsidian-graph рисует то что хранится |
| D | — | 0.5 дня | ✅ DONE | LightRAG mode=mix + ontology |
| E | A, B | 0.5 дня | ✅ DONE | MOC + чистая daily-note |
| F | D | 0.5 дня | ⚠️ PARTIAL | код есть, **5 багов** — закрываются в G |
| G | F | 0.5 дня | ❌ NOT STARTED | хотфиксы Phase F (auth, install, docs) |
| H | F1, G | 1 день | ❌ NOT STARTED | unified graph, −2 LLM-вызова на заметку |

**Правильный порядок исполнения:**

`A → B → C → D → E → F → **G → H**`

A–F закрыты (с багами в F которые чинит G). Делать сейчас:

1. **G** — обязательно перед любым продакшен-деплоем. Без G1 порт 9621 открыт, без G2 юзер не установит MCP-bridge, без G6 контейнер `lightrag-api` крашнется.
2. **H** — обязательно перед открытием графа в Claude Code. Без H ты экспонируешь LLM-extracted граф (нечёткий, не наш), а не свой типизированный.

**G и H можно делать частично параллельно**:
- G1, G6, G8 — нужны для запуска вообще (auth, env, healthcheck)
- G2, G3, G4, G5, G7, G9 — документация и тесты, **не блокируют** старт H
- H1–H8 могут стартовать как только G1 + G6 + G8 закрыты

**Рекомендованный sprint-план:**

| День | Что делать |
|------|-----------|
| 1 (утро) | G1 (auth via entrypoint) + G6 (.env.example) + G8 (bot healthcheck) — система запускается |
| 1 (день) | G5 (тесты) + G2/G3/G4 (доки честные) |
| 1 (вечер) | G7 (убрать ложные claim'ы) + G9 (sanity check) |
| 2 (утро) | H1 (converter) + H7 (тесты converter) |
| 2 (день) | H2 (заменить ainsert на ainsert_custom_kg) + H3 (FORWARD_RELATIONS) |
| 2 (вечер) | H4 (rebind на add_typed_link) + H5 (комментарий) + H6 (удалить ontology) + H8 (smoke test) |
| 3 | H9 (rename hook — следить за переименованиями) |

**Не делай G и H одним PR.** G — bugfix, должен попасть отдельно с понятным «закрыли все QA-замечания». H — feature, отдельный PR с замером эффекта (стоимость до/после, качество запросов).

---

## 4.1. Прогресс по DoD (snapshot 2026-05-08)

### ✅ Завершено

**Phase A — Owner anchor + Dedup:**
- ✅ A1: `_meta/owner.md` создаётся в `_create_owner_note()` ([src/telegram/handlers/text.py:56](src/telegram/handlers/text.py#L56)), `step_owner_name` есть в onboarding
- ✅ A2: [src/vault/dedup.py](src/vault/dedup.py) с `find_similar()` через rapidfuzz
- ✅ A3: дедуп в `_create_note` ([src/tools/obsidian.py:112](src/tools/obsidian.py#L112)), threshold 70/85/90
- ✅ A4: `aliases` в `EntityInfo` ([src/agent/extractor.py:23](src/agent/extractor.py#L23)), правило в `prompts/session_extract.md`

**Phase B — Smart linker:**
- ✅ B1: [src/agent/linker.py](src/agent/linker.py) с `propose_links()` через structured outputs
- ✅ B2: `apply_links()` в том же файле
- ✅ B3: хук в `apply_to_vault()` ([src/agent/extractor.py:198](src/agent/extractor.py#L198))
- ✅ B4: правила линковки в `prompts/session_extract.md`

**Phase C — Frontmatter-driven граф:**
- ✅ C1: `RELATION_INVERSE`, `LINK_FIELDS`, `get_inverse_field()` ([src/vault/frontmatter.py:14](src/vault/frontmatter.py#L14))
- ✅ C2: [src/vault/linking.py](src/vault/linking.py) с `add_typed_link()` и `render_links_section()`
- ✅ C3: старый `_add_link` оставлен для агента/юзер-команд

**Phase D — LightRAG ontology:**
- ✅ D1: `_DEFAULT_ENTITY_TYPES` + `addon_params` ([src/lightrag_svc/client.py:14](src/lightrag_svc/client.py#L14))
- ✅ D2: [src/lightrag_svc/ontology.py](src/lightrag_svc/ontology.py) (но в Phase H6 решается удалить)
- ✅ D3: scheduled task `lightrag_ontology_and_reindex` + handler в `_handle_system_task`
- ✅ D4: `mode="mix"` дефолтом во всех 3 lightrag-тулах ([src/tools/lightrag.py](src/tools/lightrag.py))

**Phase E — MOC + daily hygiene:**
- ✅ E1: [src/vault/moc.py](src/vault/moc.py) с `regenerate_moc()`
- ✅ E2: хук в `apply_to_vault` ([src/agent/extractor.py:213](src/agent/extractor.py#L213))
- ✅ E3: heroes-only в `_update_daily()`, `notes_touched` в frontmatter
- ✅ E4: README раздел `## Recommended Obsidian Graph Settings`

**Phase F — MCP-мост (с QA-замечаниями):**
- ✅ F1: `initialize_share_data(workers=2)` + `await rag.initialize_storages()` ([src/lightrag_svc/client.py](src/lightrag_svc/client.py)) — *но multi-process claim переоценён, см. G7*
- ✅ F2: [scripts/init_lightrag_api_key.sh](scripts/init_lightrag_api_key.sh)
- ⚠️ F3: [docker-compose.yml](docker-compose.yml) с `lightrag-api` сервисом + [Dockerfile.lightrag](Dockerfile.lightrag) — **auth не работает, чинится в G1**
- ✅ F4: [setup.sh](setup.sh)
- ⚠️ F5: README раздел «Connect Your Brain to AI Coding Tools» — **врёт про read-only и pip install, чинится в G2/G3/G7**
- ⚠️ F6: 4 переведённых README с короткой секцией — **те же баги что F5, чинятся в G2/G3**
- ⚠️ F7: [docs/MCP_TESTING.md](docs/MCP_TESTING.md) — **чеклист с несуществующими endpoints, чинится в G4**

### ❌ Не сделано

**Phase G** (всё): G1, G2, G3, G4, G5, G6, G7, G8, G9 — 9 пунктов.

**Phase H** (всё): H1, H2, H3, H4, H5, H6, H7, H8 — 8 пунктов + новый H9 (см. ниже).

### 🐛 Известные баги до Phase G

| Файл | Что не так | Фикс |
|------|-----------|------|
| `docker-compose.yml::lightrag-api.environment` | `LIGHTRAG_API_KEY_FILE` не существует как env | G1 |
| `README.md` + 4 перевода | `pip install daniel-lightrag-mcp` → 404 | G2 |
| `README.md` + 4 перевода | `LIGHTRAG_API_URL` вместо `LIGHTRAG_BASE_URL` | G3 |
| `docs/MCP_TESTING.md` | endpoint `/graphs/labels` не существует, тест на 401 для `/health` неверен | G4 |
| `tests/test_dedup.py::test_find_similar_cyrillic_transliteration` | `WRatio` не делает транслит, тест на ложном предположении | G5.1 |
| `tests/test_linker.py::test_propose_links_returns_mock_result` | оба файла в `touched_paths` → ранний return | G5.2 |
| `.env.example` | нет `LLM_BINDING`, `EMBEDDING_*` | G6 |
| `docker-compose.yml::bot` | нет healthcheck, `condition: service_healthy` не сработает | G8 |
| `IMPLEMENTATION_PLAN.md §F0` + `README.md` | claim'ы про read-only и multi-process safe — ложные | G7 |

### 📊 Архитектурный долг до Phase H

| Проблема | Эффект | Фикс |
|----------|--------|------|
| LightRAG делает свой LLM-extraction поверх наших markdown'ов | 2 LLM-вызова на каждую заметку (~$0.01) | H1+H2 |
| Граф LightRAG ≠ граф Obsidian (typed wikilinks игнорируются) | `kg_query` отвечает по нечёткому графу, не нашему | H1+H2 |
| YAML frontmatter попадает в эмбеддинги как шум | хуже семантический поиск | H1 (`_strip_frontmatter_for_chunk`) |
| `add_typed_link` меняет frontmatter, но граф LightRAG не обновляется | устаревшие данные в `kg_query` | H4 |
| MOC и ontology файлы попадают в индекс при `full_reindex` | дубль ссылок, шум | H2 (`full_reindex` фильтрует `_meta/MOC_*`) |
| Переименование заметки оставляет orphan-entity в графе | при `move_note(old, new)` старая нода висит навсегда | H9 (новый пункт) |

---

## 5. Анти-паттерны (не делать ни при каких условиях)

- ❌ **Не вводить новые типы заметок** (только 9 существующих). Если кажется что нужно — спроси владельца.
- ❌ **Не править существующие тулы** `_create_note`, `_add_link` ломая их API. Только дополнять поведением (как в A3).
- ❌ **Не менять формат frontmatter существующих заметок миграцией.** Новые поля — опциональны, парсер уже прощает их отсутствие.
- ❌ **Не делать одновременно две фазы.** Сначала закрыть A, потом начинать B.
- ❌ **Не оставлять закомментированный старый код.** Удаляй полностью.
- ❌ **Не добавлять `try/except Exception: pass`.** Только конкретные исключения с логированием.
- ❌ **Не вызывать LLM в цикле для каждой пары заметок.** Только batch-вызовы.
- ❌ **Не делать MOC из всех типов** — только person/project/job/theme.
- ❌ **Не падать пайплайн при ошибке косметической задачи** (linker, MOC, ontology). Логировать и продолжать.
- ❌ **Не использовать subprocess.run('git', ...) вне `vault/git_ops.py`.**
- ❌ **Не обходить `vault/writer.py`.** Любая запись в файл vault — только через writer.
- ❌ **Не редактировать `_meta/ontology.md`, `MOC_*.md` руками** в коде — только через `vault_writer.write_note`.
- ❌ **Не хранить путь owner.md в коде** — только через `profile["owner_path"]`.
- ❌ **Не делать функции > 60 строк.** Если получается — разбей.
- ❌ **Не реализовывать функцию которая не описана в этом плане.** Если кажется что нужна — добавь в §6 ниже и спроси владельца.

### Анти-паттерны конкретно для Фазы F

- ❌ **Не рефакторить `lightrag_svc/client.py` под HTTP-вызовы.** Бот остаётся в embedded-режиме. Сервер — отдельный процесс параллельно.
- ❌ **Не проксировать запросы бота через lightrag-api.** Бот пишет напрямую в файлы через `ainsert()` — иначе теряем смысл (двойная сериализация, двойные сетевые хопы).
- ❌ **Не биндить порт 9621 на 0.0.0.0.** Только `127.0.0.1:9621:9621` в docker-compose. Если юзер хочет remote — он сам делает tailscale/ssh-tunnel.
- ❌ **Не передавать API key через query string** (`?api_key=...`). Только через заголовок `X-API-Key` или `Authorization: Bearer`.
- ❌ **Не запускать lightrag-server без `initialize_share_data` в боте.** Если бот стартанул без shared storage — поднявшийся параллельно сервер увидит несинхронизированные структуры → corrupted reads.
- ❌ **Не вкатывать в наш проект кастомный MCP-сервер.** Используй community `daniel-lightrag-mcp`. Если он не покрывает кейс — открой issue в их репо, не пиши свой.
- ❌ **Не давать MCP-серверу право на запись в граф.** В клиентском конфиге не должно быть `--allow-writes` или подобного. Только query.
- ❌ **Не коммитить `secrets/lightrag_api_key.txt` ни под каким видом.** Проверь `.gitignore` перед каждым `git add .`.
- ❌ **Не упоминать Mnemo как multi-user систему в README.** MCP-доступ — это всё ещё personal tool, просто расшаренный для своего же Claude Code.

---

## 6. Открытые вопросы (для владельца)

*(Этот раздел заполняется по ходу реализации, если что-то неочевидно. Sonnet — добавляй сюда вопрос ДО написания кода, не после.)*

- [ ] Q: При дедупликации score 70-85 агент возвращается с кандидатами и должен сам решать. Если LLM зациклится — что делать? *(текущая идея: ограничить max_rounds в run_chat → 30, существующее ограничение)*
- [ ] Q: При генерации онтологии — какой минимум заметок чтобы триггерить? Сейчас 5. Достаточно?
- [ ] Q: MOC регенерируются после каждой сессии. Если сессий много за день — много коммитов в MOC. Дебаунсить?
- [ ] Q (Фаза F): Если `ghcr.io/hkuds/lightrag` образа не существует на момент имплементации — собирать свой `Dockerfile.lightrag` (вариант указан в F3) или ждать официальный?
- [ ] Q (Фаза F): При первом старте бота `initialize_share_data` блокирует event loop на ~1 сек (форк процессов). Допустимо или вынести в startup hook с прогресс-логом?
- [ ] Q (Фаза F): Хочешь ли в будущем расширить MCP-доступ на vault tools (search_notes, read_note, list_notes)? Сейчас экспонируется только LightRAG. Если да — это **отдельная Phase G**, со своим custom MCP-сервером в нашем репо.

---

## 7. Тесты (минимально необходимые)

Добавить в `tests/`:

### `test_dedup.py`
- Создать в `40_Projects/` две заметки: `legai.md` (aliases: ["LegAI"]) и `echelon.md` (aliases: []).
- `find_similar("project", "LegAI")` → возвращает `legai.md` со score ≥ 90.
- `find_similar("project", "ЛегАИ")` → возвращает `legai.md` со score ≥ 70 (транслит).
- `find_similar("project", "новый-проект")` → пустой список.
- `find_similar("task", "legai")` → пустой список (другой тип).

### `test_typed_links.py`
- Создать `50_Tasks/foo.md` (type: task) и `40_Projects/bar.md` (type: project).
- `add_typed_link("50_Tasks/foo.md", "40_Projects/bar.md", "for_project", "test")` → True.
- В `foo.md` frontmatter `for_project == "[[40_Projects/bar]]"`.
- В `bar.md` frontmatter `tasks == ["[[50_Tasks/foo]]"]`.
- В обеих секция `## Связи`.
- Один git commit с сообщением `link 50_Tasks/foo.md -[for_project]-> 40_Projects/bar.md [session=test]`.
- Повторный вызов → False (уже есть).

### `test_linker.py` (требует моки OpenAI)
- Замокать `client.beta.chat.completions.parse` возвращать `LinkBatch(links=[LinkProposal(...)])`.
- `propose_links([...], "_meta/owner.md")` → возвращает мок-результат.
- `apply_links(...)` применяет связи.

### `test_moc.py`
- Создать 3 person заметки в `20_People/` (одна с `status: archived`).
- `regenerate_moc("person")` → `_meta/MOC_People.md` существует, в активных 2, в архиве 1.

---

## ФАЗА G — Hot-fixes после Phase F audit ❌ NOT STARTED — делать первым

> **Контекст:** Phase F был написан и реализован Sonnet'ом, после чего senior QA нашёл 11 проблем, из них 5 — критичные (юзер не запустит систему as-is). Эта фаза закрывает все.
>
> **Прочитай §G0 целиком ДО начала любого пункта.** Без этого ты повторишь те же ошибки.
>
> **Не браться за G2, G3, G4 пока не сделан G1.** G1 — это фундамент: без рабочей авторизации остальное бессмысленно.

### G0. Что было сломано в Phase F (читать обязательно)

1. **Пакет `daniel-lightrag-mcp` не существует на PyPI** (404). Все 5 README инструктируют `pip install daniel-lightrag-mcp` — у юзера упадёт. На GitHub `desimpkins/daniel-lightrag-mcp` репо есть, но ставится из исходников.
2. **`LIGHTRAG_API_KEY_FILE` env var lightrag-server не читает.** В docker-compose стоит `LIGHTRAG_API_KEY_FILE: ...` — это игнорируется. Нужен либо `LIGHTRAG_API_KEY` (env), либо CLI флаг `--key`. Сейчас порт 9621 **открыт без авторизации** (несмотря на 127.0.0.1 binding).
3. **MCP-клиент читает `LIGHTRAG_BASE_URL`, не `LIGHTRAG_API_URL`.** В README везде неправильное имя.
4. **Endpoint `/graphs/labels` в `MCP_TESTING.md` не существует.** Реальные пути: `/graph/label/list`, `/graph/label/popular`, `/graphs`.
5. **Тест в `MCP_TESTING.md` «`/health` без ключа → 401» неверен.** `/health` входит в `WHITELIST_PATHS=/health,/api/*`. Ответ всегда 200.
6. **Тест `test_find_similar_cyrillic_transliteration` падает.** `rapidfuzz.WRatio("ЛегАИ","legai") < 70` — никакой транслитерации `WRatio` не делает.
7. **Тест `test_propose_links_returns_mock_result` падает.** В фикстуре только два файла, оба в `touched_paths` → `_build_candidates_context()` возвращает `""` → ранний `return []` без вызова мока.
8. **`.env.example` не содержит `LLM_BINDING`, `LLM_BINDING_HOST`, `LLM_MODEL`, `EMBEDDING_BINDING`, `EMBEDDING_MODEL`, `EMBEDDING_DIM`.** `lightrag-server` без них упадёт при первом запросе.
9. **План в F0 утверждает «multi-process safe shared storage between bot и lightrag-api».** Это **неправда**: `initialize_share_data` использует `multiprocessing.Manager()`, который шарится только в пределах одного fork-tree (uvicorn master → workers). Два разных контейнера = два разных Manager'а, никакого общего lock'а нет. Атомарность файлов графа держится только на том что бот единственный writer.
10. **План в F0 утверждает «MCP read-only».** Это **неправда**: `daniel-lightrag-mcp` экспонирует 22 тула, из них 6 write (`insert_text`, `upload_document`, `delete_document`, и т.д.). В README написано «все edits через бота» — ложная гарантия.
11. **`depends_on: bot` без healthcheck.** `bot` не имеет healthcheck → `lightrag-api` стартует как только bot контейнер «started», не «ready». Race не критичный (lazy init), но плановое утверждение «бот должен подняться первым» не гарантировано.

---

### G1. Auth — пробросить API key в lightrag-server через CLI флаг

**Зачем:** без этого порт 9621 открыт. Любой локальный процесс делает запросы к графу без ключа.

**Решение:** не используем `LIGHTRAG_API_KEY_FILE` (его нет). Вместо этого entrypoint-скрипт читает файл и передаёт значение в `lightrag-server --key`.

**Шаги:**

1. Создать `scripts/lightrag_entrypoint.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

KEY_FILE="${LIGHTRAG_API_KEY_FILE:-/run/secrets/lightrag_api_key}"

if [ ! -f "$KEY_FILE" ]; then
    echo "ERROR: API key file not found at $KEY_FILE" >&2
    echo "Run ./scripts/init_lightrag_api_key.sh on the host first." >&2
    exit 1
fi

API_KEY="$(tr -d '[:space:]' < "$KEY_FILE")"

if [ -z "$API_KEY" ]; then
    echo "ERROR: API key file is empty: $KEY_FILE" >&2
    exit 1
fi

exec lightrag-server \
    --host 0.0.0.0 \
    --port 9621 \
    --working-dir /data/lightrag \
    --key "$API_KEY" \
    "$@"
```

`chmod +x scripts/lightrag_entrypoint.sh`.

2. Заменить `Dockerfile.lightrag` целиком:

```dockerfile
FROM python:3.12-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir 'lightrag-hku[api]>=1.4'

WORKDIR /data

COPY scripts/lightrag_entrypoint.sh /usr/local/bin/lightrag_entrypoint.sh
RUN chmod +x /usr/local/bin/lightrag_entrypoint.sh

EXPOSE 9621

ENTRYPOINT ["/usr/local/bin/lightrag_entrypoint.sh"]
```

3. В `docker-compose.yml` сервис `lightrag-api` — убрать `command:` целиком (entrypoint всё знает), убрать `LIGHTRAG_API_KEY_FILE` из `environment` (entrypoint читает напрямую):

```yaml
  lightrag-api:
    build:
      context: .
      dockerfile: Dockerfile.lightrag
    env_file: .env
    environment:
      MAX_PARALLEL_INSERT: "1"
    volumes:
      - ./data/lightrag:/data/lightrag
      - ./secrets/lightrag_api_key.txt:/run/secrets/lightrag_api_key:ro
    ports:
      - "127.0.0.1:9621:9621"
    depends_on:
      bot:
        condition: service_healthy
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://localhost:9621/health"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 60s
```

**Что НЕ делать:**
- ❌ Не использовать `LIGHTRAG_API_KEY_FILE` — env var не существует.
- ❌ Не класть API key в `.env` — он генерируется уникально per-install, в `.env.example` не должен быть.
- ❌ Не передавать `--key` через `command:` в docker-compose в открытом виде — entrypoint обязателен.
- ❌ Не убирать `--workers` если хочешь масштаб; для personal use оставь дефолт (`1`). С `--workers 2` нужен gunicorn-режим (`lightrag-gunicorn`), отдельный launcher. План в F явно запрещает gunicorn — значит `--workers` опускаем.
- ❌ Не оставлять старый `command:` массив в docker-compose lightrag-api сервисе — он перетрёт `ENTRYPOINT` и сломает auth.

**DoD G1:**
- [ ] `curl http://localhost:9621/health` без ключа → 200 (это whitelisted, см. G0 #5).
- [ ] `curl -H "X-API-Key: WRONG" http://localhost:9621/graphs?label=*` → 401.
- [ ] `curl -H "X-API-Key: $(cat secrets/lightrag_api_key.txt)" http://localhost:9621/graphs?label=*` → 200 или 404 (если граф пустой) — но НЕ 401.
- [ ] Логи `lightrag-api` при старте содержат строку про `api_key_configured=True` (или эквивалентную).

---

### G2. MCP-клиент — заменить инструкцию pip install на git clone

**Зачем:** PyPI пакета `daniel-lightrag-mcp` нет. `pip install` упадёт с 404.

**Файлы:** `README.md`, `docs/README_zh.md`, `docs/README_es.md`, `docs/README_pt.md`, `docs/README_fr.md`.

**Шаги:**

1. В `README.md` найти раздел `### 2. Install the MCP bridge` и заменить блок установки:

**Было:**
```markdown
```bash
pip install daniel-lightrag-mcp
# or with pipx for isolation:
pipx install daniel-lightrag-mcp
```
```

**Стало:**
```markdown
The bridge is not on PyPI yet — install it from source:

```bash
git clone https://github.com/desimpkins/daniel-lightrag-mcp.git
cd daniel-lightrag-mcp
pip install -e .
```

After install, the `daniel-lightrag-mcp` command is available in your PATH.
```

2. В каждом из 4 переводов найти блок `pip install daniel-lightrag-mcp` и заменить на:

```bash
git clone https://github.com/desimpkins/daniel-lightrag-mcp.git
cd daniel-lightrag-mcp && pip install -e .
```

(Без длинных пояснений — для переводов достаточно команды + ссылки на главный README.)

**Что НЕ делать:**
- ❌ Не предлагать `pipx install` для git-проекта — он не предназначен для local checkout. Только `pip install -e .`.
- ❌ Не писать в README «package coming soon to PyPI» — это домысел про чужой проект.
- ❌ Не форкать `daniel-lightrag-mcp` в наш репо чтобы публиковать на PyPI — лишняя поддержка.

**DoD G2:**
- [ ] Во всех 5 README команда установки начинается с `git clone https://github.com/desimpkins/daniel-lightrag-mcp.git`.
- [ ] Слова `pip install daniel-lightrag-mcp` нет ни в одном файле.

---

### G3. MCP-клиент — поправить env var на LIGHTRAG_BASE_URL

**Зачем:** реальный `daniel-lightrag-mcp` читает `LIGHTRAG_BASE_URL`, не `LIGHTRAG_API_URL`. Сейчас в JSON-конфиге неправильное имя.

**Файлы:** те же 5 README'ов.

**Шаги:** в JSON-блоке для Claude Code заменить `LIGHTRAG_API_URL` на `LIGHTRAG_BASE_URL`. Пример:

```json
{
  "mcpServers": {
    "mnemo-brain": {
      "command": "daniel-lightrag-mcp",
      "env": {
        "LIGHTRAG_BASE_URL": "http://localhost:9621",
        "LIGHTRAG_API_KEY": "<paste contents of secrets/lightrag_api_key.txt>"
      }
    }
  }
}
```

**DoD G3:**
- [ ] `grep -r "LIGHTRAG_API_URL" README.md docs/` → пусто.
- [ ] `grep -r "LIGHTRAG_BASE_URL" README.md docs/` → 5 совпадений (по одному на файл).

---

### G4. MCP_TESTING.md — починить чеклист

**Зачем:** в текущем чеклисте два пункта **никогда не пройдут** — не потому что система сломана, а потому что план врёт. Юзер дёрнет проверку, удивится, и потеряет доверие к остальной документации.

**Файл:** `docs/MCP_TESTING.md`.

**Шаги:** заменить блок `## API server health` целиком:

```markdown
## API server health

- [ ] `curl http://localhost:9621/health` → 200 (whitelisted, no auth required)
- [ ] `curl http://localhost:9621/graphs?label=*` без ключа → 401
- [ ] `curl -H "X-API-Key: $(cat secrets/lightrag_api_key.txt)" http://localhost:9621/graph/label/list` → 200 (returns label list, may be empty)
- [ ] `curl -H "X-API-Key: WRONG" http://localhost:9621/graphs?label=*` → 401
```

**Что НЕ делать:**
- ❌ Не возвращать `/graphs/labels` обратно — endpoint не существует.
- ❌ Не утверждать что `/health` требует ключа — он whitelisted.

**DoD G4:**
- [ ] Все 4 curl-проверки реально проходят на запущенной инсталляции.

---

### G5. Починить два падающих теста

**Зачем:** `pytest` сейчас красный, CI упадёт.

#### G5.1. `tests/test_dedup.py::test_find_similar_cyrillic_transliteration`

`rapidfuzz.WRatio` не делает транслитерацию кириллица↔латиница. Тест построен на ложном предположении плана.

**Решение:** удалить тест целиком. Поведение по транслитерации — open question, реально решается тем что extractor LLM сам кладёт оба варианта в `aliases`.

В `tests/test_dedup.py` удалить функцию `test_find_similar_cyrillic_transliteration` (вместе с декоратором).

В `IMPLEMENTATION_PLAN.md §7. Тесты → test_dedup.py` удалить пункт `find_similar("project", "ЛегАИ")`.

**Что НЕ делать:**
- ❌ Не понижать threshold чтобы тест проходил случайно — получим ложные срабатывания на проде.
- ❌ Не подключать `unidecode`/`transliterate` библиотеки только ради теста — план не предусматривает.

#### G5.2. `tests/test_linker.py::test_propose_links_returns_mock_result`

`propose_links` делает ранний return когда `_build_candidates_context()` пустой. В фикстуре оба файла попадают в `touched_paths` → кандидатов нет.

**Решение:** добавить третий файл в фикстуру, который **не** в `touched_paths`, чтобы кандидат-контекст был непустой.

В `tests/test_linker.py` найти фикстуру `vault` и добавить третью заметку:

```python
    (tmp_path / "20_People").mkdir(parents=True)
    (tmp_path / "20_People" / "anna.md").write_text(
        "---\ntype: person\naliases: []\n---\n\nAnna from LegAI team.\n",
        encoding="utf-8",
    )
```

В тесте `propose_links([...])` оставить аргументом только `["50_Tasks/deploy.md", "40_Projects/legai.md"]` — `anna.md` останется как кандидат, контекст непустой, мок будет вызван.

**DoD G5:**
- [ ] `uv run pytest -q tests/` → все тесты зелёные, 0 failed.

---

### G6. .env.example — добавить настройки lightrag-server

**Зачем:** без них `lightrag-api` контейнер крашнется на первом запросе (нет LLM/embedding binding).

**Файл:** `.env.example`.

**Шаги:** добавить в конец секцию (значения подставить из тех же что бот использует — OpenAI):

```env
# ── LightRAG API server (used by lightrag-api Docker service) ─────────────────
# These are read by lightrag-server (separate from the bot's embedded LightRAG).
# Bot uses OpenAI directly via OPENAI_API_KEY above.
LLM_BINDING=openai
LLM_BINDING_HOST=https://api.openai.com/v1
LLM_MODEL=gpt-5.4
LLM_BINDING_API_KEY=${OPENAI_API_KEY}

EMBEDDING_BINDING=openai
EMBEDDING_BINDING_HOST=https://api.openai.com/v1
EMBEDDING_MODEL=text-embedding-3-large
EMBEDDING_DIM=3072
EMBEDDING_BINDING_API_KEY=${OPENAI_API_KEY}
```

**Что НЕ делать:**
- ❌ Не дублировать `OPENAI_API_KEY` повторно — `${OPENAI_API_KEY}` работает в docker-compose env-substitution.
- ❌ Не ставить `EMBEDDING_DIM=1536` — модель `text-embedding-3-large` отдаёт 3072. Если поставишь 1536, индексация порушится.
- ❌ Не добавлять `LIGHTRAG_LLM_*` префиксы — это устаревший формат.

**DoD G6:**
- [ ] После `cp .env.example .env` и заполнения `OPENAI_API_KEY` контейнер `lightrag-api` стартует без ошибок binding.
- [ ] `docker compose logs lightrag-api` не содержит `LLM_BINDING is required` или подобного.

---

### G7. План §F0 + README — убрать ложные claim'ы

**Зачем:** план и README сейчас обещают то, чего архитектура не даёт. Это вводит юзера в заблуждение и сжигает доверие при первом же страктом ревью.

#### G7.1. Multi-process safe storage — переписать честно

**Файл:** `IMPLEMENTATION_PLAN.md`, секция `### F0. Требования к решению`.

**Заменить пункт:**

> «Multi-worker shared storage обязателен. Иначе при одновременной записи бота и чтении сервера — corrupted reads.»

**На:**

> «Concurrent access — best-effort, не транзакционный. Бот — единственный writer, lightrag-api — read-only по факту использования. `initialize_share_data` вызывается в каждом процессе для активации внутренних file-locks LightRAG, но `multiprocessing.Manager()` НЕ шарится между контейнерами (он создаётся локально в каждом). Атомарность держится на: (1) бот пишет через `os.replace`, (2) lightrag-api при чтении переоткрывает файлы. В редких случаях api-сервер может вернуть устаревший снапшот — eventual consistency приемлема для personal-use графа.»

#### G7.2. Read-only claim — убрать

**Файл:** `README.md`, раздел `## Connect Your Brain to AI Coding Tools`.

**Удалить из таблицы тулов фразы про read-only:**

> «All tools are read-only. Your bot is the only writer.»

**Заменить на честное:**

```markdown
### Security note

`daniel-lightrag-mcp` exposes 22 tools — including write tools (`insert_text`, `delete_document`, etc.). When you grant Claude Code access to this MCP server, you grant it write access to your knowledge graph.

If you only want Claude Code to read, do one of:

- Use a tool that exposes only read endpoints (e.g. fork the bridge and remove write tools)
- Put a reverse proxy in front of port 9621 that whitelists only `GET /graphs/*`, `POST /query`, `GET /health`
- Trust your own coding tool — for personal use, this is usually fine

Mnemo's bot remains the canonical writer. Anything Claude Code inserts via MCP is **not** reflected in your Obsidian vault — it only lives in the LightRAG graph index. To get it into the vault, message the bot.
```

**Файл:** `IMPLEMENTATION_PLAN.md`, секция `### F0`.

Удалить пункт «Read-only для внешних клиентов. Claude Code/Cursor могут только запрашивать (`/query`, `/graphs`), но не делать `/documents/upload`. Бот — единственный writer.»

Заменить на: «Read-only НЕ enforced на уровне MCP-сервера. Юзер сам отвечает за то, какому клиенту даёт доступ. Бот остаётся каноническим writer'ом для vault'а — то что MCP-клиент пишет в граф, не синхронизируется обратно в Obsidian заметки.»

**Что НЕ делать:**
- ❌ Не пытаться убрать write-tools из `daniel-lightrag-mcp` правкой их кода — это чужой репо.
- ❌ Не внедрять reverse-proxy сейчас (это отдельная фаза, опциональная). Сейчас задача — **честная документация**.

**DoD G7:**
- [ ] В плане §F0 пункт про «Read-only для внешних клиентов» переписан.
- [ ] В плане §F0 пункт про «Multi-worker shared storage» переписан с признанием cross-container ограничений.
- [ ] В README раздел `## Connect Your Brain to AI Coding Tools` содержит «Security note» с честным предупреждением.
- [ ] Слов «read-only» применительно к MCP больше нет ни в README ни в плане.

---

### G8. Bot healthcheck для безопасного старта

**Зачем:** `depends_on: bot` сейчас ждёт только PID, не готовности. После G1 condition: service_healthy требует реальный healthcheck.

**Файл:** `docker-compose.yml`.

**Шаги:** добавить healthcheck в сервис `bot`. Бот не имеет HTTP-endpoint'а, поэтому проверяем что Python-процесс отвечает Redis-у.

```yaml
  bot:
    build: .
    env_file: .env
    volumes:
      - ./data/vault:/data/vault
      - ./data/lightrag:/data/lightrag
      - ./secrets/vault_ssh_key:/run/secrets/vault_ssh_key:ro
    depends_on:
      redis:
        condition: service_healthy
    restart: unless-stopped
    healthcheck:
      test: ["CMD-SHELL", "python -c 'import sys, redis; r = redis.from_url(\"redis://redis:6379/0\"); sys.exit(0 if r.ping() else 1)'"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 90s
```

`start_period: 90s` — у бота на старте может быть медленная инициализация (lightrag, scheduler, telegram polling).

**Что НЕ делать:**
- ❌ Не делать healthcheck через telegram API — это внешний сервис, может тротлить, healthcheck станет flaky.
- ❌ Не делать healthcheck через `pgrep python` — процесс жив ≠ бот работает.

**DoD G8:**
- [ ] `docker compose ps` показывает у `bot` статус `(healthy)` после старта.
- [ ] `docker compose up -d` поднимает `lightrag-api` только **после** того как `bot` стал healthy.

---

### G9. Sanity-check фазы F после фиксов

**Зачем:** после G1–G8 нужно убедиться что вся Phase F снова собирается end-to-end.

**Файл:** `docs/MCP_TESTING.md` — добавить секцию `## Sanity check after Phase G fixes`.

**Шаги:**

```markdown
## Sanity check after Phase G fixes

Run through this BEFORE declaring Phase F + G done.

### Build & start

- [ ] `./setup.sh` на чистом репо проходит без ошибок
- [ ] `docker compose up -d --build` поднимает 3 контейнера
- [ ] `docker compose ps` показывает `bot (healthy)`, `lightrag-api (healthy)`, `redis (healthy)` через ≤ 2 минуты

### Auth

- [ ] G1 DoD прошёл (4 curl-проверки)

### MCP install

- [ ] `git clone https://github.com/desimpkins/daniel-lightrag-mcp.git && cd daniel-lightrag-mcp && pip install -e .` проходит
- [ ] `daniel-lightrag-mcp --help` запускается

### MCP query

- [ ] Claude Code с конфигом из README подключается к серверу (зелёный статус)
- [ ] Запрос «what's in my brain about <topic>» возвращает ответ из графа

### Tests

- [ ] `uv run pytest -q tests/` — все зелёные

### Docs honesty

- [ ] Поиск по README + docs/ слов `LIGHTRAG_API_URL`, `read-only`, `pip install daniel-lightrag-mcp`, `/graphs/labels` — везде пусто.
```

**DoD G9:**
- [ ] Все чекбоксы выше пройдены и приложены к PR.

---

### Фаза G — общий DoD

- [ ] G1: API key реально требуется для непривилегированных endpoints
- [ ] G2: установка MCP-bridge работает as documented (нет 404)
- [ ] G3: env var в JSON-конфиге совпадает с тем что читает реальный пакет
- [ ] G4: чеклист `MCP_TESTING.md` соответствует реальным endpoint'ам и whitelisting'у
- [ ] G5: pytest зелёный
- [ ] G6: lightrag-api контейнер запускается и обрабатывает запрос end-to-end
- [ ] G7: план и README не содержат ложных claim'ов про read-only и shared storage
- [ ] G8: оркестрация старта детерминированная
- [ ] G9: sanity-check пройден

---

### Анти-паттерны конкретно для Фазы G

- ❌ **Не пытайся починить `LIGHTRAG_API_KEY_FILE` через монтирование в `/run/secrets/` и docker secrets driver.** В compose без swarm/k8s этот механизм работает только как обычный bind mount. Делай через entrypoint-скрипт (G1).
- ❌ **Не правь `daniel-lightrag-mcp` исходники чтобы он читал `LIGHTRAG_API_URL`.** Это чужой репо. Меняй имя у себя в README (G3).
- ❌ **Не добавляй `unidecode` ради одного теста.** Удали тест (G5.1).
- ❌ **Не поднимай `--workers 2` без gunicorn.** Plan F явно запретил gunicorn-режим. Оставь дефолт (1 worker) — для personal use достаточно. План в G1 уже это исправляет (`--workers` убран из CLI).
- ❌ **Не реализуй reverse-proxy для read-only filtering в этой же фазе.** Это **Phase H**, опциональная. Сейчас — только честная документация (G7).
- ❌ **Не правь `IMPLEMENTATION_PLAN.md §F0` исходный текст** — добавляй пометку «(см. G7 — пункт переписан)» рядом со старой формулировкой, чтобы история была видна.
- ❌ **Не выкатывай G частями.** Все 9 пунктов закрывай одним PR — иначе у юзера будет промежуточное состояние «MCP не работает потому что я только G1 сделал».

---

### Открытые вопросы (для владельца)

- [ ] Q (G7): хочешь ли реальный read-only enforcement через reverse-proxy? Если да — это Phase J, отдельной задачей. Сейчас просто честная документация.
- [ ] Q (G6): `LLM_MODEL=gpt-5.4` в .env.example — это финальное значение. **Никаких gpt-4o/gpt-4-turbo в проекте**, владелец явно потребовал.
- [ ] Q (G2): хочешь форкнуть `daniel-lightrag-mcp` под наш репо и запаблишить на PyPI как `mnemo-mcp` чтобы юзеры ставили `pip install mnemo-mcp`? Это Phase K, отдельно. Сейчас — git clone.

---

## ФАЗА H — Unified graph (custom KG injection) ❌ NOT STARTED — делать после G

> **Контекст:** после имплементации фазы F senior QA сделал инструментальный smoke-test и обнаружил архитектурную проблему: бот строит **типизированный граф через wikilinks в Obsidian**, а LightRAG **независимо** строит свой граф через LLM-extraction из markdown текста. Это два разных графа, не синхронизированы. Кроме того, LLM-extraction LightRAG'ом — это +2 LLM-вызова на каждую заметку (≈$0.01 каждая) поверх работы бота.
>
> **Цель Phase H:** убрать LLM-extraction в LightRAG, скармливать ему **готовый граф** через `ainsert_custom_kg(...)`. LightRAG только **эмбеддит** наши entities/relations/chunks. Один источник правды — Obsidian frontmatter.
>
> **Эффект:**
> - 1 граф вместо 2 (наш typed-link граф = граф LightRAG)
> - Экономия 2 LLM-вызова на каждый `index_files()` (только embeddings остаются)
> - Точность связей выше (наши relations типизированы, у LightRAG-extracted — нет)
> - Сохраняется фишка LightRAG: семантический поиск по чанкам и сущностям

### H0. Что должен прочитать перед началом

1. Перечитай раздел `## Скептичные выводы` из QA-разбора (если в репо нет — спроси владельца).
2. Прочитай сигнатуру `ainsert_custom_kg` в `lightrag/lightrag.py` (строки 2370–2540 в `lightrag-hku==1.4.x`). Формат входа:

```python
custom_kg = {
    "chunks": [
        {
            "content": "raw text of chunk",
            "source_id": "doc-<sha>",       # custom unique ID per document
            "file_path": "20_People/anna.md",
            "chunk_order_index": 0,          # optional
        },
    ],
    "entities": [
        {
            "entity_name": "Anna",            # должен быть уникальным глобально
            "entity_type": "Person",
            "description": "Аня — CTO LegAI стартапа",
            "source_id": "doc-<sha>",         # связь с chunk через source_id
            "file_path": "20_People/anna.md",
        },
    ],
    "relationships": [
        {
            "src_id": "Anna",                  # должен совпадать с entity_name
            "tgt_id": "LegAI",
            "description": "Anna works at LegAI as CTO",
            "keywords": "for_job",             # наш тип связи
            "source_id": "doc-<sha>",
            "weight": 1.0,
            "file_path": "20_People/anna.md",
        },
    ],
}
```

3. **Не путать `ainsert_custom_kg` с `ainsert`.** Первый принимает готовый граф, второй запускает LLM-extraction. Мы переходим со второго на первый.

4. **Эмбеддинги всё равно делаются** — LightRAG эмбеддит chunks, entities (`name + description`), relationships (`description + keywords`) внутри `ainsert_custom_kg`. Стоимость embeddings ~$0.0001 за заметку — пренебрежимо.

---

### H1. Конвертер vault → custom_kg

**Создать новый файл:** `src/lightrag_svc/converter.py`.

Цель — взять список vault-relative paths и построить `custom_kg` dict готовый для `ainsert_custom_kg`.

```python
from __future__ import annotations
import hashlib
from pathlib import Path
from typing import Any

import structlog

from src.config import settings
from src.vault.frontmatter import LINK_FIELDS, parse, TYPE_FOLDERS

logger = structlog.get_logger()

# Маппинг наших note types → LightRAG entity_type (PascalCase)
_NOTE_TYPE_TO_ENTITY: dict[str, str] = {
    "person": "Person",
    "project": "Project",
    "task": "Task",
    "job": "Organization",
    "theme": "Theme",
    "memory": "Memory",
    "thought": "Thought",
}

# Frontmatter поля связей которые становятся relationships в LightRAG.
# Список: только прямые связи (не обратные), чтобы избежать дубликатов.
_FORWARD_RELATIONS = {
    "owner", "works_at", "for_job", "for_project",
    "themes", "about_person", "related_people", "parent_theme",
}


def _entity_name_from_path(rel_path: str) -> str:
    """Стабильное имя сущности из vault path. Используется и как entity_name и как src_id."""
    return rel_path.removesuffix(".md")


def _doc_id(rel_path: str, content: str) -> str:
    """Уникальный source_id для одной заметки = sha от path + content hash."""
    h = hashlib.sha256(f"{rel_path}|{content}".encode("utf-8")).hexdigest()[:12]
    return f"doc-{h}"


def _strip_frontmatter_for_chunk(body: str, title: str) -> str:
    """Тело без YAML и без секции `## Связи` — для чистого embedding."""
    if "## Связи" in body:
        body = body.split("## Связи", 1)[0].rstrip()
    return f"# {title}\n\n{body}".strip()


def _wikilink_to_entity_name(wikilink: str) -> str | None:
    """`[[40_Projects/legai]]` → `40_Projects/legai`. Игнорирует pipe-aliases."""
    s = wikilink.strip()
    if not (s.startswith("[[") and s.endswith("]]")):
        return None
    inner = s[2:-2].split("|", 1)[0].strip()
    return inner or None


def vault_to_custom_kg(rel_paths: list[str]) -> dict[str, Any]:
    """Build a LightRAG custom_kg from a list of vault notes.

    Each note → 1 chunk + 1 entity + N relationships (one per forward link in frontmatter).
    """
    vault = Path(settings.vault_path)
    chunks: list[dict[str, Any]] = []
    entities: list[dict[str, Any]] = []
    relationships: list[dict[str, Any]] = []

    for rel in rel_paths:
        full = vault / rel
        if not full.exists() or full.suffix != ".md":
            continue
        raw = full.read_text(encoding="utf-8")
        fm, body = parse(raw)
        note_type = str(fm.get("type", "inbox"))
        if note_type in {"daily", "inbox"}:
            continue  # daily/inbox — не сущности графа

        title = full.stem.replace("-", " ")
        clean_body = _strip_frontmatter_for_chunk(body, title)
        source_id = _doc_id(rel, clean_body)

        chunks.append({
            "content": clean_body,
            "source_id": source_id,
            "file_path": rel,
        })

        entity_name = _entity_name_from_path(rel)
        entity_type = _NOTE_TYPE_TO_ENTITY.get(note_type, "Concept")
        # description = первые 2 факта из тела + aliases
        first_facts = "\n".join(
            line for line in body.splitlines()
            if line.strip().startswith("- ")
        )[:400]
        aliases = fm.get("aliases", []) or []
        description = (
            f"{title}. Aliases: {', '.join(aliases)}. {first_facts}"
            if aliases else f"{title}. {first_facts}"
        )

        entities.append({
            "entity_name": entity_name,
            "entity_type": entity_type,
            "description": description.strip() or title,
            "source_id": source_id,
            "file_path": rel,
        })

        for field in _FORWARD_RELATIONS:
            v = fm.get(field)
            if not v:
                continue
            targets = v if isinstance(v, list) else [v]
            for tgt_wikilink in targets:
                tgt_name = _wikilink_to_entity_name(str(tgt_wikilink))
                if not tgt_name or tgt_name == entity_name:
                    continue
                relationships.append({
                    "src_id": entity_name,
                    "tgt_id": tgt_name,
                    "description": f"{title} {field.replace('_', ' ')} {tgt_name}",
                    "keywords": field,
                    "source_id": source_id,
                    "weight": 1.0,
                    "file_path": rel,
                })

    logger.info(
        "vault_to_custom_kg built",
        chunks=len(chunks),
        entities=len(entities),
        relationships=len(relationships),
    )
    return {"chunks": chunks, "entities": entities, "relationships": relationships}
```

**Что НЕ делать:**
- ❌ Не использовать markdown title (с дефисами в slug) как `entity_name` — двусмысленно. Используй полный `vault-relative path без .md` (стабильный, уникальный).
- ❌ Не передавать YAML frontmatter в `chunks[].content` — он засоряет эмбеддинги. Только тело без frontmatter и без секции `## Связи`.
- ❌ Не пытаться передать обратные связи (`tasks`, `projects`, `mentions`...) — LightRAG отношения **неориентированные**, дубль будет. Только `_FORWARD_RELATIONS`.
- ❌ Не игнорировать `daily` и `inbox` — это правильное решение, но не превращай это в фильтр который надо вызывать на стороне caller'а. Делай прямо в `vault_to_custom_kg`.
- ❌ Не использовать `description="UNKNOWN"` для пустых тел — поставь хотя бы title.
- ❌ Не делать `entity_name` через slugify — потеряем стабильность (slug может меняться при правке title). Используй именно `rel_path.removesuffix('.md')`.
- ❌ Не добавлять сюда инкрементальную дедупликацию (если entity уже есть в графе) — это делает сам `ainsert_custom_kg` через `chunk_entity_relation_graph.upsert_nodes_batch`. У нас идемпотентность гарантирована стабильным `entity_name`.

**DoD H1:**
- [ ] `vault_to_custom_kg(["20_People/anna.md", "40_Projects/legai.md"])` возвращает dict с непустыми `chunks`, `entities`, `relationships` (если в anna.md есть `works_at: "[[40_Projects/legai]]"`).
- [ ] `entity_name` в `entities` совпадает с `src_id`/`tgt_id` в `relationships` для существующих ссылок.
- [ ] Тест `tests/test_converter.py` (см. H7) зелёный.

---

### H2. Заменить `index_files` на custom KG injection

**Файл:** `src/lightrag_svc/indexer.py`.

**Заменить целиком функцию `index_files`:**

```python
async def index_files(paths: list[str]) -> None:
    """Incrementally update the LightRAG graph from vault notes.

    Uses custom KG injection: our typed wikilinks become graph edges directly,
    no LLM extraction. Cost: ~$0.0001 per note (embeddings only).
    """
    if not paths:
        return
    from src.lightrag_svc.client import get_rag
    from src.lightrag_svc.converter import vault_to_custom_kg

    custom_kg = vault_to_custom_kg(paths)
    if not custom_kg["entities"]:
        logger.info("incremental index skipped: no entities")
        return

    rag = await get_rag()
    try:
        await rag.ainsert_custom_kg(custom_kg)  # type: ignore[attr-defined]
        logger.info(
            "incremental index done (custom kg)",
            entities=len(custom_kg["entities"]),
            relations=len(custom_kg["relationships"]),
            chunks=len(custom_kg["chunks"]),
        )
    except Exception as exc:
        logger.error("incremental index failed", error=str(exc))
```

**Заменить `full_reindex` тоже:**

```python
async def full_reindex() -> None:
    """Re-build the LightRAG graph from scratch from current vault state."""
    vault = Path(settings.vault_path)
    all_md = [
        m for m in vault.rglob("*.md")
        if not str(m.relative_to(vault)).startswith(("_meta/MOC_", "_meta/ontology", "_meta/portrait"))
    ]
    rel_paths = [str(m.relative_to(vault)) for m in all_md]
    logger.info("full reindex start (custom kg)", files=len(rel_paths))

    from src.lightrag_svc.converter import vault_to_custom_kg
    from src.lightrag_svc.client import get_rag

    # Clear existing graph storage by reinitializing (singleton swap)
    from src.lightrag_svc import client as lc
    if lc._rag is not None:
        # LightRAG has no public clear() — drop singleton and let next init rebuild
        lc._rag = None

    custom_kg = vault_to_custom_kg(rel_paths)
    if not custom_kg["entities"]:
        logger.info("full reindex: no entities")
        return

    rag = await get_rag()
    try:
        # Batch insert in chunks of 100 to avoid memory blow-up on big vaults
        BATCH = 100
        ents = custom_kg["entities"]
        rels = custom_kg["relationships"]
        chks = custom_kg["chunks"]
        for i in range(0, len(ents), BATCH):
            sub = {
                "entities": ents[i:i + BATCH],
                "relationships": [r for r in rels if r["src_id"] in {e["entity_name"] for e in ents[i:i + BATCH]}],
                "chunks": [c for c in chks if c["source_id"] in {e["source_id"] for e in ents[i:i + BATCH]}],
            }
            await rag.ainsert_custom_kg(sub)  # type: ignore[attr-defined]
            logger.info("reindex batch done", done=i + len(sub["entities"]), total=len(ents))
    except Exception as exc:
        logger.error("full reindex failed", error=str(exc))
        raise
```

**Что НЕ делать:**
- ❌ Не оставлять старый `await rag.ainsert(text)` нигде — теряется весь смысл фазы (он триггерит LLM-extraction).
- ❌ Не вызывать `ainsert_custom_kg` без `await rag.initialize_storages()` — это делается в `get_rag()` (фаза F1).
- ❌ Не пытаться очистить storage через `os.unlink` или `rmtree` в `full_reindex` — у LightRAG бывают открытые file-handle'ы. Singleton swap (`_rag = None`) — единственный безопасный путь, при следующем `get_rag()` он переоткроется и **обновит** существующие сущности (idempotent через `entity_name`).
- ❌ Не индексировать MOC файлы и `_meta/ontology.md` — они авто-сгенерированные, дублируют исходники.

**DoD H2:**
- [ ] При вызове `index_files(["20_People/anna.md"])` LLM extraction **не запускается** — в логах LightRAG нет `Extracting stage`, `Merging stage`, `LLM cache saving`.
- [ ] В логах есть `incremental index done (custom kg)` с числом entities и relations.
- [ ] Файл `data/lightrag/graph_chunk_entity_relation.graphml` содержит ноду `Anna` (entity_id) после индексации.

---

### H3. Перевод `_FORWARD_RELATIONS` source-of-truth

**Файл:** `src/vault/frontmatter.py`.

**Уточнение:** в фазе C мы определили `RELATION_INVERSE` и `LINK_FIELDS`. Теперь добавим явный `FORWARD_RELATIONS` — единственный source of truth для конвертера.

Добавить в `frontmatter.py` после `LINK_FIELDS`:

```python
# Прямые типизированные связи (single value или list).
# Используются конвертером в LightRAG custom KG (см. lightrag_svc/converter.py).
# Обратные (employs, projects, tasks, ...) НЕ включаются — это денормализация
# для Obsidian, не для графа.
FORWARD_RELATIONS: frozenset[str] = frozenset({
    "owner", "works_at", "for_job", "for_project",
    "themes", "about_person", "related_people", "parent_theme",
})
```

В `src/lightrag_svc/converter.py` импортировать оттуда:

```python
from src.vault.frontmatter import FORWARD_RELATIONS, LINK_FIELDS, parse
```

И использовать `FORWARD_RELATIONS` вместо локального `_FORWARD_RELATIONS`.

**Что НЕ делать:**
- ❌ Не добавлять копии этого set'а в других местах — единственное определение в `frontmatter.py`.
- ❌ Не путать `FORWARD_RELATIONS` (для конвертера) с `LINK_FIELDS` (для рендеринга `## Связи` секции). Они пересекаются но имеют разную семантику.

---

### H4. Сохранять граф LightRAG целостным при изменении typed link

**Файл:** `src/vault/linking.py`.

**Проблема:** когда `add_typed_link` меняет frontmatter, граф LightRAG не обновляется автоматически. Через 5 минут `kg_query` вернёт устаревшие данные.

**Решение:** после успешного `add_typed_link` (когда git_ops.commit прошёл) — кикнуть фоновую переиндексацию двух заметок (from + to).

В `add_typed_link` после `await git_ops.stage_and_commit(...)`:

```python
    # Background re-index of both endpoints into LightRAG (custom KG, no LLM)
    try:
        import asyncio
        from src.lightrag_svc.indexer import index_files
        _t = asyncio.create_task(index_files(paths_to_commit))
        _ = _t
    except Exception as exc:
        logger.warning("link reindex skipped", error=str(exc))
    return True
```

**Что НЕ делать:**
- ❌ Не вызывать `index_files` синхронно (`await`) — это блокирует ответ юзеру. Только `create_task`.
- ❌ Не индексировать только `from_path` — обратная сторона (`to_path`) тоже изменилась (в её frontmatter появилось обратное поле).
- ❌ Не падать `add_typed_link` если индексация упала — это нон-критичный пост-проход.

**DoD H4:**
- [ ] После `add_typed_link("50_Tasks/foo", "40_Projects/bar", "for_project")` через ~2 секунды граф содержит ребро `50_Tasks/foo --[for_project]--> 40_Projects/bar`.

---

### H5. Убрать LLM-extraction промпт-настройки в client.py

**Файл:** `src/lightrag_svc/client.py`.

`addon_params={"entity_types": entity_types}` — этот параметр влияет на промпт LLM-extraction. Если мы перешли на `ainsert_custom_kg`, **LLM extraction не запускается** → этот параметр бесполезен.

**Однако** оставить **нельзя** убрать `_load_entity_types()` целиком, потому что:
- `entity_types` всё ещё используется при `query()` для filtering result types (по докам LightRAG)
- Если в будущем кто-то случайно вызовет `ainsert` (не custom_kg), он получит правильные типы

**Решение:** оставить как есть. Никаких изменений в `client.py` не требуется.

**Но добавить комментарий**:

В `_load_entity_types` под docstring:

```python
async def _load_entity_types() -> list[str]:
    """Read entity_types from _meta/ontology.md if present, otherwise use defaults.

    Note: после Phase H мы используем ainsert_custom_kg, так что entity_types
    влияет только на retrieval-side filtering и фолбэк ainsert (которого больше нет).
    """
```

**DoD H5:**
- [ ] `_DEFAULT_ENTITY_TYPES` синхронен с `_NOTE_TYPE_TO_ENTITY.values()` в converter.py.
- [ ] Логи `lightrag initialized` показывают `entity_types=[Person, Project, Task, Organization, ...]`.

---

### H6. Удалить ontology auto-generation (или сделать опциональной)

**Файл:** `src/lightrag_svc/ontology.py` + `src/scheduler/triggers.py`.

**Контекст:** в фазе D2 мы делали `generate_ontology()` который через LLM выводил entity_types из выборки заметок. После H это **бессмысленно** — мы используем фиксированный маппинг `_NOTE_TYPE_TO_ENTITY` в конвертере, и ontology влияет только на retrieval (минимально).

**Два варианта:**

**Вариант A — удалить:**

1. Удалить `src/lightrag_svc/ontology.py`.
2. В `src/scheduler/triggers.py` убрать ветку `regenerate_ontology_then_reindex`, оставить только `full_reindex` с новой custom KG логикой:

```python
async def _handle_system_task(payload: dict) -> None:
    action = payload.get("action")
    if action == "full_reindex" or action == "regenerate_ontology_then_reindex":
        try:
            from src.lightrag_svc.indexer import full_reindex
            await full_reindex()
            logger.info("full reindex completed")
        except Exception as exc:
            logger.error("reindex failed", error=str(exc))
```

3. В `src/scheduler/defaults.py` поменять `lightrag_ontology_and_reindex` на просто `lightrag_full_reindex`:

```python
("lightrag_full_reindex", "0 3 * * 6", "system",
    {"action": "full_reindex", "description": "LightRAG еженедельный full reindex"}),
```

**Вариант B — оставить как «человекочитаемая ontology в `_meta/ontology.md`»:** auto-generated файл для юзера в Obsidian, для документации того что в графе. Без эффекта на runtime. План — keep as is, только добавить комментарий что это для UX.

**Рекомендация**: **Вариант A**. Меньше кода = меньше поломок.

**Что НЕ делать:**
- ❌ Не оставлять `generate_ontology()` запускаться при каждом крон-триггере если выбран Вариант A — это $0.10 в неделю на LLM-вызов «впустую».
- ❌ Не удалять scheduled task если задача в Redis уже создана — нужно `scheduler.remove_job("lightrag_ontology_and_reindex")` при первом старте после миграции.

**DoD H6:**
- [ ] При выбранном Варианте A: модуль `ontology.py` удалён, scheduled task переименована, тесты не падают.
- [ ] `ainsert_custom_kg` использует фиксированные типы из converter, не из `_meta/ontology.md`.

---

### H7. Тест конвертера

**Создать:** `tests/test_converter.py`.

```python
from __future__ import annotations
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    (tmp_path / "20_People").mkdir(parents=True)
    (tmp_path / "40_Projects").mkdir(parents=True)
    (tmp_path / "10_Daily").mkdir(parents=True)

    (tmp_path / "20_People" / "anna.md").write_text(
        "---\n"
        "type: person\n"
        "aliases:\n- Anya\n- Аня\n"
        "works_at: '[[40_Projects/legai]]'\n"
        "---\n\n"
        "## Факты\n\n- CTO LegAI\n- работает с командой 5 лет\n",
        encoding="utf-8",
    )
    (tmp_path / "40_Projects" / "legai.md").write_text(
        "---\ntype: project\naliases: [LegAI]\n---\n\nAI startup.\n",
        encoding="utf-8",
    )
    (tmp_path / "10_Daily" / "2026-05-07.md").write_text(
        "---\ntype: daily\n---\n\nSession notes.\n",
        encoding="utf-8",
    )
    return tmp_path


def test_converter_skips_daily(vault: Path) -> None:
    with patch("src.lightrag_svc.converter.settings") as ms:
        ms.vault_path = str(vault)
        from src.lightrag_svc.converter import vault_to_custom_kg
        kg = vault_to_custom_kg([
            "20_People/anna.md",
            "40_Projects/legai.md",
            "10_Daily/2026-05-07.md",
        ])

    entity_paths = {e["file_path"] for e in kg["entities"]}
    assert "10_Daily/2026-05-07.md" not in entity_paths


def test_converter_creates_typed_relationship(vault: Path) -> None:
    with patch("src.lightrag_svc.converter.settings") as ms:
        ms.vault_path = str(vault)
        from src.lightrag_svc.converter import vault_to_custom_kg
        kg = vault_to_custom_kg(["20_People/anna.md", "40_Projects/legai.md"])

    works_at = [r for r in kg["relationships"] if r["keywords"] == "works_at"]
    assert len(works_at) == 1
    assert works_at[0]["src_id"] == "20_People/anna"
    assert works_at[0]["tgt_id"] == "40_Projects/legai"


def test_converter_chunk_excludes_frontmatter_and_links(vault: Path) -> None:
    with patch("src.lightrag_svc.converter.settings") as ms:
        ms.vault_path = str(vault)
        from src.lightrag_svc.converter import vault_to_custom_kg
        kg = vault_to_custom_kg(["20_People/anna.md"])

    chunk = kg["chunks"][0]["content"]
    assert "type: person" not in chunk
    assert "works_at:" not in chunk
    assert "## Связи" not in chunk
    assert "CTO LegAI" in chunk


def test_converter_entity_name_stable_across_runs(vault: Path) -> None:
    """entity_name должен быть стабилен (rel_path без .md), не slugify."""
    with patch("src.lightrag_svc.converter.settings") as ms:
        ms.vault_path = str(vault)
        from src.lightrag_svc.converter import vault_to_custom_kg
        kg1 = vault_to_custom_kg(["20_People/anna.md"])
        kg2 = vault_to_custom_kg(["20_People/anna.md"])

    assert kg1["entities"][0]["entity_name"] == kg2["entities"][0]["entity_name"]
    assert kg1["entities"][0]["entity_name"] == "20_People/anna"
```

**DoD H7:**
- [ ] `uv run pytest -q tests/test_converter.py` — 4/4 зелёные.

---

### H8. Smoke-test end-to-end в `MCP_TESTING.md`

**Файл:** `docs/MCP_TESTING.md`. Добавить секцию:

```markdown
## Phase H: custom KG injection sanity check

После Phase H проверь что LLM-extraction LightRAG'ом действительно отключена.

- [ ] Удалить `data/lightrag/*` (только содержимое, не папку)
- [ ] Перезапустить бот: `docker compose restart bot`
- [ ] Послать в бот сообщение которое создаст 2-3 заметки с typed links
- [ ] Дождаться завершения сессии (`/save` или 15 мин idle)
- [ ] Проверить логи: `docker compose logs bot | grep -E "Extracting stage|LLM cache"` — должно быть **пусто** (custom KG не вызывает LLM extraction)
- [ ] Проверить логи: `docker compose logs bot | grep "incremental index done (custom kg)"` — должна быть как минимум одна строка
- [ ] Открыть `data/lightrag/graph_chunk_entity_relation.graphml` в текстовом редакторе (это XML)
- [ ] Найти ноду с `entity_id="20_People/<name>"` — должна существовать
- [ ] Найти ребро с `keywords="for_project"` или подобной нашей типизированной связью — должно существовать
- [ ] Вызвать через MCP в Claude Code запрос про этого человека — ответ должен ссылаться на entity и relations из графа
```

**DoD H8:**
- [ ] Все чекбоксы выше пройдены на dev-машине.

---

### Анти-паттерны конкретно для Фазы H

- ❌ **Не используй `gpt-4o`, `gpt-4`, `gpt-4-turbo`** нигде — ни в коде, ни в моках, ни в примерах. Только `gpt-5.4` / `gpt-5.4-mini`. Это требование владельца.
- ❌ **Не оставляй обе функции `ainsert` и `ainsert_custom_kg` параллельно работающими.** Либо одна, либо другая. Иначе у тебя в графе будут и custom-injected сущности, и LLM-extracted дубликаты.
- ❌ **Не пытайся «улучшить качество» добавляя обратно LLM-extraction поверх custom KG.** Если кажется что description слабые — улучшай в боте при создании заметки, не дублируй работу.
- ❌ **Не делай `entity_name` через `slugify(title)`** — у нас уже есть стабильный rel_path, slug может ломаться при переименовании.
- ❌ **Не передавай в `chunks[].content` весь raw markdown с YAML.** Только тело без frontmatter и без `## Связи`.
- ❌ **Не индексируй заметки типов `daily` и `inbox` в граф** — это не сущности, это сессионные/незавершённые заметки.
- ❌ **Не считай что `addon_params={"entity_types": [...]}` теперь не нужен.** Он влияет на retrieval-side, оставь как есть (см. H5).
- ❌ **Не запускай `full_reindex` синхронно из chat-handler'а** — это блокирует юзера на минуты. Только из крон-задачи.
- ❌ **Не пытайся реализовать удаление сущности из графа через `ainsert_custom_kg` с пустым description** — это не удаление, а апдейт. Для удаления используй `rag.adelete_by_entity()` (отдельная задача, не в этой фазе).
- ❌ **Не делай инкрементальную дедупликацию своими руками.** `ainsert_custom_kg` сам upsert'ит по `entity_name` (см. строки 2422–2425 в `lightrag.py`).

---

### Открытые вопросы для владельца (Phase H)

- [ ] Q (H1): `description` сущности сейчас собирается как `title + aliases + первые bullet-факты`. Хочешь пробросить ВСЕ факты (может быть много токенов) или ограничиться 400 char?
- [ ] Q (H2): `full_reindex` сейчас перестраивает singleton — это значит in-memory кэш `llm_response_cache.json` теряется. Желаемое поведение, или хочешь сохранить кэш между реиндексами?
- [ ] Q (H4): после `add_typed_link` запускается фон-переиндексация. Если связей много (linker создал 10 за раз) — будет 10 параллельных переиндексаций. Дебаунсить?
- [ ] Q (H6): Вариант A (удалить ontology) или Вариант B (оставить как UX docu)? **Рекомендация: A.**

---

### Phase H — оценка эффекта

| Метрика | До (Phase F) | После (Phase H) | Дельта |
|---|---|---|---|
| LLM-вызовов на заметку | 2 (LightRAG extract+gleaning) + работа бота | 0 (только бот) | **-2** |
| Стоимость индексации 1 заметки | ~$0.01–0.02 | ~$0.0001 (только embeddings) | **~100×↓** |
| Графов в системе | 2 (Obsidian + LightRAG, рассинхронизированы) | 1 (Obsidian = LightRAG) | **-1** |
| Точность связей | LLM-извлеченные, нечёткие | Типизированные, ровно как в frontmatter | **+** |
| Семантический поиск (chunks) | работает | работает (без YAML-шума → лучше) | **+** |
| Граф-traversal (`mode="mix"`) | работает на LightRAG-extracted entities | работает на наших typed entities | **=, но точнее** |

---

### H9. Rename hook — синхронизация графа при переименовании заметки

**Зачем:** в `entity_name` мы используем `rel_path.removesuffix(".md")` (см. H1) — стабильно по содержимому, но **меняется** при переименовании. Если юзер просит бота `move_note("20_People/anna.md", "20_People/anna-petrova.md")`, то:
- Файл переезжает в новую папку (текущее поведение работает)
- Все обратные wikilinks обновляются (это уже делает `writer.move_note` через ripgrep+sed, проверь — если нет, это **отдельный bug**)
- **НО в графе LightRAG старая нода `20_People/anna` остаётся**, новая `20_People/anna-petrova` создаётся отдельно. У тебя orphan + дубликат с разными source_ids, и `kg_query` будет находить обе.

Это нельзя решить «само рассосётся» — `ainsert_custom_kg` upsert'ит по новому имени, не удаляет старое. Нужно **явно** удалить старую сущность из графа.

#### H9.1. Хук на `move_note` в writer

**Файл:** `src/vault/writer.py` — функция `move_note`.

После того как файл успешно переехал (после `git_ops.stage_and_commit`), запустить фоновую задачу:

```python
async def move_note(old_rel: str, new_rel: str, session_id: str = "") -> str:
    # ... existing logic that moves the file and commits ...

    # ⭐ НОВОЕ — sync LightRAG graph: delete old entity, index new path
    try:
        import asyncio
        from src.lightrag_svc.graph_sync import handle_rename
        _t = asyncio.create_task(handle_rename(old_rel, new_rel))
        _ = _t
    except Exception as exc:
        logger.warning("graph rename hook skipped", error=str(exc))

    return sha
```

**Что НЕ делать:**
- ❌ Не делать удаление синхронно — `adelete_by_entity` может занять секунды (нужно перестроить связанные эмбеддинги).
- ❌ Не вызывать удаление **до** успешного move — если move провалится, ты удалишь сущность из графа без того что её можно восстановить.
- ❌ Не помещать логику удаления внутрь `move_note` напрямую — `writer.py` не должен знать про lightrag_svc. Только импорт фасада.

#### H9.2. Модуль `src/lightrag_svc/graph_sync.py`

Создать новый файл с фасадом всех graph-side sync операций.

```python
from __future__ import annotations
import structlog

from src.lightrag_svc.client import get_rag

logger = structlog.get_logger()


def _entity_name(rel_path: str) -> str:
    """Same convention as converter.py: rel_path without .md."""
    return rel_path.removesuffix(".md")


async def handle_rename(old_rel: str, new_rel: str) -> None:
    """Sync LightRAG graph after a vault note rename: delete old entity, index new."""
    old_name = _entity_name(old_rel)
    new_name = _entity_name(new_rel)
    if old_name == new_name:
        return  # nothing to do

    rag = await get_rag()

    # Step 1: delete old entity (and its edges) from graph
    try:
        await rag.adelete_by_entity(old_name)  # type: ignore[attr-defined]
        logger.info("graph entity deleted on rename", old=old_name, new=new_name)
    except Exception as exc:
        # If old entity didn't exist (never indexed) — that's fine, log and continue
        logger.warning("graph entity delete failed (likely never indexed)",
                       old=old_name, error=str(exc))

    # Step 2: index new path via custom KG (creates new entity + relations)
    try:
        from src.lightrag_svc.indexer import index_files
        await index_files([new_rel])
    except Exception as exc:
        logger.error("graph reindex after rename failed", new=new_rel, error=str(exc))


async def handle_delete(rel_path: str) -> None:
    """Sync LightRAG graph after a vault note delete (used by writer.delete_note)."""
    name = _entity_name(rel_path)
    rag = await get_rag()
    try:
        await rag.adelete_by_entity(name)  # type: ignore[attr-defined]
        logger.info("graph entity deleted on note delete", name=name)
    except Exception as exc:
        logger.warning("graph entity delete failed", name=name, error=str(exc))
```

**Что НЕ делать:**
- ❌ Не делать `try: await rag.adelete_by_entity(old); except: pass`. Лог обязательно с `error=str(exc)` чтобы можно было отлаживать.
- ❌ Не реализовывать `handle_rename` через `move_entity` LightRAG'а (если такой есть) — он переносит ноду но **не** rebuild'ит embeddings под новое имя/контент. Только delete+insert.
- ❌ Не вызывать `handle_rename` если `old == new` — пустая работа, лишние LLM кэш-инвалидации.
- ❌ Не делать `handle_rename` с `adelete_by_entity(new)` сначала — это уничтожит свеже-проиндексированную сущность в случае race condition.

#### H9.3. Хук на `delete_note` в writer

**Файл:** `src/vault/writer.py` — функция `delete_note`.

Аналогично `move_note`, после удаления файла и git commit:

```python
async def delete_note(path: str, *, confirmed: bool, session_id: str = "") -> str:
    # ... existing logic ...

    try:
        import asyncio
        from src.lightrag_svc.graph_sync import handle_delete
        _t = asyncio.create_task(handle_delete(path))
        _ = _t
    except Exception as exc:
        logger.warning("graph delete hook skipped", error=str(exc))

    return sha
```

#### H9.4. Тест rename hook

**Создать:** `tests/test_graph_sync.py`.

```python
from __future__ import annotations
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_handle_rename_deletes_old_and_indexes_new() -> None:
    mock_rag = MagicMock()
    mock_rag.adelete_by_entity = AsyncMock(return_value=None)

    with (
        patch("src.lightrag_svc.graph_sync.get_rag", new=AsyncMock(return_value=mock_rag)),
        patch("src.lightrag_svc.indexer.index_files", new=AsyncMock(return_value=None)) as mock_idx,
    ):
        from src.lightrag_svc.graph_sync import handle_rename
        await handle_rename("20_People/anna.md", "20_People/anna-petrova.md")

    mock_rag.adelete_by_entity.assert_called_once_with("20_People/anna")
    mock_idx.assert_called_once_with(["20_People/anna-petrova.md"])


@pytest.mark.asyncio
async def test_handle_rename_noop_when_paths_equal() -> None:
    mock_rag = MagicMock()
    mock_rag.adelete_by_entity = AsyncMock()

    with patch("src.lightrag_svc.graph_sync.get_rag", new=AsyncMock(return_value=mock_rag)):
        from src.lightrag_svc.graph_sync import handle_rename
        await handle_rename("20_People/anna.md", "20_People/anna.md")

    mock_rag.adelete_by_entity.assert_not_called()


@pytest.mark.asyncio
async def test_handle_delete_calls_adelete_by_entity() -> None:
    mock_rag = MagicMock()
    mock_rag.adelete_by_entity = AsyncMock(return_value=None)

    with patch("src.lightrag_svc.graph_sync.get_rag", new=AsyncMock(return_value=mock_rag)):
        from src.lightrag_svc.graph_sync import handle_delete
        await handle_delete("40_Projects/legai.md")

    mock_rag.adelete_by_entity.assert_called_once_with("40_Projects/legai")
```

#### H9.5. Smoke-test в MCP_TESTING.md

Добавить в `docs/MCP_TESTING.md` (после H8 секции):

```markdown
## Phase H9: rename hook sanity check

- [ ] Создать заметку через бота: «у меня появилась новая работа в LegAI»
- [ ] Подождать индексации (~3 сек после `apply_to_vault`)
- [ ] Запросить через MCP: «what is in my brain about LegAI?» → ответ ссылается на entity `30_Jobs/legai`
- [ ] Через бота: «переименуй legai в legai-corp»
- [ ] Подождать ~5 сек
- [ ] Запросить тот же запрос → ответ ссылается на entity `30_Jobs/legai-corp`
- [ ] **НЕ должна** в ответе появляться старая `30_Jobs/legai` — если появилась, значит H9 не отработал
- [ ] Открыть `data/lightrag/graph_chunk_entity_relation.graphml` — ноды `legai` НЕ должно быть, есть только `legai-corp`
```

#### Фаза H9 — DoD

- [ ] H9.1: `writer.move_note` вызывает `graph_sync.handle_rename` фоном
- [ ] H9.2: `src/lightrag_svc/graph_sync.py` существует с `handle_rename` и `handle_delete`
- [ ] H9.3: `writer.delete_note` вызывает `graph_sync.handle_delete` фоном
- [ ] H9.4: `tests/test_graph_sync.py` зелёный
- [ ] H9.5: smoke test пройден на dev-машине

#### H9 — анти-паттерны

- ❌ **Не дёргать `adelete_by_entity` в hot path** (внутри chat handler без `create_task`). Удаление — медленное, блокирует ответ юзеру.
- ❌ **Не реализовывать «soft delete» с тегом `archived` для графа.** Это не наша работа — обратимость даёт git, не граф.
- ❌ **Не пытаться переименовать entity_name в самом графе (`amerge_entities`/похожее).** Delete + re-insert через custom KG надёжнее: эмбеддинги пересчитываются, описание обновляется.
- ❌ **Не забудь обновить `IMPLEMENTATION_PLAN.md §H1.entity_name`** если решишь поменять схему именования — H9 завязан на ту же `_entity_name_from_path` функцию.

---

### Phase H — общий DoD (полный, с H9)

- [ ] H1: `vault_to_custom_kg()` корректно строит entities + relationships из frontmatter
- [ ] H2: `index_files`/`full_reindex` используют `ainsert_custom_kg` (no LLM extraction)
- [ ] H3: `FORWARD_RELATIONS` определён в `frontmatter.py` как single source of truth
- [ ] H4: `add_typed_link` запускает фоновую переиндексацию обеих сторон
- [ ] H5: комментарий в `_load_entity_types` обновлён
- [ ] H6: `ontology.py` удалён, scheduled task переименована (Вариант A)
- [ ] H7: тесты конвертера проходят
- [ ] H8: smoke test ручной пройден, LLM extraction не вызывается
- [ ] H9: rename + delete hooks синхронизируют граф, тесты зелёные

---

**Когда всё сделано по плану — обновляй этот файл с пометкой `✅ DONE` напротив каждого пункта DoD. Не удаляй и не реструктурируй сам план.**
