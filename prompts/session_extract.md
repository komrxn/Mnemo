Проанализируй диалог сессии и извлеки структурированные сущности для записи в Obsidian.

# Поля результата

- **summary**: 2-3 предложения, суть сессии
- **topic**: 3-5 слов, главная тема
- **entities**: список сущностей упомянутых в диалоге, см. схему ниже
- **thoughts**: атомарные мысли заслуживающие отдельной заметки (используй `entity type=thought`)
- **memories**: долгосрочные факты которые нужно помнить (`entity type=memory`)
- **links_to_create**: пары `[from_path, to_path]` для нетипизированных wikilink'ов в теле — используй редко, только когда связь важна но не подходит ни под один типизированный field
- **open_questions**: незакрытые темы для follow-up

# Семь типов сущностей

`person`, `project`, `task`, `job`, `theme`, `memory`, `thought`.

Расположение по папкам определяется автоматически:

| Тип | Папка |
|---|---|
| person | 20_People/ |
| job | 30_Jobs/ |
| project | 40_Projects/ |
| task | 50_Tasks/ |
| thought | 60_Thoughts/ |
| memory | 70_Memories/ |
| theme | 80_Themes/ |

# Семантика типизированных связей

Каждая сущность кроме `person` получает `"owner": ["_meta/owner.md"]` в `typed_links` — она часть жизни владельца. Person — транзитивно через `works_at`/`about_person`/`related_people` от других сущностей.

| Связь | Где (источник) | Куда (цель) | Семантика | Single или list |
|---|---|---|---|---|
| `owner` | job/project/task/thought/memory/theme | _meta/owner.md | «Это часть моей жизни». Всегда. | single |
| `works_at` | person | job | «X работает в Y» | single |
| `for_job` | project/task | job | «X делается в рамках работы Y» | single |
| `for_project` | task/thought/memory | project | «X относится к проекту Y» | single |
| `themes` | project/thought/memory | theme | «X — про темы [Y, Z]» | list |
| `about_person` | thought/memory | person | «Это про человека Y» | single |
| `related_people` | project/job/memory | person | «В X упоминаются люди» | list |
| `parent_theme` | theme | theme | «X — подтема Y» (для иерархии) | single |

# Поля entity

- `type`: person | project | task | job | theme | memory | thought
- `name`: каноническое имя (короткое, конкретное, на естественном языке)
- `aliases`: все варианты упоминания в этой сессии («ЛегАИ», «legai», «контора»)
- `new_facts`: список новых фактов из этой сессии (буллетами)
- `updates`: изменения статуса/состояния
- `due`: дедлайн `YYYY-MM-DD` (только для tasks)
- `status`: open | done | archived (только для tasks)
- `typed_links`: dict с типизированными связями. Формат: `{"field_name": ["target_path1.md", "target_path2.md"]}`. **Single-value** поля (`owner`, `works_at`, `for_job`, `for_project`, `about_person`, `parent_theme`) — список из ОДНОГО элемента. **List-value** поля (`themes`, `related_people`) — список из 0+ элементов.

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

2. **Все entities кроме person получают owner.** В `typed_links` у jobs/projects/tasks/thoughts/memories/themes должно быть `"owner": ["_meta/owner.md"]`. У person — НЕ должно.

3. **Дублей нет.** Если человек/проект/тема упомянут в нескольких ролях — это ОДНА entity, все связи к ней проставляются через разные поля.

4. **Качество имён.** Короткие, конкретные. Темы — минимум 2 слова кроме случаев когда тема — конкретный термин/технология/имя собственное (Claude, React, AI, GPT-5.4). «Работа» как тема — нельзя. «Работа в ресторане» — нельзя (это уже job-сущность). Темы — про области интересов («разработка с AI», «здоровье и спорт», «отношения»).

5. **Иерархия тем.** Если новая тема — частный случай существующей темы (которая уже есть в vault или создаётся в этой же сессии) — у новой темы `typed_links: {"parent_theme": ["80_Themes/parent.md"]}`. Пример: появилась тема «Claude», уже есть «Разработка» → у Claude `parent_theme: "80_Themes/разработка.md"`.

6. **Не угадывай связи.** Если не понимаешь как связать сущность — оставь её без неоднозначных связей (только `owner` если применимо). Smart linker (отдельный пост-проход) подтянет упущенное.

7. **Не выдумывай.** Включай только то что явно сказано или прямо следует из диалога.

# Если сессия пустая или техническая
Возвращай пустые массивы `entities: []`, `thoughts: []`, etc.
