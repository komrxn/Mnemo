{# DRAFT translation — awaiting native speaker review #}
Sessiya muloqotini tahlil qil va Obsidian'ga yozish uchun tuzilgan mavjudotlarni ajratib ol.

# Natija maydonlari

- **summary**: 2-3 jumla, sessiya mohiyati
- **topic**: 3-5 so'z, asosiy mavzu
- **entities**: muloqotda eslatilgan mavjudotlar ro'yxati, sxema quyida
- **thoughts**: alohida yozuvga arzigulik atom fikrlar (`entity type=thought` ishlat)
- **memories**: eslab qolish kerak bo'lgan uzoq muddatli faktlar (`entity type=memory`)
- **links_to_create**: body'dagi tipsiz wikilinklar uchun `[from_path, to_path]` juftliklari — kamdan-kam ishlat, faqat bog'lanish muhim bo'lib biror tipli maydonga to'g'ri kelmasa
- **open_questions**: faqat muloqotda **to'g'ridan-to'g'ri ko'tarilgan VA javob OLINMAGAN** savollar. Agar bot X haqida so'ragan va foydalanuvchi javob bergan bo'lsa — savol YOPIQ, uni open_questions'ga KIRITMA. Agar bot so'ramagan va foydalanuvchi shunchaki gapirayotgan bo'lsa — open_questions bo'sh. Bu "yana nima bilib olish kerak" ro'yxati EMAS, bu "nima osilib qolgan"

# Yetti mavjudot turi va ularning janrlari (MUHIM — qat'iy rioya qil)

`person`, `project`, `task`, `job`, `theme`, `memory`, `thought`.

| Tur | Papka | Janr (BU nima) | ❌ QO'YMA | ✅ To'g'ri |
|---|---|---|---|---|
| **person** | 20_People/ | Bitta aniq odam | "oila", "jamoa", "hamkasblar" (bular ro'yxatlar, mavjudotlar emas) | "Anna", "Zaxir", "ona" |
| **job** | 30_Jobs/ | Bitta tashkilot / ish joyi / o'quv | "ish" (umumiy), "karyera", "ish tajribasi" | "LegAI", "IT Park University", "BEK restoran" |
| **project** | 40_Projects/ | Bitta mahsulot/loyiha | "AI loyihalar" (to'plam), "ishlar" | "LegAI MVP", "Mnemo", "TN VED Assistant" |
| **task** | 50_Tasks/ | Bitta aniq muddatli vazifa | "AI o'rganish" (bu mavzu), "rivojlanish" (maqsad) | "Mnemo MVP'ni 15 iyungacha deploy", "LegAI promo yozish" |
| **thought** | 60_Thoughts/ | **Bitta** fikr/insight/g'oya, 1-2 jumla; **+ maqsadlar (hayot/karyera maqsadlari)** | biografiya, sessiya summary, faktlar ro'yxati, xarakter tavsifi | "MDH AI bozori to'la emas", "**Forbes 30 ga kirishni xohlayman**" |
| **memory** | 70_Memories/ | **Aniq voqea yoki fakt** (qachon/qayerda/nima) | biografiya ("X profili"), tavsif ("ish uslubi"), qiziqishlar ro'yxati, hayot summary | "2024-da Lissabonga ko'chish", "11.11.2024-da Anya bilan munosabat boshlash", "2026-da Hangzhou Dianzi'ga ariza" |
| **theme** | 80_Themes/ | Qiziqish sohasi / hayot yo'nalishi / kontekst | bitta loyiha (bu project), aniq maqsad (bu thought), maqsad | "AI ishlab chiqish", "Xitoyda ta'lim", "sog'liq va sport" |

**Qoidalar:**
1. Agar mavjudot biror turga aniq to'g'ri kelmasa — **yaratma**. Inbox'ga galochka uchun tashlama. open_questions'da eslatish yaxshiroq.
2. **Maqsadlar (hayot/karyera maqsadlari) — `thought`, theme EMAS.** "Forbes 30" maqsad sifatida → thought. "Karyera ambitsiyalari" soha sifatida → theme.
3. **Egasining biografiyasi owner.md'ga ketadi, memory'ga emas.** "X profili" / "Y fonik" / "foydalanuvchi haqida" turidagi memory yaratma — bu sintezni owner.md'ning auto-refresh'i o'zi qiladi.
4. **Memory'da vaqt belgisi bo'lishi kerak** (yil/oy/"shu yil") iloji bo'lsa. Usiz — odatda thought, theme yoki umuman kerak emas.

# Tipli bog'lanish semantikasi

`person` dan tashqari har bir mavjudot `typed_links`'da `"owner": ["_meta/owner.md"]` oladi — u egasining hayotining qismi. Person boshqa mavjudotlardan `works_at`/`about_person`/`related_people` orqali tranzitiv bog'lanadi.

| Bog'lanish | Qayerdan (manba) | Qayerga (maqsad) | Semantika | Single yoki list |
|---|---|---|---|---|
| `owner` | job/project/task/thought/memory/theme | _meta/owner.md | "Bu mening hayotimning qismi". Doim. | single |
| `works_at` | person | job | "X Y'da ishlaydi" | single |
| `for_job` | project/task | job | "X Y ishi doirasida bajariladi" | single |
| `for_project` | task/thought/memory | project | "X Y loyihasiga tegishli" | single |
| `themes` | project/thought/memory | theme | "X [Y, Z] mavzular haqida" | list |
| `about_person` | thought/memory | person | "Bu Y haqida" | single |
| `related_people` | project/job/memory | person | "X'da eslatilgan odamlar" | list |
| `parent_theme` | theme | theme | "X Y'ning ostki mavzusi" (iyerarxiya) | single |

# Entity maydonlari

- `type`: person | project | task | job | theme | memory | thought
- `name`: kanonik nom (qisqa, aniq, tabiiy tilda).
  **3-shaxsdagi tasviriy iboralar TAQIQLANGAN.** ❌ "foydalanuvchining pet projects bor", ❌ "foydalanuvchi Xitoyda o'qishni xohlaydi". ✅ "pet projects", ✅ "Xitoyda o'qish". Nom — bu **yozuv nima haqida**, vaziyat qaytarib aytilishi emas.
- `aliases`: bu sessiyada eslatilgan barcha variantlar ("legai", "kompaniya")
- `new_facts`: bu sessiyadan yangi faktlar ro'yxati (belgi-belgi)
- `updates`: holat o'zgarishlari
- `due`: muddat `YYYY-MM-DD` (faqat tasks uchun)
- `status`: open | done | archived (faqat tasks uchun)
- `typed_links`: **obyektlar ro'yxati** `{field, target}`. Har bir obyekt — entity'dan target-yozuvga bitta tipli bog'lanish. Bog'lanish yo'q bo'lsa — bo'sh ro'yxat.

`field` — bog'lanish maydon nomi: `owner`, `works_at`, `for_job`, `for_project`, `themes`, `about_person`, `related_people`, `parent_theme`.

`target` — target yozuvga vault-nisbiy yo'l (`.md` bilan).

Ko'p maqsadli bog'lanishlar (`themes`, `related_people`) — ro'yxatdagi alohida obyektlar.

Entity misoli:
```json
{
  "type": "project",
  "name": "Mnemo MVP",
  "aliases": ["MVP", "birinchi ishga tushirish"],
  "new_facts": ["15 iyun muddati", "asosiy funksiya — ovoz → vault"],
  "typed_links": [
    {"field": "owner", "target": "_meta/owner.md"},
    {"field": "for_job", "target": "30_Jobs/legai.md"},
    {"field": "themes", "target": "80_Themes/ai.md"},
    {"field": "themes", "target": "80_Themes/startaplar.md"},
    {"field": "related_people", "target": "20_People/anna.md"}
  ]
}
```

# Tamoyillar

1. **Ma'no bo'yicha bog'la.** Agar muloqotda Anna LegAI xodimi sifatida eslatilgan bo'lsa — uning entity'sida `typed_links: [{"field": "works_at", "target": "30_Jobs/legai.md"}]`. Agar u haqida do'sti sifatida memory bo'lsa — memory'da `[{"field": "about_person", "target": "20_People/anna.md"}]`.

2. **Person'dan tashqari barcha entity'lar owner oladi.** Jobs/projects/tasks/thoughts/memories/themes'ning `typed_links`'ida `{"field": "owner", "target": "_meta/owner.md"}` obyekti bo'lishi KERAK. Person'da — BO'LMASLIGI KERAK.

3. **Dublikatlar yo'q.** Odam/loyiha/mavzu bir nechta rolda eslatilsa — bu BITTA entity, hamma bog'lanishlar har xil maydonlar orqali qo'yiladi.

   **Bu mavzu sinonimlariga ham tegishli.** "AI" = "Sun'iy intellekt" = "SI" — bu BITTA mavzu. Muloqotda bir mavzuning turli ifodalari ishlatilsa — BITTA kanoniyni (qisqasini) tanla va qolganlarini `aliases`'ga qo'y. Bir narsaning sinonimlaridan parent-theme/sub-theme YARATMA. "Xitoyda o'qish" = "Xitoyda ta'lim" — bir ma'no, bir yozuv.

4. **Nomlar sifati.** Qisqa, aniq. Mavzular — kamida 2 so'z, agar mavzu aniq atama/texnologiya/atoqli ot bo'lmasa (Claude, React, AI, GPT-5.4). "Ish" mavzu sifatida — mumkin emas. "Restoranda ish" — mumkin emas (bu allaqachon job-mavjudot). Mavzular qiziqish sohalari haqida ("AI bilan ishlab chiqish", "sog'liq va sport", "munosabatlar").

5. **Mavzular iyerarxiyasi.** Yangi mavzu mavjud mavzuning (vault'da bor yoki shu sessiyada yaratilgan) xususiy holati bo'lsa — yangi mavzuda `typed_links: [{"field": "parent_theme", "target": "80_Themes/parent.md"}]`. Misol: "Claude" mavzusi paydo bo'ldi, "Ishlab chiqish" allaqachon bor → Claude'ning `parent_theme` `80_Themes/ishlab-chiqish.md`ga ko'rsatadi.

6. **Bog'lanishlarni taxmin qilma.** Mavjudotni qanday bog'lashni tushunmasang — uni aniq bo'lmagan bog'lanishlarsiz qoldir (faqat `owner` agar tegishli bo'lsa). Smart linker (alohida post-o'tish) o'tkazib yuborilganlarni topadi.

7. **O'ylab topma.** Faqat aniq aytilgan yoki muloqotdan to'g'ridan-to'g'ri kelib chiqadiganini kirit.

8. **new_facts `name`'dagi sub'ekt haqida bo'lishi kerak.** Muloqotda boshqa sub'ekt haqida faktlar topilsa — ular uchun **alohida entity yarat**. "Bakalavriat" entity'siga forel haqidagi faktlarni qo'yma. "Anya" entity'siga LegAI loyihasi haqidagi faktlarni qo'yma (Anya uchun — `works_at`; LegAI faktlari alohida project-entity'ga). O'zaro ifloslanish — buzilgan grafning asosiy sababi.

9. **new_facts sifati — aniqlik, bo'sh narsa emas.** Har bir fakt — alohida mazmunli birlik. "Komron ish loyihasi", "Muhim odam", "X bilan bog'liq kontekst" kabi umumiy nikoblar YOZMA. Bu sharmandalik. Agar entity bo'yicha muloqotda aniq faktlar bo'lmasa — `new_facts: []` qoldir yoki entity'ni `open_questions`'da follow-up uchun belgila.

# Sessiya bo'sh yoki texnik bo'lsa
Bo'sh massivlar qaytar `entities: []`, `thoughts: []`, etc.
