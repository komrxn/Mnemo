# ADR-0001 — Двухслойная память: Transcript + Graph

Status: accepted
Date: 2026-05-12

## Контекст

Текущая «память» Mnemo = vault-граф из типизированных сущностей, который собирается LLM-экстрактором из сырого диалога в конце каждой сессии. Read-path для recall (вспомнить, что юзер сказал) проходит через то же графовое представление.

В сценарии онбординга наблюдён класс багов:

- Юзер пишет «Ресторан БЕК».
- Экстрактор интерпретирует «БЕК» как noise и сохраняет сущность с `canonical_name="ресторан (семейный)"` — литерал утрачен.
- На следующем ходу бот спрашивает имя ресторана повторно. Запрос «что я говорил про ресторан» возвращает только нормализованное «ресторан (семейный)», и бот отвечает: «у меня есть только …».

Корень: read-path памяти проходит через лоссовый LLM-слой. Сырого текста после конца сессии не существует (Redis `session:msgs:*` имеет TTL и не персистится).

## Решение

Память разделяется на два независимых слоя.

### Transcript layer

- Immutable дословный лог каждой сессии.
- Пишется в vault как `90_Transcripts/YYYY/MM/YYYY-MM-DD_<session_id>.md` в момент закрытия сессии.
- Read-only после `seal_session` — extractor / linker / owner_refresh не имеют права редактировать.
- Индексируется в LightRAG как **document-only** (vector store), без custom-KG injection. KG-edges не создаются.
- Тело — plain text без wikilinks; если в исходном сообщении был `[[…]]`-синтаксис, он экранируется.
- Исключён из `vault_map`, чтобы extractor-LLM не видел transcript-ноты как кандидаты на линковку.
- Исключён из Obsidian graph view (через excluded folders в Settings, документируется в README).

### Graph layer

- Существующий vault-граф: типизированные сущности + relations + MOC.
- Производный, мутабельный, лоссовый.
- Может быть пересобран из transcript layer в любой момент (event-sourcing).

### Read-path для recall

При попытке вспомнить literal-значение порядок строго:

1. **Slot** — Redis `session:pending_slot:{session_id}` (M2).
2. **Transcript** — literal substring + semantic search (M1).
3. **Graph** — vault-нота с frontmatter и facts (текущий слой).

### Инварианты

1. Transcript — единственный источник истины для «что было сказано».
2. Graph — единственный источник истины для «как это связано».
3. Граф собирается **только из wikilinks** между сущностями (`20_People`/`30_Jobs`/`40_Projects`/`50_Tasks`/`60_Thoughts`/`70_Memories`/`80_Themes`/`_meta`). `90_Transcripts/` исключён по конвенции.
4. Никакой обязательной миграции `transcript → graph`. Re-extraction — отдельная сознательная операция.
5. Бот не имеет права задать уточняющий вопрос или сказать «не помню», не проведя `recall` по transcript layer (M3).
6. Ответ юзера на прямой вопрос бота попадает в Redis-слот **до** LLM-экстракции (M2).

## Последствия

- Vault растёт линейно с числом сессий (~5–10 MB/мес md). Принимаем — это и есть «вечная память».
- LightRAG индексирует два namespace-а: graph (KG+vector) и transcript (только vector). Размер vector-store растёт; KG не растёт от transcript layer.
- Obsidian graph view остаётся «entity↔entity», не засоряется transcript-нодами.
- Появляется возможность future re-extraction: при улучшении промпта/экстрактора можно прогнать старые transcripts и обогатить граф. Не входит в M0–M6.

## Альтернативы

- **Long-TTL Redis msgs.** Отвергнуто: Redis не персистентный store, рост памяти, нет semantic поиска.
- **Отдельная SQLite/файлы вне vault.** Отвергнуто: ломает «vault — single source of truth», добавляет parallel store.
- **In-vault transcripts с wikilinks к сущностям.** Отвергнуто: засоряет Obsidian graph + LightRAG KG transcript-нодами-сателлитами.

## Связи

- Внедрение: [IMPLEMENTATION_PLAN_V5.md](../../IMPLEMENTATION_PLAN_V5.md) §Memory layers (M0–M6).
- Инварианты переходят в [CLAUDE.md](../../CLAUDE.md) после M6.
