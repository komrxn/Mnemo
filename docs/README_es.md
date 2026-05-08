# Mnemo — Segundo Cerebro con IA

> Un bot de Telegram que piensa contigo, recuerda todo y organiza tu vida en un grafo de conocimiento vivo.

**Otros idiomas:** [English](../README.md) · [中文](README_zh.md) · [Português](README_pt.md) · [Français](README_fr.md)

---

Mnemo es un asistente de IA personal autoalojado que convierte tus conversaciones de Telegram en notas estructuradas de Obsidian conectadas en un grafo de conocimiento. Cada hecho, proyecto, persona o idea que menciones se extrae, vincula y almacena de forma permanente y privada en tu propia infraestructura.

```
Tú: "Hoy tuve una llamada con Anna del equipo de LegAI.
     Acordamos lanzar el MVP antes del 15 de junio."

Mnemo: Anotado. Creado: Anna (Personas), LegAI (Proyectos),
       lanzar MVP (Tarea, vence 2026-06-15). Todo vinculado.
```

---

## Características

- **Memoria eterna** — cada sesión se extrae como notas estructuradas de Obsidian con frontmatter, etiquetas y wikilinks tipados
- **Grafo de conocimiento** — las notas se conectan automáticamente mediante `[[wikilinks]]` e se indexan en un grafo semántico (LightRAG)
- **Deduplicación** — la coincidencia difusa previene notas duplicadas ("LegAI" y "legai-proyecto" se resuelven a la misma entidad)
- **Enlazador inteligente** — después de cada sesión, un pase posterior de LLM propone relaciones tipadas (`for_project`, `works_at`, `about_person`, etc.)
- **Vínculos bidireccionales tipados** — agregar `for_project` en una tarea agrega automáticamente `tasks: [...]` en el proyecto
- **Mapas de Contenido** — `_meta/MOC_People.md`, `MOC_Projects.md`, etc. se regeneran automáticamente
- **Entrada multimodal** — texto, voz (transcripción Whisper) e imágenes (GPT-4 Vision)
- **Personalidad personalizada** — nombra a tu asistente y elige su estilo de comunicación durante la incorporación
- **Recordatorios proactivos** — resumen matutino, reflexión semanal, verificación de proyectos olvidados
- **Vault respaldado por Git** — cada commit de nota tiene versión; `/undo` revierte el último cambio
- **Completamente privado** — solo lista blanca, todos los datos permanecen en tu propio Docker + git

---

## Inicio rápido

### 1. Requisitos previos

- Docker + Docker Compose
- Token de bot de Telegram — créalo con [@BotFather](https://t.me/BotFather)
- Clave de API de OpenAI — obtenla en [platform.openai.com](https://platform.openai.com/api-keys)
- Tu ID de usuario de Telegram — encuéntralo con [@userinfobot](https://t.me/userinfobot)

### 2. Clonar y configurar

```bash
git clone https://github.com/yourname/mnemo.git
cd mnemo
cp .env.example .env
```

Edita `.env` — completa los tres valores requeridos:

```env
TELEGRAM_BOT_TOKEN=tu_token_aqui
OPENAI_API_KEY=sk-...
ALLOWED_USER_IDS=123456789
TZ=America/Mexico_City
```

### 3. Construir y ejecutar

```bash
docker compose up -d
docker compose logs -f bot
```

### 4. Incorporación por Telegram

1. Abre Telegram, envía `/start` a tu bot
2. Dale un nombre al asistente (p. ej. "Max", "Mía", "Mnemo")
3. Elige un estilo de comunicación
4. Dile al asistente tu nombre
5. Envía un texto libre sobre ti mismo — proyectos, personas, objetivos, intereses
6. Confirma el plan → tu vault está activo

---

## Estructura del Vault

```
vault/
├── _meta/           # archivos del sistema (propietario, retrato, ontología, MOC)
├── 00_Inbox/        # capturas sin procesar
├── 10_Daily/        # notas de sesión diaria
├── 20_People/       # personas en tu vida
├── 30_Jobs/         # empresas, organizaciones
├── 40_Projects/     # proyectos de trabajo y personales
├── 50_Tasks/        # tareas con fechas límite
├── 60_Thoughts/     # ideas, observaciones
├── 70_Memories/     # hechos personales, eventos pasados
├── 80_Themes/       # temas recurrentes (salud, valores, hobbies)
└── 90_Attachments/  # mensajes de voz, imágenes
```

---

## Comandos del Bot

| Comando | Descripción |
|---------|-------------|
| `/start` | Incorporación (primera vez) o verificación de estado |
| `/save` | Cerrar sesión actual y extraer notas inmediatamente |
| `/undo` | Revertir el último commit del vault |

---

## Conectar a Herramientas de IA para Programar

Mnemo expone su grafo de conocimiento como servidor MCP, permitiendo que **Claude Code, Cursor, Cline** y herramientas similares consulten tu segundo cerebro mientras programas.

El paquete no está en PyPI aún — instálalo desde el código fuente:

```bash
pip install mnemo-mcp
```

Añade esto a `~/.claude/claude_mcp_config.json` en Claude Code:

```json
{
  "mcpServers": {
    "mnemo-brain": {
      "command": "mnemo-mcp",
      "env": {
        "LIGHTRAG_BASE_URL": "http://localhost:9621",
        "LIGHTRAG_API_KEY": "<contenido de secrets/lightrag_api_key.txt>"
      }
    }
  }
}
```

Guía completa: [README en inglés](../README.md#connect-your-brain-to-ai-coding-tools)

---

## Seguridad y Privacidad

- **Diseño de usuario único** — lista blanca `ALLOWED_USER_IDS` aplicada a nivel de middleware
- **Tus datos son tuyos** — nada se almacena fuera de tu infraestructura excepto las llamadas a la API de OpenAI
- **Protecciones Git** — los indicadores `--force`, `--no-verify`, `--hard` están bloqueados a nivel de código

---

## Autor

Creado por **Komron Khakimov**

- GitHub: [@komrxn](https://github.com/komrxn)
- Telegram: [@komrxn](https://t.me/komrxn)
- LinkedIn: [@komrxn](https://linkedin.com/in/komrxn)
- Instagram: [@komrxn](https://instagram.com/komrxn)
- Email: [komronkhakimov17@gmail.com](mailto:komronkhakimov17@gmail.com)

---

## Licencia

MIT — consulta [LICENSE](../LICENSE).
