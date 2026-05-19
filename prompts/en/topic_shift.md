Decide: did the topic shift in the new message vs. the previous ones.

## Recent session messages
{{ recent_messages }}

## New message
{{ new_message }}

---

Return strict JSON: {"shift": true, "new_topic": "short description"} or {"shift": false, "new_topic": ""}

**shift = true** on MEDIUM or EXPLICIT context change:
- From work topic to personal (or vice versa)
- From one project/task to an unrelated one
- From technical to emotional/reflective topic
- **A new entity is introduced** (project, person, theme) not previously discussed in the session
- User explicitly signals a switch: "by the way", "also", "another thing", "different question"

**shift = false** on:
- User developing the same thought from a different angle (not just a new fact about the same project)
- Clarification or continuation of the previous turn
- First 3 messages of the session (too little context to judge)
- A short answer to the bot's question ("ok", "yeah", "got it") — that's not a topic shift, that's closing the thread

Principle: if in the new message the user has a different **object of attention** — it's a shift. The bot should follow, not drag them back.
