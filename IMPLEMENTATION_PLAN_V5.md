# IMPLEMENTATION_PLAN_V5 — i18n (ru/en/uz) + bot commands list

> Дополнительный план к v4. Локализация всего пользовательского текста и AI-вывода
> на три языка: русский, английский, узбекский (латиница). Плюс live-список команд
> в Telegram через `set_my_commands`.

---

## 1. Цели

- Бот общается с юзером на одном из трёх языков: **ru / en / uz** (узб. латиница).
- AI пишет заметки в vault на выбранном языке (независимый параметр от UI).
- Юзер выбирает UI-язык первой командой `/start` через inline-keyboard.
- Юзер выбирает язык заметок отдельным шагом в онбординге.
- Юзер может переключить любой из языков через `/lang`.
- В Telegram при наборе `/` виден полный список команд с описанием на UI-языке.
- Существующие онбординг-нутые юзеры мигрируют в `ru/ru` автоматически.

## 2. Не-цели

- ❌ Не локализуем имена папок vault (`20_People`, `30_Jobs`, ...) — они уже нейтральные/английские, контракт кода.
- ❌ Не локализуем имена MOC-файлов (`MOC_People.md`, `MOC_Projects.md`) — тот же контракт.
- ❌ Не локализуем enum-значения во frontmatter (`type: project`, `status: in-development`).
- ❌ Не переименовываем `_meta/owner.md` / `_meta/portrait.md`.
- ❌ Не делаем смешанные языки в одной заметке.
- ❌ Не пишем свой gettext — используем простой dict + jinja2.
- ❌ Не добавляем других языков (узб. кириллица, казахский, …) — это open question.

## 3. Архитектура

### 3.1 Профиль юзера в Redis

Текущий профиль (dict в `user:profile:{user_id}`) расширяется двумя полями:

```python
{
  "bot_name": "...",       # уже есть
  "owner_name": "...",     # уже есть
  "personality": 1,        # уже есть (1..4)
  "ui_language": "ru",     # НОВОЕ: ru | en | uz
  "notes_language": "ru",  # НОВОЕ: ru | en | uz
}
```

`ui_language` контролирует: все сообщения бота в TG, кнопки, ошибки, команд-лист.
`notes_language` контролирует: язык контента заметок (one_liner, facts), заголовки секций (`## Факты` / `## Facts` / `## Faktlar`), язык работы агента (system prompt + extractor).

### 3.2 Файлы

```
src/i18n.py                 # t(key, lang, **vars) — единая точка
src/locales/
├── ru.yaml                 # ~275 ключей UI-текстов
├── en.yaml
└── uz.yaml

prompts/
├── ru/                     # переехало из prompts/*.md
│   ├── system.md
│   ├── onboarding.md
│   ├── session_extract.md
│   ├── topic_shift.md
│   └── proactive.md
├── en/   (5 файлов)
└── uz/   (5 файлов)

prompts/section_headers.yaml  # ## Факты / ## Facts / ## Faktlar
```

Старые `prompts/*.md` удаляются — `agent/prompts.py` теперь умеет про язык.

### 3.3 i18n API

```python
# src/i18n.py
def t(key: str, lang: str = "ru", **vars: object) -> str:
    """Lookup key in locales/{lang}.yaml. Fallback: lang → ru → key itself.

    Supports dot.path keys (e.g. "onboarding.welcome").
    Vars interpolated via jinja2 (safe escaping disabled — это plain text).
    """
```

Поведение фоллбэка:
1. Если в `lang.yaml` ключ есть — берём.
2. Если нет — берём из `ru.yaml` (язык-донор).
3. Если и там нет — возвращаем сам ключ (виден в проде, легко чинить).

### 3.4 Промпт-loader

```python
# src/agent/prompts.py — обновляется
def render(name: str, lang: str = "ru", **kwargs) -> str:
    """Render prompts/{lang}/{name}.md. Fallback: lang → ru."""
```

Все вызовы `prompts.render("system", ...)` → `prompts.render("system", lang=profile["notes_language"], ...)`.

### 3.5 Bot commands list

В `main.py` после `create_bot_and_dispatcher()`:

```python
from src.telegram.commands_meta import register_bot_commands
await register_bot_commands(bot)
```

`register_bot_commands` вызывает `bot.set_my_commands(...)` три раза:
- scope=BotCommandScopeAllPrivateChats, language_code="ru"
- scope=BotCommandScopeAllPrivateChats, language_code="en"
- scope=BotCommandScopeAllPrivateChats, language_code="uz"

Telegram сам подберёт юзеру правильный список по его клиентским настройкам. Это работает **независимо от** `ui_language` — Telegram не знает наш профиль.

Также в `bot.set_my_description()` / `bot.set_my_short_description()` — три варианта.

## 4. Flow

### 4.1 Новый юзер

```
1. /start (или любое сообщение)
2. Бот: inline-keyboard "🇷🇺 Русский · 🇬🇧 English · 🇺🇿 O'zbekcha"
3. Юзер выбирает → ui_language сохраняется в профиле
4. Старт обычного онбординга на выбранном языке:
   - имя бота
   - стиль (4 кнопки)
   - имя юзера
   - ──НОВОЕ── язык заметок (ещё одна inline-keyboard)
   - portrait
   - clarifications
   - готово
```

### 4.2 Существующий юзер (мигрированный)

При первом обращении после деплоя:
- Profile есть, `ui_language` нет → миграция автоматом: `ui_language=ru`, `notes_language=ru`.
- Дальше работает как раньше. Никаких уведомлений.
- Может переключить через `/lang`.

### 4.3 `/lang`

```
/lang
→ Бот: «Текущий: UI=русский, заметки=русский. Что меняем?»
→ inline-keyboard: [UI язык] [Язык заметок] [Отмена]
→ выбор → второй inline-keyboard с тремя флагами → обновление профиля → подтверждение
```

## 5. Что локализуется и что нет (полная таблица)

| Объект | UI-язык / notes-язык / нет |
|---|---|
| Сообщения бота в TG (ответы, ошибки, кнопки) | UI |
| Bot commands list (`/save`, `/undo`, ...) | UI (через telegram language_code) |
| Inline-keyboard кнопки | UI |
| Подтверждения (`Подтверждено` / `Отменено`) | UI |
| Promproactive сообщения от scheduler (морнинг-дайджест, нудж) | UI |
| One-liner заметки | notes |
| Facts в заметке | notes |
| Заголовки секций (`## Факты`, `## Связи`) | notes |
| MOC-content (intro, table headers внутри MOC) | notes |
| `_meta/owner.md` text content | notes |
| System prompt агента | notes |
| Extractor prompt | notes |
| Topic-shift prompt | notes (mini-модель тоже на нужном языке) |
| Owner-refresh prompt | notes |
| Имена папок (`20_People`) | нет (нейтральные) |
| Имена MOC-файлов (`MOC_People.md`) | нет (нейтральные) |
| Frontmatter ключи (`owner`, `for_job`, ...) | нет (контракт кода) |
| Enum-значения (`type: project`) | нет (контракт кода) |
| Slugify имена заметок | автоматом из контента (uz-latin даёт чистые slugs) |

## 6. Файлы которые меняются

```
src/main.py                                  ← вызов register_bot_commands
src/telegram/bot.py                          ← подтверждения через t()
src/telegram/commands_meta.py                ← НОВЫЙ: set_my_commands
src/telegram/handlers/commands.py            ← все строки через t(), новая команда /lang
src/telegram/handlers/text.py                ← все строки через t(); UI lang picker; notes lang picker
src/telegram/handlers/voice.py               ← строки через t()
src/telegram/handlers/photo.py               ← строки через t()
src/agent/prompts.py                         ← lang-параметр
src/agent/extractor.py                       ← lang из профиля
src/agent/loop.py                            ← lang из профиля
src/agent/owner_refresh.py                   ← lang из профиля
src/session/topic_shift.py                   ← lang из профиля
src/scheduler/triggers.py                    ← lang из профиля + UI strings
src/scheduler/defaults.py                    ← UI strings
src/tools/obsidian.py                        ← заголовки секций через section_headers.yaml
src/tools/scheduler.py                       ← UI strings
src/tools/misc.py                            ← UI strings
src/tools/lightrag.py                        ← UI strings
src/safety/confirmations.py                  ← UI strings
src/vault/writer.py                          ← заголовки через section_headers
src/vault/linking.py                         ← заголовок ## Связи через section_headers
src/i18n.py                                  ← НОВЫЙ
src/locales/{ru,en,uz}.yaml                  ← НОВЫЕ
prompts/{ru,en,uz}/*.md                      ← перемещение + переводы
prompts/section_headers.yaml                 ← НОВЫЙ
tests/test_i18n.py                           ← НОВЫЙ
```

## 7. Этапы (по порядку)

1. **i18n каркас** — `src/i18n.py` + три пустых yaml + тест.
2. **Профиль** — `ui_language` и `notes_language` геттеры + миграция «нет поля → ru».
3. **Bot commands list** — `commands_meta.py` + регистрация в `main.py`. Тест: команды видны в TG.
4. **`/lang` команда** — handler + два inline-keyboard step'а.
5. **UI lang picker первым шагом /start** — переделать `_start_onboarding`.
6. **Notes lang picker в онбординге** — отдельный шаг.
7. **Миграция строк handlers** — `commands.py`, `text.py`, `voice.py`, `photo.py`.
8. **Миграция строк scheduler / tools / safety** — оставшиеся 100+ строк.
9. **Локализация заголовков** — `section_headers.yaml`, обновить writer + linking.
10. **Промпты × 3 языка** — переезд + переводы.
11. **Prompt loader lang-aware** — обновить `agent/prompts.py` и все вызовы.
12. **Тесты** — t() fallback, profile migration, /lang flow, prompt loader.

## 8. Тесты (минимум)

- `t("foo.bar", "en")` → english, fallback на ru при отсутствии, на key при отсутствии в обоих.
- `t("with.var", "ru", name="X")` → подстановка jinja2.
- Profile migration: profile без `ui_language` → автоматом ru при чтении.
- `/lang` flow: смена `ui_language` → следующий ответ на новом языке.
- `prompts.render("system", lang="en")` → читает `prompts/en/system.md`, fallback на ru.

## 9. Открытые вопросы

- **Узбекские переводы** — черновой перевод сделает Sonnet, носитель ревьюнет позже. До ревью узб. промпты помечены как `# DRAFT` в комментарии.
- **Bot commands description length** — Telegram ограничивает 256 символов. Все три перевода должны влезть.
- **Voice messages на узб./англ.** — Whisper поддерживает оба, проверить smoke-тестом.

## 10. Что НЕ делаем (явный список)

- Не локализуем CLAUDE.md / README.md / другие dev-доки.
- Не делаем UI для админа (управление переводами через файлы).
- Не делаем lazy-loading переводов (всё в память при старте).
- Не делаем pluralization (`1 заметка / 2 заметки / 5 заметок`) — все строки в форме «N: %d».
- Не делаем RTL (узбекский латиницей — LTR).
