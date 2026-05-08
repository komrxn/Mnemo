# MCP Integration — Manual Test Checklist

## Setup

1. `./setup.sh` → creates `secrets/`, `data/`, and `secrets/lightrag_api_key.txt`
2. Fill `.env` with `TELEGRAM_BOT_TOKEN`, `OPENAI_API_KEY`, `ALLOWED_USER_IDS`
3. `docker compose up -d` → three services running: `bot`, `lightrag-api`, `redis`
4. Send a few messages to the bot via Telegram to populate the graph

## API server health

- [ ] `curl http://localhost:9621/health` → 200 (whitelisted, no auth required)
- [ ] `curl http://localhost:9621/graphs?label=mnemo` без ключа → 401 or 403 (auth blocked)
- [ ] `curl -H "X-API-Key: $(cat secrets/lightrag_api_key.txt)" http://localhost:9621/graph/label/list` → 200 (returns label list, may be empty)
- [ ] `curl -H "X-API-Key: WRONG" http://localhost:9621/graphs?label=mnemo` → 401 or 403 (auth blocked)

## MCP client

- [ ] Install from source succeeds:
  ```bash
  git clone https://github.com/desimpkins/daniel-lightrag-mcp.git
  cd daniel-lightrag-mcp && pip install -e .
  ```
- [ ] `daniel-lightrag-mcp --help` runs without errors
- [ ] Claude Code config added to `~/.claude/claude_mcp_config.json` with `LIGHTRAG_BASE_URL` → restart → MCP shows green status
- [ ] In Claude Code: ask *"what is in my brain about \<topic you mentioned to bot\>"* → response references actual entities from graph

## Concurrent access safety

- [ ] Send a message to the bot, immediately query via Claude Code → no errors on either side
- [ ] Bot logs show no `pickle.UnpicklingError` or JSON decode errors during concurrent access
- [ ] After ~5 minutes, Claude Code query returns newly inserted entities

## Idempotency

- [ ] Run `./setup.sh` a second time → no errors, existing key not overwritten
- [ ] `docker compose up -d` after restart → `lightrag-api` healthcheck passes within 90 seconds

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

## Phase H9: rename hook sanity check

- [ ] Создать заметку через бота: «у меня появилась новая работа в LegAI»
- [ ] Подождать индексации (~3 сек после `apply_to_vault`)
- [ ] Запросить через MCP: «what is in my brain about LegAI?» → ответ ссылается на entity `30_Jobs/legai`
- [ ] Через бота: «переименуй legai в legai-corp»
- [ ] Подождать ~5 сек
- [ ] Запросить тот же запрос → ответ ссылается на entity `30_Jobs/legai-corp`
- [ ] **НЕ должна** в ответе появляться старая `30_Jobs/legai` — если появилась, значит H9 не отработал
- [ ] Открыть `data/lightrag/graph_chunk_entity_relation.graphml` — ноды `legai` НЕ должно быть, есть только `legai-corp`

## Sanity check after Phase G fixes

Run through this BEFORE declaring Phase F + G done.

### Build & start

- [ ] `./setup.sh` на чистом репо проходит без ошибок
- [ ] `docker compose up -d --build` поднимает 3 контейнера
- [ ] `docker compose ps` показывает `bot (healthy)`, `lightrag-api (healthy)`, `redis (healthy)` через ≤ 2 минуты

### Auth

- [ ] Все 4 curl-проверки из раздела «API server health» пройдены

### MCP install

- [ ] `git clone https://github.com/desimpkins/daniel-lightrag-mcp.git && cd daniel-lightrag-mcp && pip install -e .` проходит
- [ ] `daniel-lightrag-mcp --help` запускается

### MCP query

- [ ] Claude Code с конфигом из README подключается к серверу (зелёный статус)
- [ ] Запрос «what's in my brain about <topic>» возвращает ответ из графа

### Tests

- [ ] `uv run pytest -q tests/` — все зелёные

### Docs honesty

- [ ] Поиск по README + docs/ слов `LIGHTRAG_API_URL`, `pip install daniel-lightrag-mcp`, `/graphs/labels` — везде пусто
