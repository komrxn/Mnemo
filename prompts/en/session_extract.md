Analyze the session dialog and extract structured entities to write into Obsidian.

# Result fields

- **summary**: 2-3 sentences, the essence of the session
- **topic**: 3-5 words, the main topic
- **entities**: list of entities mentioned in the dialog, see schema below
- **thoughts**: atomic thoughts worthy of separate notes (use `entity type=thought`)
- **memories**: long-term facts to remember (`entity type=memory`)
- **links_to_create**: pairs `[from_path, to_path]` for untyped wikilinks in the body — use rarely, only when the link matters but doesn't fit any typed field
- **open_questions**: only questions **directly raised AND not answered** in the dialog. If the bot asked about X and the user replied — the question is CLOSED, do NOT include it in open_questions. If the bot didn't ask and the user just told a story — open_questions is empty. It's NOT a list of "what else to find out", it's "what's still hanging"

# Seven entity types and their genres (CRITICAL — follow strictly)

`person`, `project`, `task`, `job`, `theme`, `memory`, `thought`.

| Type | Folder | Genre (WHAT this IS) | ❌ DON'T put | ✅ Correct |
|---|---|---|---|---|
| **person** | 20_People/ | One specific human | "family", "team", "colleagues" (these are groups, not entities) | "Anna", "Zakhir", "mom" |
| **job** | 30_Jobs/ | One organization / workplace / school | "work" (vague), "career", "work experience" | "LegAI", "IT Park University", "BEK restaurant" |
| **project** | 40_Projects/ | One product/project | "AI projects" (collective bucket), "work things" | "LegAI MVP", "Mnemo", "TN VED Assistant" |
| **task** | 50_Tasks/ | One concrete actionable item with deadline | "study AI" (that's a theme), "self-improve" (goal) | "deploy Mnemo MVP by June 15", "write LegAI promo" |
| **thought** | 60_Thoughts/ | **One** thought/insight/idea, 1-2 sentences; **+ goals (life/career goals)** | biography, session summary, list of facts, character description | "AI market for CIS underserved", "voice > keyboard for capture", "**want to make Forbes 30 under 30**" |
| **memory** | 70_Memories/ | **Concrete event or fact** (when/where/what) | biography ("profile of X"), characterization ("work style"), interest list, life summary | "moved to Lisbon 2024", "started dating Anya 11.11.2024", "applied to Hangzhou Dianzi 2026" |
| **theme** | 80_Themes/ | Area of interest / direction in life / context | one project (that's project), specific goal (that's thought), goal | "AI development", "Chinese education", "health and sport" |

**Rules:**
1. If an entity doesn't fit any type clearly — **don't create it**. Don't dump into inbox for the sake of completeness. A mention in open_questions is better.
2. **Goals (life/career goals) are `thought`, not `theme`.** "Forbes 30 under 30" as a goal → thought. "Career ambitions in general" as an area → theme.
3. **The owner's biography goes into owner.md, not into memory.** Don't create memory like "profile of X" / "background of Y" / "about the user" — this synthesis is done by owner.md auto-refresh.
4. **Memory must have a time marker** (year/month/"this year") when possible. Without it — likely a thought, a theme, or not needed at all.

# Typed link semantics

Every entity except `person` gets `"owner": ["_meta/owner.md"]` in `typed_links` — it's part of the owner's life. Person is transitively linked via `works_at`/`about_person`/`related_people` from other entities.

| Link | From (source) | To (target) | Semantics | Single or list |
|---|---|---|---|---|
| `owner` | job/project/task/thought/memory/theme | _meta/owner.md | "This is part of my life". Always. | single |
| `works_at` | person | job | "X works at Y" | single |
| `for_job` | project/task | job | "X is done within job Y" | single |
| `for_project` | task/thought/memory | project | "X relates to project Y" | single |
| `themes` | project/thought/memory | theme | "X is about themes [Y, Z]" | list |
| `about_person` | thought/memory | person | "This is about person Y" | single |
| `related_people` | project/job/memory | person | "People mentioned in X" | list |
| `parent_theme` | theme | theme | "X is sub-theme of Y" (hierarchy) | single |

# Entity fields

- `type`: person | project | task | job | theme | memory | thought
- `name`: canonical name (short, concrete, natural language).
  **Descriptive third-person phrases are forbidden.** ❌ "user has pet projects", ❌ "user wants to study in China", ❌ "interest in hangzhou dianzi university". ✅ "pet projects", ✅ "study in China", ✅ "hangzhou dianzi university". The name is **what the note is about**, not a retelling of the situation.
- `aliases`: all variants mentioned in this session ("legai", "the company")
- `new_facts`: list of new facts from this session (bullets)
- `updates`: status/state changes
- `due`: deadline `YYYY-MM-DD` (tasks only)
- `status`: open | done | archived (tasks only)
- `typed_links`: **list of objects** `{field, target}`. Each object is one typed link from entity to target note. If no links — empty list.

`field` — link field name: `owner`, `works_at`, `for_job`, `for_project`, `themes`, `about_person`, `related_people`, `parent_theme`.

`target` — vault-relative path to target note (with `.md`).

Multi-target links (`themes`, `related_people`) — separate objects in the list.

Example entity:
```json
{
  "type": "project",
  "name": "Mnemo MVP",
  "aliases": ["MVP", "first launch"],
  "new_facts": ["deadline June 15", "main feature — voice → vault"],
  "typed_links": [
    {"field": "owner", "target": "_meta/owner.md"},
    {"field": "for_job", "target": "30_Jobs/legai.md"},
    {"field": "themes", "target": "80_Themes/ai.md"},
    {"field": "themes", "target": "80_Themes/startups.md"},
    {"field": "related_people", "target": "20_People/anna.md"}
  ]
}
```

# Principles

1. **Link by meaning.** If in the dialog Anna is mentioned as a LegAI employee — her entity has `typed_links: [{"field": "works_at", "target": "30_Jobs/legai.md"}]`. If there's a memory about her as a friend — memory has `[{"field": "about_person", "target": "20_People/anna.md"}]`.

2. **All entities except person get owner.** In `typed_links` for jobs/projects/tasks/thoughts/memories/themes there must be an object `{"field": "owner", "target": "_meta/owner.md"}`. For person — there must NOT.

3. **No duplicates.** If a person/project/theme is mentioned in several roles — it's ONE entity, all links go through different fields.

   **This also applies to theme synonyms.** "AI" = "Artificial Intelligence" = "AGI" — that's ONE theme. If the dialog uses different phrasings of one theme — pick ONE canonical (short) and put the rest in `aliases`. Do NOT create parent-theme/sub-theme from synonyms of the same thing. Same for "studying in China" = "education in China" = "Chinese education" — one meaning, one note.

4. **Quality of names.** Short, concrete. Themes — minimum 2 words unless the theme is a concrete term/technology/proper name (Claude, React, AI, GPT-5.4). "Work" as a theme — not allowed. "Working in a restaurant" — not allowed (that's already a job entity). Themes are about areas of interest ("AI development", "health and sport", "relationships").

5. **Theme hierarchy.** If a new theme is a special case of an existing theme (already in vault or created in this same session) — the new theme has `typed_links: [{"field": "parent_theme", "target": "80_Themes/parent.md"}]`. Example: a "Claude" theme appeared, "Development" already exists → Claude's `parent_theme` points to `80_Themes/development.md`.

6. **Don't guess links.** If you don't understand how to link an entity — leave it without ambiguous links (only `owner` if applicable). Smart linker (separate post-pass) will pick up the missed ones.

7. **Don't make things up.** Include only what's explicitly said or directly follows from the dialog.

8. **new_facts must be about the entity in `name`.** If facts about a different entity appear in the dialog — **create a separate entity** for them. Don't put trout-farming facts on a "bachelor's degree" entity. Don't put project-LegAI facts on the "Anna" entity (for Anna — use `works_at`; LegAI facts go on a separate project entity). Cross-contamination is the #1 cause of a polluted graph.

9. **Quality of new_facts — specifics, not filler.** Each fact is a separate substantive unit. DO NOT write generic placeholders like "Komron's work project", "Important person", "Context related to X". This is embarrassing. If an entity has no concrete facts from the dialog — leave `new_facts: []` or mark it for follow-up in `open_questions`.

# If the session is empty or technical
Return empty arrays `entities: []`, `thoughts: []`, etc.
