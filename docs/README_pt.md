# Mnemo — Segundo Cérebro com IA

> Um bot do Telegram que pensa com você, lembra de tudo e organiza sua vida em um grafo de conhecimento vivo.

**Outros idiomas:** [English](../README.md) · [中文](README_zh.md) · [Español](README_es.md) · [Français](README_fr.md)

---

Mnemo é um assistente de IA pessoal auto-hospedado que converte suas conversas do Telegram em notas estruturadas do Obsidian conectadas em um grafo de conhecimento. Cada fato, projeto, pessoa ou ideia que você mencionar é extraído, vinculado e armazenado de forma permanente e privada em sua própria infraestrutura.

```
Você: "Hoje tive uma ligação com a Anna da equipe LegAI.
       Combinamos de lançar o MVP até 15 de junho."

Mnemo: Anotado. Criado: Anna (Pessoas), LegAI (Projetos),
       lançar MVP (Tarefa, prazo 2026-06-15). Tudo vinculado.
```

---

## Funcionalidades

- **Memória eterna** — cada sessão é extraída como notas estruturadas do Obsidian com frontmatter, tags e wikilinks tipados
- **Grafo de conhecimento** — as notas são conectadas automaticamente via `[[wikilinks]]` e indexadas em um grafo semântico (LightRAG)
- **Deduplicação** — correspondência difusa evita notas duplicadas ("LegAI" e "legai-projeto" resolvem para a mesma entidade)
- **Vinculador inteligente** — após cada sessão, um LLM propõe relações tipadas (`for_project`, `works_at`, `about_person`, etc.)
- **Links bidirecionais tipados** — adicionar `for_project` em uma tarefa adiciona automaticamente `tasks: [...]` no projeto
- **Mapas de Conteúdo** — `_meta/MOC_People.md`, `MOC_Projects.md`, etc. são regenerados automaticamente
- **Entrada multimodal** — texto, voz (transcrição Whisper) e imagens (GPT-4 Vision)
- **Personalidade personalizada** — nomeie seu assistente e escolha o estilo de comunicação durante a integração
- **Lembretes proativos** — resumo matinal, reflexão semanal, verificação de projetos esquecidos
- **Vault com backup Git** — cada commit de nota é versionado; `/undo` reverte a última alteração
- **Totalmente privado** — apenas lista de permissões, todos os dados ficam em seu próprio Docker + git

---

## Início Rápido

### 1. Pré-requisitos

- Docker + Docker Compose
- Token de bot do Telegram — crie com [@BotFather](https://t.me/BotFather)
- Chave de API da OpenAI — obtenha em [platform.openai.com](https://platform.openai.com/api-keys)
- Seu ID de usuário do Telegram — encontre com [@userinfobot](https://t.me/userinfobot)

### 2. Clonar e configurar

```bash
git clone https://github.com/yourname/mnemo.git
cd mnemo
cp .env.example .env
```

Edite `.env` — preencha os três valores obrigatórios:

```env
TELEGRAM_BOT_TOKEN=seu_token_aqui
OPENAI_API_KEY=sk-...
ALLOWED_USER_IDS=123456789
TZ=America/Sao_Paulo
```

### 3. Construir e executar

```bash
docker compose up -d
docker compose logs -f bot
```

### 4. Integração pelo Telegram

1. Abra o Telegram, envie `/start` para o seu bot
2. Dê um nome ao assistente (ex: "Max", "Mia", "Mnemo")
3. Escolha um estilo de comunicação
4. Diga ao assistente seu nome
5. Envie um texto livre sobre você — projetos, pessoas, objetivos, interesses
6. Confirme o plano → seu vault está ativo

---

## Estrutura do Vault

```
vault/
├── _meta/           # arquivos do sistema (proprietário, retrato, ontologia, MOC)
├── 00_Inbox/        # capturas não processadas
├── 10_Daily/        # notas de sessão diária
├── 20_People/       # pessoas na sua vida
├── 30_Jobs/         # empresas, organizações
├── 40_Projects/     # projetos de trabalho e pessoais
├── 50_Tasks/        # tarefas com prazos
├── 60_Thoughts/     # ideias, observações
├── 70_Memories/     # fatos pessoais, eventos passados
├── 80_Themes/       # temas recorrentes (saúde, valores, hobbies)
└── 90_Attachments/  # mensagens de voz, imagens
```

---

## Comandos do Bot

| Comando | Descrição |
|---------|-----------|
| `/start` | Integração (primeira vez) ou verificação de status |
| `/save` | Fechar sessão atual e extrair notas imediatamente |
| `/undo` | Reverter o último commit do vault |

---

## Segurança e Privacidade

- **Design de usuário único** — lista de permissões `ALLOWED_USER_IDS` aplicada no nível do middleware
- **Seus dados são seus** — nada é armazenado fora da sua infraestrutura, exceto chamadas à API da OpenAI
- **Proteções Git** — os flags `--force`, `--no-verify`, `--hard` são bloqueados no nível do código

---

## Autor

Criado por **Komron Khakimov**

- GitHub: [@komrxn](https://github.com/komrxn)
- Telegram: [@komrxn](https://t.me/komrxn)
- LinkedIn: [@komrxn](https://linkedin.com/in/komrxn)
- Instagram: [@komrxn](https://instagram.com/komrxn)
- Email: [komronkhakimov17@gmail.com](mailto:komronkhakimov17@gmail.com)

---

## Licença

MIT — veja [LICENSE](../LICENSE).
