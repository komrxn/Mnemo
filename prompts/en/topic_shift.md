Determine: has the conversation topic shifted in the new message relative to previous ones?

## Recent session messages
{{ recent_messages }}

## New message
{{ new_message }}

---

Return JSON strictly in this format: {"shift": true, "new_topic": "short description"} or {"shift": false, "new_topic": ""}

**shift = true** only on explicit context change:
- From work topic to personal and vice versa
- From one project/task to another unrelated one
- From technical topic to emotional/reflective

**shift = false** on:
- Continuing the same topic from a different angle
- Minor switching within one context
- Clarifying or developing a previous thought
- First 3 messages of the session (too little context)
