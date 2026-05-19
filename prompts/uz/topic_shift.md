{# DRAFT translation — awaiting native speaker review #}
Aniqla: yangi xabarda suhbat mavzusi avvalgilariga nisbatan o'zgardimi?

## Sessiyaning so'nggi xabarlari
{{ recent_messages }}

## Yangi xabar
{{ new_message }}

---

JSON'ni qat'iy formatda qaytar: {"shift": true, "new_topic": "qisqa tavsif"} yoki {"shift": false, "new_topic": ""}

**shift = true** o'rta YOKI aniq kontekst o'zgarishida:
- Ish mavzusidan shaxsiyga (yoki aksincha)
- Bir loyiha/vazifadan boshqa bog'liq bo'lmaganiga
- Texnik mavzudan hissiy/mulohazaviyga
- **Yangi sub'ekt kiritildi** (loyiha, odam, mavzu), avval sessiyada muhokama qilinmagan
- Foydalanuvchi o'zi almashishni bildiradi: "aytmoqchi", "umuman", "yana", "boshqa savol"

**shift = false**:
- Foydalanuvchi shu fikrni boshqa burchakdan rivojlantiryapti (bir loyiha haqida yangi fakt emas)
- Oldingi gapning aniqlashtirilishi yoki davomi
- Sessiyaning birinchi 3 xabari (kontekst yetarli emas)
- Bot savoliga qisqa javob ("ok", "ha", "tushundim") — bu mavzu o'zgarishi emas, bu nitni yopish

Tamoyil: yangi xabarda foydalanuvchining **diqqat ob'ekti** boshqa bo'lsa — bu shift. Bot uning ortidan borishi kerak, orqaga tortmasligi.
