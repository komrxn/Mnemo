# Mnemo — AI 第二大脑

> 一个与你一起思考、记住一切、并将你的生活整理成知识图谱的 Telegram 机器人。

**其他语言：** [English](../README.md) · [Español](README_es.md) · [Português](README_pt.md) · [Français](README_fr.md)

---

Mnemo 是一个自托管的个人 AI 助手，它将你的 Telegram 对话转化为结构化的 Obsidian 笔记，并将这些笔记连接成知识图谱。你提到的每一个事实、项目、人物或想法都会被提取、链接并永久私密地存储在你自己的基础设施上。

```
你："今天和 LegAI 团队的 Anna 通了电话。
      我们约好 6 月 15 日前发布 MVP。"

Mnemo：已记录。已创建：Anna（人物）、LegAI（项目）、
       发布 MVP（任务，截止日期 2026-06-15）。已链接所有内容。
```

---

## 功能特性

- **永久记忆** — 每次会话都被提取为带有 frontmatter、标签和类型化 wikilinks 的结构化 Obsidian 笔记
- **知识图谱** — 笔记通过 `[[wikilinks]]` 自动连接，并索引到语义图谱（LightRAG）中
- **去重** — 模糊匹配防止重复笔记（"LegAI" 和 "legai-project" 解析为同一实体）
- **智能链接器** — 每次会话后，LLM 后处理程序提议类型化关系（`for_project`、`works_at`、`about_person` 等）
- **双向类型化链接** — 在任务上添加 `for_project` 会自动在项目上添加 `tasks: [...]`
- **内容地图** — `_meta/MOC_People.md`、`MOC_Projects.md` 等自动重新生成
- **多模态输入** — 文本、语音（Whisper 转录）和图片（GPT-4 Vision）
- **自定义个性** — 在引导过程中为助手命名并选择沟通风格
- **主动提醒** — 早晨摘要、每周反思、过期项目检查
- **Git 备份** — 每次笔记提交都有版本控制；`/undo` 撤销最后一次更改
- **完全私密** — 仅白名单用户，所有数据保留在你自己的 Docker + git 中

---

## 快速开始

### 1. 前置条件

- Docker + Docker Compose
- Telegram 机器人 token — 通过 [@BotFather](https://t.me/BotFather) 创建
- OpenAI API 密钥 — 在 [platform.openai.com](https://platform.openai.com/api-keys) 获取
- 你的 Telegram 用户 ID — 通过 [@userinfobot](https://t.me/userinfobot) 查找

### 2. 克隆并配置

```bash
git clone https://github.com/yourname/mnemo.git
cd mnemo
cp .env.example .env
```

编辑 `.env`，填写三个必填值：

```env
TELEGRAM_BOT_TOKEN=your_token_here
OPENAI_API_KEY=sk-...
ALLOWED_USER_IDS=123456789
TZ=Asia/Shanghai
```

### 3. 构建并运行

```bash
docker compose up -d
docker compose logs -f bot
```

### 4. 通过 Telegram 进行引导

1. 打开 Telegram，向你的机器人发送 `/start`
2. 给助手起个名字（例如 "小明"、"助手"、"Mnemo"）
3. 选择沟通风格
4. 告诉助手你的名字
5. 发送一段关于你自己的自由文本 — 项目、人物、目标、兴趣
6. 确认计划 → 你的知识库已激活

---

## Vault 结构

```
vault/
├── _meta/           # 系统文件（所有者、肖像、本体、MOC）
├── 00_Inbox/        # 未处理的捕获
├── 10_Daily/        # 每日会话笔记
├── 20_People/       # 你生活中的人
├── 30_Jobs/         # 公司、组织
├── 40_Projects/     # 工作和个人项目
├── 50_Tasks/        # 带截止日期的任务
├── 60_Thoughts/     # 想法、观察
├── 70_Memories/     # 个人事实、过去的事件
├── 80_Themes/       # 反复出现的主题（健康、价值观、爱好）
└── 90_Attachments/  # 语音消息、图片
```

---

## 机器人命令

| 命令 | 描述 |
|------|------|
| `/start` | 引导（首次运行）或状态检查 |
| `/save` | 立即关闭当前会话并提取笔记 |
| `/undo` | 撤销最后一次 vault 提交 |

---

## 连接到 AI 编程工具

Mnemo 将知识图谱作为 MCP 服务器暴露，让 **Claude Code、Cursor、Cline** 等工具在帮你写代码时可以查询你的第二大脑。

该包尚未发布到 PyPI，请从源码安装：

```bash
pip install mnemo-mcp
```

在 Claude Code 的 `~/.claude/claude_mcp_config.json` 中添加：

```json
{
  "mcpServers": {
    "mnemo-brain": {
      "command": "mnemo-mcp",
      "env": {
        "LIGHTRAG_BASE_URL": "http://localhost:9621",
        "LIGHTRAG_API_KEY": "<secrets/lightrag_api_key.txt 的内容>"
      }
    }
  }
}
```

完整安装指南：[English README](../README.md#connect-your-brain-to-ai-coding-tools)

---

## 安全与隐私

- **单用户设计** — `ALLOWED_USER_IDS` 白名单在中间件层面强制执行
- **数据属于你** — 除 OpenAI API 调用外，没有任何内容存储在你的基础设施之外
- **Git 保护** — 代码层面阻止 `--force`、`--no-verify`、`--hard` 标志

---

## 作者

由 **Komron Khakimov** 构建

- GitHub: [@komrxn](https://github.com/komrxn)
- Telegram: [@komrxn](https://t.me/komrxn)
- LinkedIn: [@komrxn](https://linkedin.com/in/komrxn)
- Instagram: [@komrxn](https://instagram.com/komrxn)
- 邮箱: [komronkhakimov17@gmail.com](mailto:komronkhakimov17@gmail.com)

---

## 许可证

MIT — 详见 [LICENSE](../LICENSE)。
